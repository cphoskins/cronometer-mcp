"""Cronometer API client using the GWT-RPC protocol.

Authenticates via the web login flow, then exports nutrition data
(servings, daily summaries, exercises, biometrics, notes) as CSV.

NOTE: Cronometer has no public API. This client uses the same GWT-RPC
protocol as the web app. The GWT magic values (permutation hash, header)
may change when Cronometer deploys new builds. See README for details.
"""

import csv
import io
import json
import logging
import os
import pickle
import re
from datetime import date
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# URLs
LOGIN_HTML_URL = "https://cronometer.com/login/"
LOGIN_API_URL = "https://cronometer.com/login"
GWT_BASE_URL = "https://cronometer.com/cronometer/app"
EXPORT_URL = "https://cronometer.com/export"
GWT_NOCACHE_JS_URL = "https://cronometer.com/cronometer/cronometer.nocache.js"
GWT_CACHE_JS_URL = "https://cronometer.com/cronometer/{permutation}.cache.js"

# NCCDB "universal" measure_id that works for any food in updateDiary.
# CRDB foods have food-specific measure_ids in their getFood response,
# but those IDs cause "ghost entries" (counted but invisible in diary).
# Using an NCCDB measure_id (here: eggs' "mL chopped" measure) with the
# correct weight_grams produces working entries for ALL food sources.
UNIVERSAL_MEASURE_ID = 124399

# GWT magic values — used as fallbacks if auto-discovery fails.
DEFAULT_GWT_CONTENT_TYPE = "text/x-gwt-rpc; charset=UTF-8"
DEFAULT_GWT_MODULE_BASE = "https://cronometer.com/cronometer/"
DEFAULT_GWT_PERMUTATION = "CBC38FBB0A1527BD5E68722DD9DABD27"
DEFAULT_GWT_HEADER = "76FC4464E20E53D16663AC9A96A486B3"

GWT_AUTHENTICATE = (
    "7|0|5|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "authenticate|java.lang.Integer/3438268394|"
    "1|2|3|4|1|5|5|-300|"
)

GWT_GENERATE_AUTH_TOKEN = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "generateAuthorizationToken|java.lang.String/2004016611|"
    "I|com.cronometer.shared.user.AuthScope/2065601159|"
    "{nonce}|1|2|3|4|4|5|6|6|7|8|{user_id}|3600|7|2|"
)

GWT_FIND_FOODS = (
    "7|0|12|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "findFoods|java.lang.String/2004016611|"
    "I|[Lcom.cronometer.shared.foods.FoodSource;/3597302983|"
    "com.cronometer.shared.foods.FoodSearchTabSelection/1776179901|"
    "Z|{nonce}|{query}|"
    "com.cronometer.shared.foods.FoodSource/4236433762|"
    "1|2|3|4|8|5|5|6|7|6|5|8|9|10|11|{max_results}|7|1|12|0|0|0|8|0|0|"
)

GWT_UPDATE_DIARY = (
    "7|0|12|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "updateDiary|java.lang.String/2004016611|"
    "I|java.util.List|{nonce}|"
    "java.util.Collections$SingletonList/1586180994|"
    "com.cronometer.shared.entries.changes.AddEntryChange/3949104564|"
    "com.cronometer.shared.entries.models.Serving/2553599101|"
    "com.cronometer.shared.entries.models.Day/782579793|"
    "1|2|3|4|3|5|6|7|8|{user_id}|9|10|1|1|11|12|"
    "{day}|{month}|{year}|{quantity}|{diary_group}|0|{measure_id}|0|0|"
    "{weight_grams}|{food_source_id}|A|{food_id}|0|0|"
)

GWT_REMOVE_SERVING = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "removeServing|java.lang.String/2004016611|"
    "J|I|{nonce}|1|2|3|4|3|5|6|7|8|{serving_id}|{user_id}|"
)

GWT_GET_FOOD = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getFood|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{food_source_id}|"
)

EXPORT_TYPES = {
    "servings": "servings",
    "daily_summary": "dailySummary",
    "exercises": "exercises",
    "biometrics": "biometrics",
    "notes": "notes",
}


class CronometerClient:
    """Client for the Cronometer GWT-RPC API.

    Credentials are read from CRONOMETER_USERNAME and CRONOMETER_PASSWORD
    environment variables, or can be passed directly.
    """

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        gwt_permutation: str | None = None,
        gwt_header: str | None = None,
    ):
        self.username = username or os.environ.get("CRONOMETER_USERNAME", "")
        self.password = password or os.environ.get("CRONOMETER_PASSWORD", "")
        self.gwt_permutation = gwt_permutation or DEFAULT_GWT_PERMUTATION
        self.gwt_header = gwt_header or DEFAULT_GWT_HEADER

        if not self.username or not self.password:
            raise ValueError(
                "Cronometer credentials required. Set CRONOMETER_USERNAME and "
                "CRONOMETER_PASSWORD environment variables, or pass them directly."
            )

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "cronometer-mcp/0.1"})
        self.nonce: str | None = None
        self.user_id: str | None = None
        self._authenticated = False
        self._cookie_path = Path(
            os.environ.get("CRONOMETER_DATA_DIR", Path.home() / ".local" / "share" / "cronometer-mcp")
        ) / ".session_cookies"

    def _get_anticsrf(self) -> str:
        """Step 1: Fetch the login page and extract the anti-CSRF token."""
        resp = self.session.get(LOGIN_HTML_URL)
        resp.raise_for_status()
        match = re.search(r'name="anticsrf"\s+value="([^"]+)"', resp.text)
        if not match:
            raise RuntimeError("Could not find anti-CSRF token on login page")
        return match.group(1)

    def _login(self, anticsrf: str) -> None:
        """Step 2: POST credentials to the login endpoint."""
        resp = self.session.post(
            LOGIN_API_URL,
            data={
                "anticsrf": anticsrf,
                "username": self.username,
                "password": self.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("error"):
            raise RuntimeError(f"Login failed: {result['error']}")
        if not (result.get("success") or result.get("redirect")):
            raise RuntimeError(f"Login failed: unexpected response {result}")

        # Extract sesnonce cookie
        self.nonce = self.session.cookies.get("sesnonce")
        if not self.nonce:
            raise RuntimeError("Login succeeded but no sesnonce cookie received")
        logger.info("Login successful")

    def _discover_gwt_hashes(self) -> None:
        """Discover current GWT permutation and header hashes.

        Fetches the GWT bootstrap JS to get the permutation hash, then
        fetches the compiled cache.js to extract the serialization policy
        hash (GWT header) for the 'app' endpoint.

        Falls back to the hardcoded defaults if discovery fails.
        """
        try:
            # Step 1: Get permutation hash from nocache.js
            resp = self.session.get(GWT_NOCACHE_JS_URL)
            resp.raise_for_status()
            perm_match = re.search(r"='([A-F0-9]{32})'", resp.text)
            if not perm_match:
                logger.warning("Could not extract permutation hash; using default")
                return
            permutation = perm_match.group(1)

            # Step 2: Get GWT header from the compiled cache.js
            cache_url = GWT_CACHE_JS_URL.replace("{permutation}", permutation)
            resp = self.session.get(cache_url)
            resp.raise_for_status()
            # The 'app' endpoint hash appears as: 'app','<32-HEX>'
            header_match = re.search(
                r"'app','([A-F0-9]{32})'", resp.text
            )
            if not header_match:
                logger.warning(
                    "Could not extract GWT header from cache.js; using default"
                )
                # Still update permutation even if header extraction fails
                self.gwt_permutation = permutation
                return

            self.gwt_permutation = permutation
            self.gwt_header = header_match.group(1)
            logger.info(
                "GWT hashes discovered: permutation=%s, header=%s",
                self.gwt_permutation,
                self.gwt_header,
            )
        except Exception:
            logger.warning(
                "GWT hash discovery failed; using defaults", exc_info=True
            )

    def _gwt_authenticate(self) -> None:
        """Step 3: GWT authentication to get user ID."""
        body = GWT_AUTHENTICATE.replace("{gwt_header}", self.gwt_header)
        resp = self.session.post(
            GWT_BASE_URL,
            data=body,
            headers={
                "content-type": DEFAULT_GWT_CONTENT_TYPE,
                "x-gwt-module-base": DEFAULT_GWT_MODULE_BASE,
                "x-gwt-permutation": self.gwt_permutation,
            },
        )
        resp.raise_for_status()

        match = re.search(r"OK\[(\d+),", resp.text)
        if not match:
            raise RuntimeError(
                f"GWT authenticate failed to extract user ID. "
                f"Response: {resp.text[:200]}"
            )
        self.user_id = match.group(1)

        # Update nonce from cookies
        new_nonce = self.session.cookies.get("sesnonce")
        if new_nonce:
            self.nonce = new_nonce
        logger.info("GWT auth successful, user_id=%s", self.user_id)

    def _generate_auth_token(self) -> str:
        """Step 4: Generate a short-lived auth token for export requests."""
        body = GWT_GENERATE_AUTH_TOKEN.replace("{gwt_header}", self.gwt_header)
        body = body.replace("{nonce}", self.nonce or "")
        body = body.replace("{user_id}", self.user_id or "")

        resp = self.session.post(
            GWT_BASE_URL,
            data=body,
            headers={
                "content-type": DEFAULT_GWT_CONTENT_TYPE,
                "x-gwt-module-base": DEFAULT_GWT_MODULE_BASE,
                "x-gwt-permutation": self.gwt_permutation,
            },
        )
        resp.raise_for_status()

        match = re.search(r'"([^"]+)"', resp.text)
        if not match:
            raise RuntimeError(
                f"Failed to extract auth token. Response: {resp.text[:200]}"
            )
        token = match.group(1)
        logger.info("Auth token generated")
        return token

    def _save_session(self) -> None:
        """Persist session cookies and auth state to disk."""
        self._cookie_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "cookies": self.session.cookies.get_dict(),
            "nonce": self.nonce,
            "user_id": self.user_id,
            "gwt_permutation": self.gwt_permutation,
            "gwt_header": self.gwt_header,
        }
        self._cookie_path.write_bytes(pickle.dumps(data))
        logger.debug("Session saved to %s", self._cookie_path)

    def _restore_session(self) -> bool:
        """Try to restore a previous session from disk. Returns True if valid."""
        if not self._cookie_path.exists():
            return False
        try:
            data = pickle.loads(self._cookie_path.read_bytes())
            for k, v in data["cookies"].items():
                self.session.cookies.set(k, v)
            self.nonce = data["nonce"]
            self.user_id = data["user_id"]
            self.gwt_permutation = data.get("gwt_permutation", self.gwt_permutation)
            self.gwt_header = data.get("gwt_header", self.gwt_header)
            # Validate with a lightweight GWT call (generateAuthToken)
            self._discover_gwt_hashes()
            token = self._generate_auth_token()
            if token:
                logger.info("Restored saved session")
                return True
        except Exception:
            pass
        self._cookie_path.unlink(missing_ok=True)
        return False

    def authenticate(self) -> None:
        """Full authentication flow: discover hashes, login, GWT auth."""
        if self._authenticated:
            return
        if self._restore_session():
            self._authenticated = True
            return
        self._discover_gwt_hashes()
        anticsrf = self._get_anticsrf()
        self._login(anticsrf)
        self._gwt_authenticate()
        self._authenticated = True
        self._save_session()

    def export_raw(
        self,
        export_type: str,
        start: date | None = None,
        end: date | None = None,
    ) -> str:
        """Export raw CSV data from Cronometer.

        Args:
            export_type: One of 'servings', 'daily_summary', 'exercises',
                        'biometrics', 'notes'.
            start: Start date (defaults to today).
            end: End date (defaults to today).

        Returns:
            Raw CSV text.
        """
        self.authenticate()
        token = self._generate_auth_token()

        if start is None:
            start = date.today()
        if end is None:
            end = date.today()

        generate_value = EXPORT_TYPES.get(export_type, export_type)

        resp = self.session.get(
            EXPORT_URL,
            params={
                "nonce": token,
                "generate": generate_value,
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
            },
            headers={
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
            },
        )
        resp.raise_for_status()
        return resp.text

    def export_parsed(
        self,
        export_type: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        """Export and parse CSV data into a list of dicts.

        Args:
            export_type: One of 'servings', 'daily_summary', 'exercises',
                        'biometrics', 'notes'.
            start: Start date (defaults to today).
            end: End date (defaults to today).

        Returns:
            List of dicts, one per CSV row.
        """
        raw = self.export_raw(export_type, start, end)
        reader = csv.DictReader(io.StringIO(raw))
        return list(reader)

    def _gwt_post(self, body: str) -> str:
        """POST a GWT-RPC payload and return the raw response text.

        Raises RuntimeError if the response does not start with '//OK'.
        """
        resp = self.session.post(
            GWT_BASE_URL,
            data=body,
            headers={
                "content-type": DEFAULT_GWT_CONTENT_TYPE,
                "x-gwt-module-base": DEFAULT_GWT_MODULE_BASE,
                "x-gwt-permutation": self.gwt_permutation,
            },
        )
        resp.raise_for_status()
        if not resp.text.startswith("//OK"):
            raise RuntimeError(
                f"GWT-RPC call failed. Response: {resp.text[:300]}"
            )
        return resp.text

    @staticmethod
    def _parse_find_foods(raw: str) -> list[dict]:
        """Parse a GWT-RPC findFoods response into structured food records.

        The GWT-RPC wire format encodes all strings in a string table at the
        end of the response; the data section contains integer tokens that
        reference that table by 1-based index.  Each SearchHit occupies a
        fixed-width slot in the token stream:

            score, flags, name_ref, food_id, measure_desc_ref, locale_ref,
            food_source_id, popularity, keywords_ref, <SearchHit type ref>

        We locate every occurrence of the SearchHit type-index value in the
        token list and read the nine tokens that precede it.

        Args:
            raw: Raw ``//OK[...]`` GWT-RPC response string.

        Returns:
            List of dicts with keys ``food_id``, ``food_source_id``, ``name``,
            ``measure_desc``, and ``score``.  Returns an empty list when the
            response contains no results or cannot be parsed.

        Raises:
            ValueError: If the response does not match the expected GWT-RPC
                        envelope format.

        Example::

            results = CronometerClient._parse_find_foods(raw_gwt_response)
            # [{"food_id": 1072102, "food_source_id": 464674,
            #   "name": "Egg, whole, cooked, hard-boiled",
            #   "measure_desc": "1 large - 50g", "score": 100}, ...]
        """
        raw = raw.strip()
        if not raw.startswith("//OK["):
            raise ValueError(
                f"Unexpected GWT-RPC response format: {raw[:100]!r}"
            )

        # The response ends with ],0,7] — the ']' closes the embedded string
        # table array, then ',0,7]' closes the outer //OK[ envelope.
        closing = ",0,7]"
        if not raw.endswith(closing):
            raise ValueError(
                f"Response does not end with expected closing ',0,7]': "
                f"{raw[-40:]!r}"
            )

        # Locate the string table JSON array by scanning backwards from the
        # ']' that closes it (immediately before ',0,7]').
        # Must skip brackets inside quoted strings (e.g. Java array type
        # descriptors like "[Lcom.cronometer...").
        st_close = len(raw) - len(closing) - 1  # index of ']' closing str table
        depth = 1
        pos = st_close - 1
        in_string = False
        while pos >= 0 and depth > 0:
            ch = raw[pos]
            if ch == '"' and (pos == 0 or raw[pos - 1] != "\\"):
                in_string = not in_string
            elif not in_string:
                if ch == "]":
                    depth += 1
                elif ch == "[":
                    depth -= 1
            pos -= 1
        st_open = pos + 1  # index of '[' opening the string table

        string_table: list[str] = json.loads(raw[st_open : st_close + 1])

        # Find the 1-based indices of key classes in the string table.
        searchhit_type_idx: int | None = None
        foodsource_type_idx: int | None = None
        foodtype_type_idx: int | None = None
        for idx, entry in enumerate(string_table):
            # Skip Java array type descriptors (e.g. "[Lcom.cronometer...;/...")
            # — we only want the actual class, not the array-of-class type.
            if entry.startswith("["):
                continue
            if "SearchHit" in entry and searchhit_type_idx is None:
                searchhit_type_idx = idx + 1  # GWT uses 1-based references
            elif "FoodSource" in entry and foodsource_type_idx is None:
                foodsource_type_idx = idx + 1
            elif "FoodType" in entry and foodtype_type_idx is None:
                foodtype_type_idx = idx + 1

        if searchhit_type_idx is None:
            # No SearchHit class in the string table → zero results.
            return []

        # Helper: resolve a 1-based string table reference to its string value.
        def _resolve(ref: int) -> str | None:
            if 1 <= ref <= len(string_table):
                return string_table[ref - 1]
            return None

        # Tokenise the data section (between '//OK[' and the string table).
        # All tokens in a findFoods data section are plain integers.
        # '//OK[' is 5 characters, so data starts at index 5.
        data_section = raw[5:st_open].rstrip(",")
        if not data_section:
            return []

        tokens: list[int] = []
        for part in data_section.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                tokens.append(int(part))
            except ValueError:
                # Unexpected non-integer token; insert a sentinel that will
                # never match a valid type index so scanning continues safely.
                tokens.append(-(10 ** 9))

        # Scan for SearchHit type-index occurrences.
        # Each hit has exactly 9 fields before the type ref:
        #
        #   i-9  score          (relevance, e.g. 1918)
        #   i-8  flags          (always 0)
        #   i-7  name_ref       (1-based string table index)
        #   i-6  food_id        (raw integer, e.g. 1072101)
        #   i-5  measure_desc_ref (1-based string table index)
        #   i-4  locale_ref     (1-based string table index, e.g. → "en")
        #   i-3  food_source_id (raw integer, e.g. 464674)
        #   i-2  popularity     (usage count, not returned to caller)
        #   i-1  keywords_ref   (1-based string table index, not returned)
        #   i    <SearchHit type ref>
        #
        # FoodSource/FoodType enum data appears in separate token blocks
        # between SearchHit records, NOT immediately after the type ref.
        results: list[dict] = []
        for i, token in enumerate(tokens):
            if token != searchhit_type_idx:
                continue
            if i < 9:
                continue

            score = tokens[i - 9]
            # tokens[i - 8] is flags (always 0; not validated here)
            name_ref = tokens[i - 7]
            food_id = tokens[i - 6]
            measure_desc_ref = tokens[i - 5]
            # tokens[i - 4] is locale_ref (not returned)
            food_source_id = tokens[i - 3]
            # tokens[i - 2] is popularity (not returned)
            # tokens[i - 1] is keywords_ref (not returned)

            name = _resolve(name_ref)
            if name is None:
                continue
            # Skip refs that resolve to GWT class descriptors (e.g.
            # "com.cronometer...", "java.util...", "[Lcom...") — indicates a
            # false positive where a stray integer matched the type index.
            if "/" in name and ("." in name.split("/")[0]):
                continue

            measure_desc = _resolve(measure_desc_ref) or ""

            results.append(
                {
                    "food_id": food_id,
                    "food_source_id": food_source_id,
                    "name": name,
                    "measure_desc": measure_desc,
                    "score": score,
                }
            )

        return results

    def find_foods(self, query: str, max_results: int = 50) -> list[dict]:
        """Search Cronometer's food database.

        Args:
            query: Search term.  The Cronometer web app uppercases queries
                   before sending; this method does the same automatically.
            max_results: Maximum number of results to return (default 50).

        Returns:
            List of dicts, each with keys:

            - ``food_id`` (int): Numeric food identifier.
            - ``food_source_id`` (int): Source database identifier (e.g. USDA).
            - ``name`` (str): Food name as stored in Cronometer.
            - ``measure_desc`` (str): Default measure description
              (e.g. ``"1 large - 50g"``).
            - ``score`` (int): Relevance score from the search engine.
        """
        self.authenticate()
        body = (
            GWT_FIND_FOODS
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{query}", query.upper())
            .replace("{max_results}", str(max_results))
        )
        raw = self._gwt_post(body)
        return self._parse_find_foods(raw)

    def get_food(self, food_source_id: int) -> dict:
        """Get detailed food information including available measures.

        Args:
            food_source_id: Food source ID from find_foods results.

        Returns:
            Dict with keys:

            - ``food_source_id`` (int): Echo of the input.
            - ``raw_response`` (str): Raw GWT-RPC response for debugging.
            - ``measures`` (list[dict]): Available serving measures, each with:
              - ``measure_id`` (int): Numeric ID needed by add_serving.
              - ``description`` (str): Human-readable description
                (e.g. ``"1 large - 50g"``).
              - ``weight_grams`` (float): Weight in grams for this measure.
        """
        self.authenticate()
        body = (
            GWT_GET_FOOD
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{food_source_id}", str(food_source_id))
        )
        raw = self._gwt_post(body)
        return self._parse_get_food(raw, food_source_id)

    @staticmethod
    def _parse_get_food(raw: str, food_source_id: int) -> dict:
        """Parse a getFood GWT-RPC response to extract measure information.

        The response contains food metadata and a list of Measure objects.
        Each Measure has fields (reading backwards from the Measure type ref):

            i-6  description_ref (1-based string table index)
            i-5  flags (0)
            i-4  measure_id (integer, the key value needed by add_serving)
            i-3  food_source_id
            i-2  flags (0)
            i-1  quantity (1.0)
            i    <Measure type ref>

        Weight in grams is a float that appears earlier in the token stream,
        before the Measure$Type ref/back-ref for each measure.
        """
        result: dict = {
            "food_source_id": food_source_id,
            "measures": [],
        }

        if not raw.startswith("//OK[") or not raw.endswith(",0,7]"):
            return result

        # Extract string table
        closing = ",0,7]"
        st_close = len(raw) - len(closing) - 1
        depth, pos, in_str = 1, st_close - 1, False
        while pos >= 0 and depth > 0:
            ch = raw[pos]
            if ch == '"' and (pos == 0 or raw[pos - 1] != "\\"):
                in_str = not in_str
            elif not in_str:
                if ch == "]":
                    depth += 1
                elif ch == "[":
                    depth -= 1
            pos -= 1
        st_open = pos + 1
        string_table: list[str] = json.loads(raw[st_open : st_close + 1])

        def _resolve(ref: int) -> str | None:
            if 1 <= ref <= len(string_table):
                return string_table[ref - 1]
            return None

        # Find Measure class index in string table
        measure_type_idx: int | None = None
        for idx, entry in enumerate(string_table):
            if entry.startswith("["):
                continue
            if "Measure/" in entry and "Measure$" not in entry and "Derived" not in entry:
                if measure_type_idx is None:
                    measure_type_idx = idx + 1

        if measure_type_idx is None:
            return result

        # Tokenize data section — preserve floats
        data_section = raw[5:st_open].rstrip(",")
        if not data_section:
            return result

        tokens: list = []
        for part in data_section.split(","):
            part = part.strip()
            if not part:
                continue
            # Handle quoted strings (e.g. serving_id in Food metadata)
            if part.startswith('"') and part.endswith('"'):
                tokens.append(part)
                continue
            try:
                tokens.append(float(part) if "." in part else int(part))
            except ValueError:
                tokens.append(None)

        # Scan for Measure type-index occurrences and extract fields.
        measures = []
        for i, token in enumerate(tokens):
            if token != measure_type_idx:
                continue
            if i < 6:
                continue

            measure_id_val = tokens[i - 4]

            if not isinstance(measure_id_val, int):
                continue

            # Description ref is at i-6 for standard Measure layout, but
            # CRDB foods with a boxed Double field shift it to i-7 or i-8.
            # Scan multiple offsets to find a valid description string.
            description = ""
            for offset in (6, 7, 8):
                if i < offset:
                    continue
                ref = tokens[i - offset]
                if isinstance(ref, int) and 1 <= ref <= len(string_table):
                    candidate = string_table[ref - 1]
                    if (candidate
                            and not candidate.startswith("com.")
                            and not candidate.startswith("java.")
                            and not candidate.startswith("[")):
                        description = candidate
                        break

            # Find weight_grams: it's the float that appears before the
            # Measure$Type ref/back-ref, which is at i-7 or i-8.
            # Scan backwards from i-7 to find the first float.
            weight_grams = 0.0
            for j in range(i - 7, max(i - 12, -1), -1):
                if isinstance(tokens[j], float):
                    weight_grams = tokens[j]
                    break

            measures.append({
                "measure_id": measure_id_val,
                "description": description or "",
                "weight_grams": round(weight_grams, 2),
            })

        result["measures"] = measures
        return result

    def add_serving(
        self,
        food_id: int,
        food_source_id: int,
        measure_id: int,
        quantity: float,
        weight_grams: float,
        day: date,
        diary_group: int = 1,
    ) -> dict:
        """Add a food serving to the Cronometer diary.

        Args:
            food_id: Numeric food ID from Cronometer's food database.
            food_source_id: Food source ID (identifies the database the food
                           comes from, e.g. USDA, custom).
            measure_id: Measure/unit ID. For NCCDB foods, use the measure_id
                        from getFood. For CRDB/custom foods, use
                        UNIVERSAL_MEASURE_ID (124399) to avoid ghost entries.
                        Pass 0 to auto-select UNIVERSAL_MEASURE_ID.
            quantity: Serving quantity. When using UNIVERSAL_MEASURE_ID, set
                      this equal to weight_grams (since the measure is g-based).
            weight_grams: Weight of the serving in grams.
            day: Calendar date to log the entry against.
            diary_group: Meal slot — 1=Breakfast, 2=Lunch, 3=Dinner, 4=Snacks.

        Returns:
            Dict with keys:
                - serving_id (str): Opaque diary entry identifier (e.g. "D80lp$").
                - food_id (int): Echo of the food_id argument.
                - food_source_id (int): Echo of the food_source_id argument.
        """
        self.authenticate()

        if measure_id == 0:
            measure_id = UNIVERSAL_MEASURE_ID

        # Cronometer sends integer quantities without a decimal point
        quantity_str = str(int(quantity)) if quantity == int(quantity) else str(quantity)
        weight_str = str(int(weight_grams)) if weight_grams == int(weight_grams) else str(weight_grams)

        body = (
            GWT_UPDATE_DIARY
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{day}", str(day.day))
            .replace("{month}", str(day.month))
            .replace("{year}", str(day.year))
            .replace("{quantity}", quantity_str)
            .replace("{diary_group}", str(diary_group))
            .replace("{measure_id}", str(measure_id))
            .replace("{weight_grams}", weight_str)
            .replace("{food_source_id}", str(food_source_id))
            .replace("{food_id}", str(food_id))
        )

        raw = self._gwt_post(body)

        # Response format (example):
        # //OK[0,0,1072101,"D80lp$",464674,50.0,2107848,0,65541,0,1,1,2026,3,4,
        #      4,3,2,1,1,["java.util.ArrayList/...","com.cronometer..."],0,7]
        # Positional layout (0-indexed from the inner array start):
        #   index 3 → serving_id (quoted string)
        #   index 2 → food_id
        #   index 4 → food_source_id
        inner_match = re.search(r"//OK\[(.+),\d+,7\]$", raw, re.DOTALL)
        if not inner_match:
            raise RuntimeError(
                f"Unexpected updateDiary response format: {raw[:300]}"
            )

        inner = inner_match.group(1)
        # The response layout is:
        #   0,0,{food_id},"{serving_id}",{food_source_id},{weight},... ,[string_table],0,7
        # Match the first five meaningful fields directly from the full inner string.
        fields_match = re.match(
            r"\d+,\d+,(\d+),\"([^\"]+)\",(\d+),",
            inner,
        )
        if not fields_match:
            raise RuntimeError(
                f"Could not parse updateDiary response fields: {inner[:200]}"
            )

        return {
            "serving_id": fields_match.group(2),
            "food_id": int(fields_match.group(1)),
            "food_source_id": int(fields_match.group(3)),
        }

    def remove_serving(self, serving_id: str) -> bool:
        """Remove a serving entry from the Cronometer diary.

        Args:
            serving_id: Opaque diary entry identifier returned by add_serving
                        (e.g. "D80lp$").

        Returns:
            True on success.

        Raises:
            RuntimeError: If the server returns an error or an unexpected
                          response format.
        """
        self.authenticate()
        body = (
            GWT_REMOVE_SERVING
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{serving_id}", serving_id)
        )
        raw = self._gwt_post(body)
        # Success response: //OK[[],0,7]
        if "//OK" not in raw:
            raise RuntimeError(f"removeServing returned unexpected response: {raw[:200]}")
        logger.info("Removed serving %s", serving_id)
        return True

    def get_food_log(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        """Get detailed food log (servings) for a date range."""
        return self.export_parsed("servings", start, end)

    def get_daily_summary(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        """Get daily nutrition summary for a date range."""
        return self.export_parsed("daily_summary", start, end)
