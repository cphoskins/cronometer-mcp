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
    "{weight_grams}|{food_source_id}|A|{food_id}|0|1|"
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

GWT_GET_ALL_MACRO_SCHEDULES = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getAllMacroSchedules|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{user_id}|"
)

GWT_GET_DAILY_MACRO_TARGET_TEMPLATE = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getDailyMacroTargetTemplate|java.lang.String/2004016611|"
    "I|com.cronometer.shared.entries.models.Day/782579793|"
    "{nonce}|"
    "1|2|3|4|3|5|6|7|8|{user_id}|7|{day}|{month}|{year}|"
)

GWT_UPDATE_DAILY_TARGET_TEMPLATE = (
    "7|0|12|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "updateDailyTargetTemplate|java.lang.String/2004016611|"
    "I|com.cronometer.shared.targets.models.MacroTargetTemplate/3691130822|"
    "{nonce}|"
    "java.lang.Boolean/476441737|"
    "java.lang.Double/858496421|"
    "com.cronometer.shared.entries.models.Day/782579793|"
    "{template_name}|"
    "1|2|3|4|3|5|6|7|8|{user_id}|"
    "7|9|0|10|{carbs}|0|11|{day}|{month}|{year}|"
    "10|{calories}|10|{fat}|0|1|0|0|0|12|10|{protein}|0|"
)

GWT_GET_MACRO_TARGET_TEMPLATES = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getMacroTargetTemplates|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{user_id}|"
)

GWT_SAVE_MACRO_SCHEDULE = (
    "7|0|9|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "saveMacroSchedule|java.lang.String/2004016611|"
    "I|com.cronometer.shared.targets.DayOfWeek/913617675|"
    "{nonce}|"
    "com.cronometer.shared.targets.DayOfWeek$DayOfWeekEnum/3974900421|"
    "1|2|3|4|4|5|6|7|6|8|{user_id}|7|9|{day_of_week}|{template_id}|"
)

GWT_DELETE_MACRO_TARGET_TEMPLATE = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "deleteMacroTargetTemplate|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|3|5|6|6|7|{user_id}|{template_id}|"
)

# --- Biometric GWT templates ---

GWT_GET_RECENT_BIOMETRICS = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getRecentBiometrics|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{user_id}|"
)

GWT_ADD_BIOMETRIC = (
    "7|0|9|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "addBiometric|java.lang.String/2004016611|"
    "com.cronometer.shared.biometrics.Biometric/2989635787|"
    "I|{nonce}|"
    "com.cronometer.shared.entries.models.Day/782579793|"
    "1|2|3|4|3|5|6|7|8|6|"
    "{value}|9|{day}|{month}|{year}|0|A|0|1|0|{flags}|"
    "0|0|0|0|0|{metric_position}|0|{user_id}|"
)

GWT_REMOVE_MEASUREMENT = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "removeMeasurement|java.lang.String/2004016611|"
    "J|I|{nonce}|"
    "1|2|3|4|3|5|6|7|8|{biometric_id}|{user_id}|"
)

# Known biometric metric types and their addBiometric flags/position
# flags encodes metric type + unit preferences
# metric_position is the position in the Biometric object structure
_BIOMETRIC_TYPES = {
    "weight": {"flags": 65539, "metric_position": 2, "unit": "lbs"},
    "blood_glucose": {"flags": 196609, "metric_position": 2, "unit": "mg/dL"},
    "heart_rate": {"flags": 65540, "metric_position": 2, "unit": "bpm"},
    "body_fat": {"flags": 65539, "metric_position": 2, "unit": "%"},
}

# --- Fasting GWT templates ---

GWT_GET_USER_FASTS = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getUserFasts|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{user_id}|"
)

GWT_GET_USER_FASTS_FOR_RANGE = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getUserFastsForRange|java.lang.String/2004016611|"
    "I|com.cronometer.shared.entries.models.Day/782579793|"
    "{nonce}|"
    "1|2|3|4|4|5|6|7|7|8|{user_id}|"
    "7|{start_day}|{start_month}|{start_year}|"
    "7|{end_day}|{end_month}|{end_year}|"
)

GWT_GET_FASTING_STATS = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getFastingStats|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{user_id}|"
)

GWT_DELETE_FAST = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "deleteFast|java.lang.String/2004016611|"
    "I|java.lang.Integer/3438268394|"
    "{nonce}|"
    "1|2|3|4|4|5|6|6|7|8|{user_id}|{fast_id}|0|"
)

GWT_CANCEL_FAST_KEEP_SERIES = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "cancelFastAndKeepSeries|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|3|5|6|6|7|{user_id}|{fast_id}|"
)

# --- Diary operations GWT templates ---

GWT_COPY_DAY = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "copyDay|java.lang.String/2004016611|"
    "I|com.cronometer.shared.entries.models.Day/782579793|"
    "{nonce}|"
    "1|2|3|4|4|5|6|7|7|8|{user_id}|"
    "7|{src_day}|{src_month}|{src_year}|"
    "7|{dst_day}|{dst_month}|{dst_year}|"
)

GWT_SET_DAY_COMPLETE = (
    "7|0|9|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "setDayComplete|java.lang.String/2004016611|"
    "I|com.cronometer.shared.entries.models.Day/782579793|"
    "java.lang.Boolean/476441737|"
    "{nonce}|"
    "1|2|3|4|4|5|6|7|8|9|{user_id}|"
    "7|{day}|{month}|{year}|{complete}|"
)

# --- Repeat item GWT templates ---

GWT_GET_REPEATED_ITEMS = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "getRepeatedItems|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|2|5|6|7|{user_id}|"
)

GWT_ADD_REPEAT_ITEM = (
    "7|0|11|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "addRepeatItem|java.lang.String/2004016611|"
    "I|com.cronometer.shared.repeatitems.RepeatItem/477684891|"
    "{nonce}|"
    "java.util.ArrayList/4159755760|"
    "java.lang.Integer/3438268394|"
    "{food_name}|"
    "1|2|3|4|3|5|6|7|8|{user_id}|7|{quantity}|"
    "9|{day_count}|{day_entries}|"
    "0|11|{diary_group}|0|{food_source_id}|{food_id}|0|"
)

GWT_DELETE_REPEAT_ITEM = (
    "7|0|7|https://cronometer.com/cronometer/|"
    "{gwt_header}|"
    "com.cronometer.shared.rpc.CronometerService|"
    "deleteRepeatItem|java.lang.String/2004016611|"
    "I|{nonce}|"
    "1|2|3|4|3|5|6|6|7|{user_id}|{repeat_item_id}|"
)

# US ordering (getAllMacroSchedules) to ISO ordering (saveMacroSchedule)
# getAllMacroSchedules: 0=Sun, 1=Mon, ..., 6=Sat
# saveMacroSchedule:   0=Mon, 1=Tue, ..., 6=Sun
_US_TO_ISO_DOW = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}

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

    def _reauthenticate(self) -> None:
        """Force a full re-authentication, discarding cached session state."""
        logger.info("Session expired, re-authenticating...")
        self._authenticated = False
        self.nonce = None
        self.user_id = None
        self.session.cookies.clear()
        self._cookie_path.unlink(missing_ok=True)
        self.authenticate()

    def export_raw(
        self,
        export_type: str,
        start: date | None = None,
        end: date | None = None,
        *,
        _retried: bool = False,
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
        if resp.status_code == 403 and not _retried:
            self._reauthenticate()
            return self.export_raw(export_type, start, end, _retried=True)
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

    def _gwt_post(self, body: str, *, _retried: bool = False) -> str:
        """POST a GWT-RPC payload and return the raw response text.

        Raises RuntimeError if the response does not start with '//OK'.
        On ``NotLoggedInException`` the session is transparently
        re-authenticated and the request is retried once.
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
            if "NotLoggedInException" in resp.text and not _retried:
                self._reauthenticate()
                return self._gwt_post(body, _retried=True)
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
            measure_id: Measure/unit ID. Pass 0 to auto-select
                        UNIVERSAL_MEASURE_ID (124399). The diary_group is
                        encoded into the high 16 bits automatically.
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

        # Encode diary_group into the measure_id's high 16 bits.
        # The Cronometer server reads the diary group from this encoding:
        #   1=Breakfast, 2=Lunch, 3=Dinner, 4=Snacks.
        # Strip any existing group from the high bits, then apply the requested one.
        measure_base = measure_id & 0xFFFF
        encoded_measure = (diary_group << 16) | measure_base

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
            .replace("{measure_id}", str(encoded_measure))
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

    # ── Macro target methods ──────────────────────────────────────────

    @staticmethod
    def _extract_gwt_string_table(raw: str) -> list[str]:
        """Extract the string table from a GWT-RPC //OK[...] response."""
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
        return json.loads(raw[st_open : st_close + 1])

    @staticmethod
    def _tokenize_gwt_data(raw: str, string_table: list[str]) -> list:
        """Tokenize the data section of a GWT-RPC response.

        Returns a list of int, float, or str tokens.
        """
        # Find the string table position to extract data before it
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

        data_section = raw[5:st_open].rstrip(",")
        if not data_section:
            return []

        tokens: list = []
        for part in data_section.split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith('"') and part.endswith('"'):
                tokens.append(part.strip('"'))
                continue
            try:
                tokens.append(float(part) if "." in part else int(part))
            except ValueError:
                tokens.append(None)
        return tokens

    @staticmethod
    def _parse_macro_target_template(raw: str) -> dict:
        """Parse a GWT-RPC response containing a single MacroTargetTemplate.

        Works for both getDailyMacroTargetTemplate and getMacroTargetTemplate
        responses. Extracts macro values by finding float tokens in the data.

        The float values appear in a fixed order (left to right):
        protein, fat, calories, carbs.

        Returns:
            Dict with keys: protein_g, fat_g, calories, carbs_g, template_name.
        """
        result = {
            "protein_g": 0.0,
            "fat_g": 0.0,
            "calories": 0.0,
            "carbs_g": 0.0,
            "template_name": "",
        }

        if not raw.startswith("//OK[") or not raw.endswith(",0,7]"):
            return result

        string_table = CronometerClient._extract_gwt_string_table(raw)

        # Template name = last non-class string in the string table
        for entry in reversed(string_table):
            if (
                not entry.startswith("com.")
                and not entry.startswith("java.")
                and not entry.startswith("[")
            ):
                result["template_name"] = entry
                break

        # Tokenize and extract float values
        tokens = CronometerClient._tokenize_gwt_data(raw, string_table)
        floats = [t for t in tokens if isinstance(t, float)]

        # In MacroTargetTemplate responses, floats appear in order:
        # protein, fat, calories, carbs
        if len(floats) >= 4:
            result["protein_g"] = floats[0]
            result["fat_g"] = floats[1]
            result["calories"] = floats[2]
            result["carbs_g"] = floats[3]

        return result

    @staticmethod
    def _parse_all_macro_schedules(raw: str) -> list[dict]:
        """Parse a GWT-RPC getAllMacroSchedules response.

        Returns a list of 7 dicts (one per day of week), each with:
        day_of_week (0=Sun..6=Sat), protein_g, fat_g, calories, carbs_g,
        template_name, template_id.

        GWT encoding note: The response contains 7 MacroSchedule objects
        in fixed-size blocks. Only the first block uses full type refs;
        subsequent blocks use GWT back-references (-N). The block size
        is determined by finding the first MacroSchedule type ref.
        Within each block, floats appear in order: protein, fat, calories,
        carbs. The day ordinal is the last token in each block (for block 0,
        the MacroSchedule type ref occupies that slot, so day 0 = Sunday
        is inferred).
        """
        _DOW_NAMES = [
            "Sunday", "Monday", "Tuesday", "Wednesday",
            "Thursday", "Friday", "Saturday",
        ]

        if not raw.startswith("//OK[") or not raw.endswith(",0,7]"):
            return []

        string_table = CronometerClient._extract_gwt_string_table(raw)
        tokens = CronometerClient._tokenize_gwt_data(raw, string_table)

        # Find MacroSchedule type index (1-based) in string table
        schedule_type_idx = None
        for idx, entry in enumerate(string_table):
            if "MacroSchedule/" in entry:
                schedule_type_idx = idx + 1
                break

        if schedule_type_idx is None:
            return []

        # Find the first occurrence of the MacroSchedule type ref to
        # determine block size. It appears at the END of the first block.
        first_sched_pos = None
        for i, token in enumerate(tokens):
            if token == schedule_type_idx:
                first_sched_pos = i
                break

        if first_sched_pos is None:
            return []

        block_size = first_sched_pos + 1  # block 0 spans tokens 0..first_sched_pos

        # Template name(s) — non-class strings in the string table.
        # Also handle negative back-refs (e.g., -6 → string_table[5]).
        template_names = {}
        for idx, entry in enumerate(string_table):
            if (
                not entry.startswith("com.")
                and not entry.startswith("java.")
                and not entry.startswith("[")
            ):
                template_names[idx + 1] = entry      # positive ref
                template_names[-(idx + 1)] = entry    # negative back-ref

        # Extract 7 blocks and determine day ordinals.
        # GWT serialization varies between Cronometer versions:
        # - Some versions put the ordinal at block[-4] (before type refs)
        # - Others put it at block[-1] (after back-refs)
        # Strategy: try block[-4] first; if values aren't unique 0-6, try block[-1].
        blocks = []
        for block_idx in range(7):
            start = block_idx * block_size
            end = start + block_size
            if end > len(tokens):
                break
            blocks.append(tokens[start:end])

        # Try block[-4] for day ordinals
        ordinals_m4 = [b[-4] if len(b) >= 4 and isinstance(b[-4], int) else -1 for b in blocks]
        ordinals_m1 = [b[-1] if len(b) >= 1 and isinstance(b[-1], int) else -1 for b in blocks]

        if set(ordinals_m4) == set(range(7)):
            ordinals = ordinals_m4
        else:
            # block[-1] has ordinals for blocks 1-6; block 0's [-1] is
            # the MacroSchedule type ref (a duplicate value). Detect the
            # duplicate and replace it with the missing ordinal.
            ordinals = list(ordinals_m1)
            seen: dict[int, list[int]] = {}
            for i, v in enumerate(ordinals):
                seen.setdefault(v, []).append(i)
            missing = set(range(7)) - set(ordinals)
            if missing:
                missing_val = missing.pop()
                # Find the duplicate value — the one that appears twice
                for val, indices in seen.items():
                    if len(indices) > 1:
                        # The first occurrence (block 0) is the bogus one
                        ordinals[indices[0]] = missing_val
                        break
            # Fallback: if still not unique, assign sequentially
            if set(ordinals) != set(range(7)):
                ordinals = list(range(7))

        schedules = []
        for block_idx, block in enumerate(blocks):
            dow_ordinal = ordinals[block_idx]

            template_data = {
                "day_of_week": dow_ordinal,
                "day_name": _DOW_NAMES[dow_ordinal] if 0 <= dow_ordinal < 7 else f"Day {dow_ordinal}",
                "protein_g": 0.0,
                "fat_g": 0.0,
                "calories": 0.0,
                "carbs_g": 0.0,
                "template_name": "",
                "template_id": 0,
            }

            # Extract floats from this block → [protein, fat, calories, carbs]
            floats = [t for t in block if isinstance(t, float)]
            if len(floats) >= 4:
                template_data["protein_g"] = floats[0]
                template_data["fat_g"] = floats[1]
                template_data["calories"] = floats[2]
                template_data["carbs_g"] = floats[3]

            # Template name: look for string refs (positive or negative)
            for t in block:
                if isinstance(t, int) and t in template_names:
                    template_data["template_name"] = template_names[t]

            # Template ID: large integer (> string table size) in the block
            for t in block:
                if isinstance(t, int) and t > len(string_table):
                    template_data["template_id"] = t
                    break

            schedules.append(template_data)

        # Sort by day_of_week
        schedules.sort(key=lambda x: x["day_of_week"])
        return schedules

    def get_all_macro_schedules(self) -> list[dict]:
        """Get the weekly macro target schedule (all 7 days).

        Returns:
            List of 7 dicts, one per day of week, each containing:
            - day_of_week (int): 0=Sunday through 6=Saturday
            - day_name (str): Human-readable day name
            - protein_g (float): Protein target in grams
            - fat_g (float): Fat target in grams
            - calories (float): Calorie target
            - carbs_g (float): Net carbs target in grams
            - template_name (str): Name of the assigned template
            - template_id (int): Template ID (0 for custom)
        """
        self.authenticate()
        body = (
            GWT_GET_ALL_MACRO_SCHEDULES
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
        )
        raw = self._gwt_post(body)
        return self._parse_all_macro_schedules(raw)

    def get_daily_macro_targets(self, day: date | None = None) -> dict:
        """Get the effective macro targets for a specific date.

        Args:
            day: Target date (defaults to today).

        Returns:
            Dict with keys: protein_g, fat_g, calories, carbs_g,
            template_name.
        """
        self.authenticate()
        if day is None:
            day = date.today()
        body = (
            GWT_GET_DAILY_MACRO_TARGET_TEMPLATE
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{day}", str(day.day))
            .replace("{month}", str(day.month))
            .replace("{year}", str(day.year))
        )
        raw = self._gwt_post(body)
        return self._parse_macro_target_template(raw)

    def update_daily_targets(
        self,
        day: date,
        protein_g: float,
        fat_g: float,
        carbs_g: float,
        calories: float,
        template_name: str = "Custom Targets",
    ) -> bool:
        """Update macro targets for a specific date.

        Args:
            day: Target date.
            protein_g: Protein target in grams.
            fat_g: Fat target in grams.
            carbs_g: Net carbs target in grams.
            calories: Calorie target.
            template_name: Template name (default "Custom Targets").

        Returns:
            True on success.
        """
        self.authenticate()

        # Format numeric values: integers as int, otherwise float
        def _fmt(v: float) -> str:
            return str(int(v)) if v == int(v) else str(v)

        body = (
            GWT_UPDATE_DAILY_TARGET_TEMPLATE
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{template_name}", template_name)
            .replace("{day}", str(day.day))
            .replace("{month}", str(day.month))
            .replace("{year}", str(day.year))
            .replace("{protein}", _fmt(protein_g))
            .replace("{fat}", _fmt(fat_g))
            .replace("{carbs}", _fmt(carbs_g))
            .replace("{calories}", _fmt(calories))
        )
        raw = self._gwt_post(body)
        # Success: //OK[1,2,1,["...ResponseEvent...","Success"],0,7]
        if "Success" in raw:
            logger.info(
                "Updated daily targets for %s: protein=%.1fg, fat=%.1fg, "
                "carbs=%.1fg, calories=%.0f",
                day, protein_g, fat_g, carbs_g, calories,
            )
            return True
        raise RuntimeError(
            f"updateDailyTargetTemplate failed: {raw[:300]}"
        )

    def get_macro_target_templates(self) -> list[dict]:
        """Get all saved macro target templates.

        Returns:
            List of dicts with keys: template_id, template_name,
            protein_g, fat_g, calories, carbs_g.
        """
        self.authenticate()
        body = (
            GWT_GET_MACRO_TARGET_TEMPLATES
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
        )
        raw = self._gwt_post(body)
        return self._parse_macro_target_templates(raw)

    @staticmethod
    def _parse_macro_target_templates(raw: str) -> list[dict]:
        """Parse getMacroTargetTemplates GWT response.

        Returns list of template dicts with id, name, and macro values.
        """
        if not raw.startswith("//OK["):
            return []

        string_table = CronometerClient._extract_gwt_string_table(raw)
        tokens = CronometerClient._tokenize_gwt_data(raw, string_table)

        # Find MacroTargetTemplate type index
        template_type_idx = None
        for idx, entry in enumerate(string_table):
            if "MacroTargetTemplate/" in entry:
                template_type_idx = idx + 1
                break

        if template_type_idx is None:
            return []

        # Find block boundaries by locating each template type ref
        # or back-reference. First occurrence is the type ref,
        # subsequent are back-refs (negative).
        first_pos = None
        for i, token in enumerate(tokens):
            if token == template_type_idx:
                first_pos = i
                break

        if first_pos is None:
            return []

        block_size = first_pos + 1

        # Extract template names from string table
        template_name_map = {}
        for idx, entry in enumerate(string_table):
            if (
                not entry.startswith("com.")
                and not entry.startswith("java.")
                and not entry.startswith("[")
            ):
                template_name_map[idx + 1] = entry
                template_name_map[-(idx + 1)] = entry

        templates = []
        block_idx = 0
        while True:
            start = block_idx * block_size
            end = start + block_size
            if end > len(tokens):
                break

            block = tokens[start:end]

            # Extract floats: [protein, fat, calories, carbs]
            floats = [t for t in block if isinstance(t, float)]

            # Extract template name
            name = ""
            for t in block:
                if isinstance(t, int) and t in template_name_map:
                    name = template_name_map[t]

            # Extract template ID: large int > string table size
            template_id = 0
            for t in block:
                if isinstance(t, int) and t > len(string_table):
                    template_id = t
                    break

            if len(floats) >= 4:
                templates.append({
                    "template_id": template_id,
                    "template_name": name,
                    "protein_g": floats[0],
                    "fat_g": floats[1],
                    "calories": floats[2],
                    "carbs_g": floats[3],
                })

            block_idx += 1

        return templates

    def save_macro_schedule(
        self,
        day_of_week_us: int,
        template_id: int,
    ) -> bool:
        """Assign a macro template to a day of the week in the schedule.

        Args:
            day_of_week_us: Day of week in US ordering (0=Sunday, 6=Saturday).
            template_id: Template ID from get_macro_target_templates().
                         Use 0 for the default profile targets.

        Returns:
            True on success.
        """
        self.authenticate()

        # Convert US ordering (0=Sun) to ISO ordering (0=Mon) for the API
        iso_dow = _US_TO_ISO_DOW[day_of_week_us]

        body = (
            GWT_SAVE_MACRO_SCHEDULE
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{day_of_week}", str(iso_dow))
            .replace("{template_id}", str(template_id))
        )
        raw = self._gwt_post(body)
        if "//OK" in raw:
            logger.info(
                "Set macro schedule: day_of_week=%d (US) -> %d (ISO), "
                "template_id=%d",
                day_of_week_us, iso_dow, template_id,
            )
            return True
        raise RuntimeError(
            f"saveMacroSchedule failed: {raw[:300]}"
        )

    def save_macro_target_template(
        self,
        template_name: str,
        protein_g: float,
        fat_g: float,
        carbs_g: float,
        calories: float,
    ) -> int:
        """Create a new saved macro target template.

        Args:
            template_name: Name for the template.
            protein_g: Protein target in grams.
            fat_g: Fat target in grams.
            carbs_g: Net carbs target in grams.
            calories: Calorie target.

        Returns:
            The template_id assigned by the server.
        """
        self.authenticate()

        def _fmt(v: float) -> str:
            return str(int(v)) if v == int(v) else str(v)

        # Build the GWT-RPC payload dynamically because the fat field
        # uses object back-references when fat == carbs (GWT optimization).
        # String table positions (1-indexed):
        #  1=module, 2=gwt_header, 3=service, 4=method, 5=String type,
        #  6=I type, 7=MacroTargetTemplate type, 8=nonce, 9=Boolean type,
        #  10=Double type, 11=Integer type, 12=Rigorous, 13=template_name
        carbs_str = _fmt(carbs_g)
        fat_str = _fmt(fat_g)
        cal_str = _fmt(calories)
        protein_str = _fmt(protein_g)

        if fat_g == carbs_g:
            # Fat equals carbs: use back-reference -3 (refers to
            # the Double object at position 3 in the object stream)
            fat_token = "-3"
        else:
            # Fat differs: encode explicitly
            fat_token = f"10|{fat_str}"

        # The data section encodes the MacroTargetTemplate fields.
        # Field order: boolean, carbs, fat, null, calories,
        #   [extra fields], template_id(0=new), program("Rigorous"),
        #   null, template_name, protein, [trailing ref]
        #
        # When fat==carbs, trailing back-refs like -6 refer to
        # Double(calories). When fat!=carbs, the object positions
        # shift so we use explicit values instead.
        if fat_g == carbs_g:
            data = (
                f"8|{self.user_id}|"
                f"7|9|0|10|{carbs_str}|-3|0|10|{cal_str}|-3|-3|0|"
                f"11|0|12|0|13|10|{protein_str}|-6|"
            )
        else:
            data = (
                f"8|{self.user_id}|"
                f"7|9|0|10|{carbs_str}|10|{fat_str}|0|10|{cal_str}|"
                f"10|{fat_str}|10|{fat_str}|0|"
                f"11|0|12|0|13|10|{protein_str}|10|{cal_str}|"
            )

        header = (
            "7|0|13|https://cronometer.com/cronometer/|"
            f"{self.gwt_header}|"
            "com.cronometer.shared.rpc.CronometerService|"
            "saveMacroTargetTemplate|java.lang.String/2004016611|"
            "I|com.cronometer.shared.targets.models.MacroTargetTemplate/"
            "3691130822|"
            f"{self.nonce or ''}|"
            "java.lang.Boolean/476441737|"
            "java.lang.Double/858496421|"
            "java.lang.Integer/3438268394|"
            "Rigorous|"
            f"{template_name}|"
            "1|2|3|4|3|5|6|7|"
        )

        body = header + data
        raw = self._gwt_post(body)

        if "//OK" not in raw:
            raise RuntimeError(
                f"saveMacroTargetTemplate failed: {raw[:300]}"
            )

        logger.info(
            "Created macro target template '%s': protein=%.1fg, "
            "fat=%.1fg, carbs=%.1fg, calories=%.0f",
            template_name, protein_g, fat_g, carbs_g, calories,
        )

        # Fetch templates to get the server-assigned template_id
        templates = self.get_macro_target_templates()
        for t in templates:
            if t["template_name"] == template_name:
                return t["template_id"]

        # Template was created but not found — return 0 as fallback
        logger.warning(
            "Template '%s' created but not found in template list",
            template_name,
        )
        return 0

    def delete_macro_target_template(self, template_id: int) -> bool:
        """Delete a saved macro target template.

        Args:
            template_id: Template ID to delete.

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_DELETE_MACRO_TARGET_TEMPLATE
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{template_id}", str(template_id))
        )
        raw = self._gwt_post(body)
        if "//OK" in raw:
            logger.info("Deleted macro target template: id=%d", template_id)
            return True
        raise RuntimeError(
            f"deleteMacroTargetTemplate failed: {raw[:300]}"
        )

    # --- Fasting methods ---

    def get_user_fasts(self) -> list[dict]:
        """Get all fasting history.

        Returns:
            List of fast dicts with keys: fast_id, recurrence_id, name,
            recurrence_rule, start_ts, end_ts, notes, is_active.
        """
        self.authenticate()
        body = (
            GWT_GET_USER_FASTS
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
        )
        raw = self._gwt_post(body)
        return self._parse_fasts(raw)

    def get_user_fasts_for_range(
        self, start: date, end: date,
    ) -> list[dict]:
        """Get fasts for a specific date range.

        Args:
            start: Start date.
            end: End date.

        Returns:
            List of fast dicts.
        """
        self.authenticate()
        body = (
            GWT_GET_USER_FASTS_FOR_RANGE
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{start_day}", str(start.day))
            .replace("{start_month}", str(start.month))
            .replace("{start_year}", str(start.year))
            .replace("{end_day}", str(end.day))
            .replace("{end_month}", str(end.month))
            .replace("{end_year}", str(end.year))
        )
        raw = self._gwt_post(body)
        return self._parse_fasts(raw)

    def get_fasting_stats(self) -> dict:
        """Get aggregate fasting statistics.

        Returns:
            Dict with keys: total_hours, longest_fast_hours,
            seven_fast_avg_hours, completed_count.
        """
        self.authenticate()
        body = (
            GWT_GET_FASTING_STATS
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
        )
        raw = self._gwt_post(body)
        return self._parse_fasting_stats(raw)

    def delete_fast(self, fast_id: int) -> bool:
        """Delete a fast entry.

        Args:
            fast_id: Fast ID to delete.

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_DELETE_FAST
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{fast_id}", str(fast_id))
        )
        raw = self._gwt_post(body)
        if "//OK" in raw:
            logger.info("Deleted fast: id=%d", fast_id)
            return True
        raise RuntimeError(f"deleteFast failed: {raw[:300]}")

    def cancel_fast_keep_series(self, fast_id: int) -> bool:
        """Cancel an active fast while preserving the recurring schedule.

        Args:
            fast_id: The recurrence/fast ID of the active fast.

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_CANCEL_FAST_KEEP_SERIES
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{fast_id}", str(fast_id))
        )
        raw = self._gwt_post(body)
        if "//OK" in raw:
            logger.info("Cancelled fast (kept series): id=%d", fast_id)
            return True
        raise RuntimeError(
            f"cancelFastAndKeepSeries failed: {raw[:300]}"
        )

    @staticmethod
    def _parse_fasting_stats(raw: str) -> dict:
        """Parse getFastingStats GWT response.

        Response format:
        //OK[{totalHours},{longestFast},{sevenFastAvg},{completedCount},
             1,[...string table...],0,7]
        """
        if not raw.startswith("//OK["):
            return {}

        string_table = CronometerClient._extract_gwt_string_table(raw)
        tokens = CronometerClient._tokenize_gwt_data(raw, string_table)

        floats = [t for t in tokens if isinstance(t, float)]
        ints = [t for t in tokens if isinstance(t, int)]

        result = {
            "total_hours": 0.0,
            "longest_fast_hours": 0.0,
            "seven_fast_avg_hours": 0.0,
            "completed_count": 0,
        }

        if len(floats) >= 3:
            result["total_hours"] = round(floats[0], 1)
            result["longest_fast_hours"] = round(floats[1], 1)
            result["seven_fast_avg_hours"] = round(floats[2], 1)

        # completed_count is typically the first large-ish int
        # (after string table refs which are small)
        for val in ints:
            if val > len(string_table) and val < 100000:
                result["completed_count"] = val
                break

        return result

    @staticmethod
    def _parse_fasts(raw: str) -> list[dict]:
        """Parse getUserFasts or getUserFastsForRange GWT response.

        Extracts Fast objects from the GWT-RPC response. Each Fast has:
        - fast_id (int)
        - recurrence_id (int)
        - name (str)
        - recurrence_rule (str, e.g. "FREQ=WEEKLY")
        - start_ts (str, base62 timestamp)
        - end_ts (str, base62 timestamp or "0")
        - is_active (bool, True if end_ts is "0" or empty)
        """
        if not raw.startswith("//OK["):
            return []

        string_table = CronometerClient._extract_gwt_string_table(raw)
        tokens = CronometerClient._tokenize_gwt_data(raw, string_table)

        # Find the Fast type in string table
        fast_type_idx = None
        for idx, entry in enumerate(string_table):
            if "fasting.Fast/" in entry and "[L" not in entry:
                fast_type_idx = idx + 1
                break

        if fast_type_idx is None:
            return []

        # Find FastingRecurrance type
        recurrence_type_idx = None
        for idx, entry in enumerate(string_table):
            if "FastingRecurrance/" in entry:
                recurrence_type_idx = idx + 1
                break

        # Extract meaningful strings (fast names, recurrence rules, notes)
        meaningful_strings = {}
        for idx, entry in enumerate(string_table):
            if (
                not entry.startswith("com.")
                and not entry.startswith("java.")
                and not entry.startswith("[")
            ):
                meaningful_strings[idx + 1] = entry
                meaningful_strings[-(idx + 1)] = entry

        # Find the first Fast type ref to determine block size
        first_fast_pos = None
        for i, token in enumerate(tokens):
            if token == fast_type_idx:
                first_fast_pos = i
                break

        if first_fast_pos is None:
            return []

        block_size = first_fast_pos + 1

        # Extract blocks
        fasts = []
        block_idx = 0
        while True:
            start = block_idx * block_size
            end = start + block_size
            if end > len(tokens):
                break

            block = tokens[start:end]

            # Extract strings from this block (fast name, recurrence rule,
            # notes). Strings are referenced by string table index.
            block_strings = []
            for t in block:
                if isinstance(t, str):
                    block_strings.append(t)
                elif isinstance(t, int) and t in meaningful_strings:
                    block_strings.append(meaningful_strings[t])
                elif isinstance(t, int) and t < 0 and t in meaningful_strings:
                    block_strings.append(meaningful_strings[t])

            # Extract large ints (fast_id, recurrence_id)
            large_ints = [
                t for t in block
                if isinstance(t, int)
                and abs(t) > len(string_table)
                and abs(t) < 10**9
            ]

            # Extract quoted strings (base62 timestamps)
            quoted_strings = [t for t in block if isinstance(t, str)]

            # Build fast dict
            fast = {
                "fast_id": large_ints[0] if len(large_ints) >= 1 else 0,
                "recurrence_id": large_ints[1] if len(large_ints) >= 2 else 0,
                "name": "",
                "recurrence_rule": "",
                "start_ts": "",
                "end_ts": "",
                "is_active": False,
            }

            # Assign strings heuristically
            for s in block_strings:
                if s.startswith("FREQ="):
                    fast["recurrence_rule"] = s
                elif any(c.isalpha() and c.isupper() for c in s) and len(s) < 10 and s != "0":
                    # Likely a base62 timestamp
                    if not fast["start_ts"]:
                        fast["start_ts"] = s
                    else:
                        fast["end_ts"] = s
                elif len(s) > 3:
                    # Likely a name or note
                    if not fast["name"]:
                        fast["name"] = s
                    # Additional strings could be notes

            # Timestamps from quoted strings in the block
            for s in quoted_strings:
                if s and s != "0" and len(s) >= 5:
                    if not fast["start_ts"]:
                        fast["start_ts"] = s
                    elif not fast["end_ts"]:
                        fast["end_ts"] = s

            fast["is_active"] = fast["end_ts"] in ("", "0")

            if fast["fast_id"] or fast["name"]:
                fasts.append(fast)

            block_idx += 1

        return fasts

    # --- Biometric methods ---

    def get_recent_biometrics(self) -> list[dict]:
        """Get the most recently logged biometric entries.

        Returns:
            List of dicts with keys: biometric_id, metric_id, value,
            date, metric_name (if available).
        """
        self.authenticate()
        body = (
            GWT_GET_RECENT_BIOMETRICS
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
        )
        raw = self._gwt_post(body)
        return self._parse_recent_biometrics(raw)

    def add_biometric(
        self,
        metric_type: str,
        value: float,
        day: date,
    ) -> str:
        """Add a biometric entry.

        Args:
            metric_type: One of 'weight', 'blood_glucose', 'heart_rate',
                         'body_fat'.
            value: The value in display units (lbs, mg/dL, bpm, %).
            day: Date for the entry.

        Returns:
            The biometric entry ID (string).
        """
        self.authenticate()

        if metric_type not in _BIOMETRIC_TYPES:
            raise ValueError(
                f"Unknown metric_type '{metric_type}'. "
                f"Supported: {list(_BIOMETRIC_TYPES.keys())}"
            )

        info = _BIOMETRIC_TYPES[metric_type]

        def _fmt(v: float) -> str:
            return str(int(v)) if v == int(v) else str(v)

        body = (
            GWT_ADD_BIOMETRIC
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{value}", _fmt(value))
            .replace("{day}", str(day.day))
            .replace("{month}", str(day.month))
            .replace("{year}", str(day.year))
            .replace("{flags}", str(info["flags"]))
            .replace("{metric_position}", str(info["metric_position"]))
        )
        raw = self._gwt_post(body)

        if "//OK" not in raw:
            raise RuntimeError(f"addBiometric failed: {raw[:300]}")

        # Extract biometric ID from response: //OK["BXW0DA",[],0,7]
        biometric_id = ""
        if raw.startswith("//OK["):
            import re
            match = re.search(r'"([A-Za-z0-9]+)"', raw)
            if match:
                biometric_id = match.group(1)

        logger.info(
            "Added biometric: type=%s, value=%.1f, date=%s, id=%s",
            metric_type, value, day, biometric_id,
        )
        return biometric_id

    def remove_biometric(self, biometric_id: str) -> bool:
        """Remove a biometric entry.

        Args:
            biometric_id: The biometric entry ID (e.g. "BXW0DA").

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_REMOVE_MEASUREMENT
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{biometric_id}", biometric_id)
        )
        raw = self._gwt_post(body)
        if "//OK" in raw:
            logger.info("Removed biometric: id=%s", biometric_id)
            return True
        raise RuntimeError(f"removeMeasurement failed: {raw[:300]}")

    def _parse_recent_biometrics(self, raw: str) -> list[dict]:
        """Parse getRecentBiometrics GWT response.

        Returns list of biometric entries with id, metric_id, value, date.
        """
        if not raw.startswith("//OK["):
            return []

        string_table = CronometerClient._extract_gwt_string_table(raw)
        tokens = CronometerClient._tokenize_gwt_data(raw, string_table)

        # Find the Biometric type in string table
        bio_type_idx = None
        for idx, entry in enumerate(string_table):
            if "biometrics.Biometric/" in entry and "[L" not in entry:
                bio_type_idx = idx + 1
                break

        if bio_type_idx is None:
            return []

        # Find Day type
        day_type_idx = None
        for idx, entry in enumerate(string_table):
            if "models.Day/" in entry:
                day_type_idx = idx + 1
                break

        # Find first Biometric type ref to determine block size
        first_bio_pos = None
        for i, token in enumerate(tokens):
            if token == bio_type_idx:
                first_bio_pos = i
                break

        if first_bio_pos is None:
            return []

        block_size = first_bio_pos + 1

        # Extract meaningful strings (biometric IDs, composite JSON, etc.)
        meaningful_strings = {}
        for idx, entry in enumerate(string_table):
            if (
                not entry.startswith("com.")
                and not entry.startswith("java.")
                and not entry.startswith("[")
            ):
                meaningful_strings[idx + 1] = entry
                meaningful_strings[-(idx + 1)] = entry

        biometrics = []
        block_idx = 0
        while True:
            start = block_idx * block_size
            end = start + block_size
            if end > len(tokens):
                break

            block = tokens[start:end]

            # Extract floats (biometric value)
            floats = [t for t in block if isinstance(t, float)]

            # Extract strings (biometric ID, composite JSON)
            block_strings = []
            for t in block:
                if isinstance(t, str):
                    block_strings.append(t)
                elif isinstance(t, int) and t in meaningful_strings:
                    block_strings.append(meaningful_strings[t])

            # Extract large ints (metric_id, user_id, flags)
            large_ints = [
                t for t in block
                if isinstance(t, int)
                and abs(t) > len(string_table)
            ]

            # Build entry
            entry = {
                "biometric_id": "",
                "value": floats[0] if floats else 0.0,
                "metric_id": 0,
                "date": "",
            }

            # Biometric IDs are short alphanumeric strings (6-8 chars)
            for s in block_strings:
                if (
                    len(s) >= 4 and len(s) <= 12
                    and s.isalnum()
                    and not s.startswith("com")
                ):
                    entry["biometric_id"] = s
                elif s.startswith("{"):
                    # Composite JSON (blood pressure, etc.)
                    entry["composite"] = s

            # Extract date: look for 3 consecutive small ints that
            # could be day/month/year
            for i in range(len(block) - 2):
                if (
                    isinstance(block[i], int)
                    and isinstance(block[i + 1], int)
                    and isinstance(block[i + 2], int)
                    and 1 <= block[i] <= 31
                    and 1 <= block[i + 1] <= 12
                    and 2020 <= block[i + 2] <= 2030
                ):
                    entry["date"] = (
                        f"{block[i + 2]:04d}-{block[i + 1]:02d}-"
                        f"{block[i]:02d}"
                    )
                    break

            # metric_id is typically in the large_ints
            for val in large_ints:
                if val < 100000 and val != int(self.user_id or 0):
                    entry["metric_id"] = val
                    break

            if entry["biometric_id"] or entry["value"]:
                biometrics.append(entry)

            block_idx += 1

        return biometrics

    # ── Diary operations ──────────────────────────────────────────────

    def copy_day(self, src: date, dst: date) -> bool:
        """Copy all diary entries from one date to another.

        This is a server-side operation that copies ALL entries
        (food, exercise, notes, biometrics) from src to dst. It is
        additive — existing entries on dst are not removed.

        Args:
            src: Source date to copy from.
            dst: Destination date to copy to.

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_COPY_DAY
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{src_day}", str(src.day))
            .replace("{src_month}", str(src.month))
            .replace("{src_year}", str(src.year))
            .replace("{dst_day}", str(dst.day))
            .replace("{dst_month}", str(dst.month))
            .replace("{dst_year}", str(dst.year))
        )
        raw = self._gwt_post(body)
        if not raw.startswith("//OK"):
            raise RuntimeError(f"copyDay failed: {raw[:300]}")
        return True

    def set_day_complete(self, day: date, complete: bool = True) -> bool:
        """Mark a diary day as complete or incomplete.

        Args:
            day: The date to mark.
            complete: True to mark complete, False to mark incomplete.

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_SET_DAY_COMPLETE
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{day}", str(day.day))
            .replace("{month}", str(day.month))
            .replace("{year}", str(day.year))
            .replace("{complete}", "1" if complete else "0")
        )
        raw = self._gwt_post(body)
        if not raw.startswith("//OK"):
            raise RuntimeError(f"setDayComplete failed: {raw[:300]}")
        return True

    # ── Repeat item methods ───────────────────────────────────────────

    def get_repeated_items(self) -> list[dict]:
        """Get all recurring food entries.

        Returns:
            List of repeat item dicts with keys: repeat_item_id,
            food_name, food_source_id, measure_id, quantity,
            diary_group, days_of_week.
        """
        self.authenticate()
        body = (
            GWT_GET_REPEATED_ITEMS
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
        )
        raw = self._gwt_post(body)
        return self._parse_repeated_items(raw)

    def add_repeat_item(
        self,
        food_source_id: int,
        food_id: int,
        quantity: float,
        food_name: str,
        diary_group: int = 1,
        days_of_week: list[int] | None = None,
    ) -> bool:
        """Add a recurring food entry.

        Args:
            food_source_id: Food source ID from search_foods.
            food_id: Food ID from search_foods.
            quantity: Number of default servings (e.g., 12 cups of coffee).
            food_name: Display name for the food.
            diary_group: Meal slot — 1=Breakfast, 2=Lunch, 3=Dinner, 4=Snacks.
            days_of_week: List of days (0=Sun, 1=Mon, ..., 6=Sat).
                          Defaults to all 7 days.

        Returns:
            True on success.
        """
        self.authenticate()

        if days_of_week is None:
            days_of_week = [0, 1, 2, 3, 4, 5, 6]

        # Build day entries: "10|{day}" for each day, joined by "|"
        day_entries = "|".join(f"10|{d}" for d in days_of_week)

        # Format quantity as float-like string for GWT
        qty_str = str(int(quantity)) if quantity == int(quantity) else str(quantity)

        body = (
            GWT_ADD_REPEAT_ITEM
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{food_name}", food_name)
            .replace("{diary_group}", str(diary_group))
            .replace("{day_count}", str(len(days_of_week)))
            .replace("{day_entries}", day_entries)
            .replace("{quantity}", qty_str)
            .replace("{food_source_id}", str(food_source_id))
            .replace("{food_id}", str(food_id))
        )
        raw = self._gwt_post(body)
        if not raw.startswith("//OK"):
            raise RuntimeError(f"addRepeatItem failed: {raw[:300]}")
        return True

    def delete_repeat_item(self, repeat_item_id: int) -> bool:
        """Delete a recurring food entry.

        Args:
            repeat_item_id: The ID of the repeat item to delete.

        Returns:
            True on success.
        """
        self.authenticate()
        body = (
            GWT_DELETE_REPEAT_ITEM
            .replace("{gwt_header}", self.gwt_header)
            .replace("{nonce}", self.nonce or "")
            .replace("{user_id}", self.user_id or "")
            .replace("{repeat_item_id}", str(repeat_item_id))
        )
        raw = self._gwt_post(body)
        if not raw.startswith("//OK"):
            raise RuntimeError(f"deleteRepeatItem failed: {raw[:300]}")
        return True

    @staticmethod
    def _parse_repeated_items(raw: str) -> list[dict]:
        """Parse a GWT-RPC getRepeatedItems response.

        Response format example (1 item):
        //OK[0,1055762,461776,658384,1,4,0,1,3,1,1,3.0,2,1,1,
          ["java.util.ArrayList/...",
           "com.cronometer.shared.repeatitems.RepeatItem/477684891",
           "java.lang.Integer/3438268394",
           "Wasa, Crispbread, Multi Grain"],0,7]

        The string table has: ArrayList, RepeatItem, Integer, and food names.
        Data section has interleaved values for each RepeatItem.
        """
        if not raw.startswith("//OK"):
            return []

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
        string_table = json.loads(raw[st_open:st_close + 1])

        # Extract data tokens before string table
        data_section = raw[5:st_open].rstrip(",")
        if not data_section:
            return []

        tokens: list = []
        for part in data_section.split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith('"') and part.endswith('"'):
                tokens.append(part.strip('"'))
                continue
            try:
                tokens.append(float(part) if "." in part else int(part))
            except ValueError:
                tokens.append(None)

        # Find food names in string table (not type references)
        type_prefixes = ("java.", "com.cronometer.")
        food_names = [
            s for s in string_table
            if not any(s.startswith(p) for p in type_prefixes)
        ]

        # Find the RepeatItem type index
        repeat_type_idx = None
        for i, s in enumerate(string_table):
            if "RepeatItem/" in s:
                repeat_type_idx = i + 1
                break

        if repeat_type_idx is None:
            return []

        # Count items: look for the item count in the data
        # The response starts with type refs then item data
        # Find float values (quantities) to determine item count
        float_tokens = [t for t in tokens if isinstance(t, float)]
        item_count = len(float_tokens)  # each item has exactly one float (quantity)

        if item_count == 0:
            return []

        items = []
        # Parse items from the token stream
        # Key pattern: large ints are food_source_id, measure_id, repeat_item_id
        # Small ints include day counts, day-of-week values, diary group
        # The float is the quantity

        # Simple heuristic: split tokens into blocks per item
        # Each item has: food_source_id, measure_id, repeat_item_id,
        # day info, quantity, and references to food name

        # Find positions of float values to split blocks
        float_positions = [i for i, t in enumerate(tokens) if isinstance(t, float)]

        for item_idx, fpos in enumerate(float_positions):
            item = {
                "repeat_item_id": 0,
                "food_name": food_names[item_idx] if item_idx < len(food_names) else "",
                "food_source_id": 0,
                "measure_id": 0,
                "quantity": tokens[fpos],
                "diary_group": 0,
                "days_of_week": [],
            }

            # Look backwards from the float to find large ints
            # (food_source_id, measure_id, repeat_item_id)
            large_ints = []
            start = float_positions[item_idx - 1] + 1 if item_idx > 0 else 0
            block = tokens[start:fpos]

            for t in block:
                if isinstance(t, int) and t > 10000:
                    large_ints.append(t)

            # Typical order: food_source_id, measure_id, repeat_item_id
            if len(large_ints) >= 3:
                item["food_source_id"] = large_ints[0]
                item["measure_id"] = large_ints[1]
                item["repeat_item_id"] = large_ints[2]
            elif len(large_ints) == 2:
                item["food_source_id"] = large_ints[0]
                item["measure_id"] = large_ints[1]

            items.append(item)

        return items
