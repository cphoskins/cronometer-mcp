"""Tests for the Cronometer client (mocked, no credentials needed)."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import date

from cronometer_mcp.client import CronometerClient, EXPORT_TYPES, UNIVERSAL_MEASURE_ID


@pytest.fixture
def client():
    """Create a client with dummy credentials."""
    return CronometerClient(username="test@example.com", password="testpass")


class TestClientInit:
    def test_creates_with_explicit_creds(self):
        c = CronometerClient(username="a@b.com", password="pw")
        assert c.username == "a@b.com"
        assert c.password == "pw"
        assert not c._authenticated

    def test_raises_without_creds(self):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="credentials required"):
                CronometerClient()

    def test_reads_env_vars(self):
        env = {"CRONOMETER_USERNAME": "env@test.com", "CRONOMETER_PASSWORD": "envpw"}
        with patch.dict("os.environ", env, clear=True):
            c = CronometerClient()
            assert c.username == "env@test.com"
            assert c.password == "envpw"

    def test_custom_gwt_values(self):
        c = CronometerClient(
            username="a@b.com", password="pw",
            gwt_permutation="CUSTOM_PERM",
            gwt_header="CUSTOM_HDR",
        )
        assert c.gwt_permutation == "CUSTOM_PERM"
        assert c.gwt_header == "CUSTOM_HDR"


class TestAuthentication:
    def test_get_anticsrf(self, client):
        mock_resp = MagicMock()
        mock_resp.text = '<input name="anticsrf" value="token123">'
        client.session.get = MagicMock(return_value=mock_resp)

        token = client._get_anticsrf()
        assert token == "token123"

    def test_get_anticsrf_missing(self, client):
        mock_resp = MagicMock()
        mock_resp.text = "<html>no token here</html>"
        client.session.get = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="anti-CSRF"):
            client._get_anticsrf()

    def test_login_success_redirect(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"redirect": "https://cronometer.com/"}
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value="nonce123")

        client._login("csrf_token")
        assert client.nonce == "nonce123"

    def test_login_success_flag(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True}
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value="nonce456")

        client._login("csrf_token")
        assert client.nonce == "nonce456"

    def test_login_error(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "Invalid credentials"}
        client.session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Invalid credentials"):
            client._login("csrf_token")

    def test_login_no_nonce(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"redirect": "https://cronometer.com/"}
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="sesnonce"):
            client._login("csrf_token")

    def test_gwt_authenticate(self, client):
        mock_resp = MagicMock()
        mock_resp.text = "//OK[12345,1,['some','data'],0,7]"
        client.session.post = MagicMock(return_value=mock_resp)
        client.session.cookies = MagicMock()
        client.session.cookies.get = MagicMock(return_value="new_nonce")

        client._gwt_authenticate()
        assert client.user_id == "12345"
        assert client.nonce == "new_nonce"

    def test_gwt_authenticate_failure(self, client):
        mock_resp = MagicMock()
        mock_resp.text = "//EX[something went wrong]"
        client.session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="GWT authenticate failed"):
            client._gwt_authenticate()

    def test_generate_auth_token(self, client):
        client.nonce = "test_nonce"
        client.user_id = "12345"

        mock_resp = MagicMock()
        mock_resp.text = '//OK["abc-token-123",0,7]'
        client.session.post = MagicMock(return_value=mock_resp)

        token = client._generate_auth_token()
        assert token == "abc-token-123"

    def test_authenticate_full_flow(self, client):
        with patch.object(client, "_restore_session", return_value=False) as m0, \
             patch.object(client, "_discover_gwt_hashes") as md, \
             patch.object(client, "_get_anticsrf", return_value="csrf") as m1, \
             patch.object(client, "_login") as m2, \
             patch.object(client, "_gwt_authenticate") as m3, \
             patch.object(client, "_save_session") as m4:
            client.authenticate()
            m0.assert_called_once()
            md.assert_called_once()
            m1.assert_called_once()
            m2.assert_called_once_with("csrf")
            m3.assert_called_once()
            m4.assert_called_once()
            assert client._authenticated

    def test_authenticate_restores_session(self, client):
        with patch.object(client, "_restore_session", return_value=True) as m0, \
             patch.object(client, "_get_anticsrf") as m1:
            client.authenticate()
            m0.assert_called_once()
            m1.assert_not_called()
            assert client._authenticated

    def test_authenticate_skips_if_already_done(self, client):
        client._authenticated = True
        with patch.object(client, "_get_anticsrf") as m:
            client.authenticate()
            m.assert_not_called()


class TestExports:
    def test_export_types_mapping(self):
        assert EXPORT_TYPES["servings"] == "servings"
        assert EXPORT_TYPES["daily_summary"] == "dailySummary"
        assert EXPORT_TYPES["exercises"] == "exercises"
        assert EXPORT_TYPES["biometrics"] == "biometrics"
        assert EXPORT_TYPES["notes"] == "notes"

    def test_export_parsed(self, client):
        csv_data = "Day,Food Name,Amount\n2026-01-01,Eggs,2.00 large\n"
        with patch.object(client, "export_raw", return_value=csv_data):
            rows = client.export_parsed("servings", date(2026, 1, 1))
            assert len(rows) == 1
            assert rows[0]["Food Name"] == "Eggs"
            assert rows[0]["Amount"] == "2.00 large"


# ---------------------------------------------------------------------------
# Helper to build synthetic GWT-RPC findFoods responses for unit tests.
# ---------------------------------------------------------------------------

def _build_find_foods_response(foods: list[dict]) -> str:
    """Build a minimal //OK[...] findFoods response from a list of food specs.

    Each food spec dict must have:
        name, measure_desc, food_id, food_source_id, score, keywords

    The string table is populated with class names first, then data strings
    deduplicated in insertion order.  Data tokens are assembled to match the
    field layout documented in the GWT-RPC spec.
    """
    # Canonical class names that always appear in the string table.
    class_names = [
        "java.util.ArrayList/4159755760",
        "com.cronometer.shared.foods.SearchHit/1606796888",
        "com.cronometer.shared.foods.FoodSource/4236433762",
        "com.cronometer.shared.foods.FoodType/3105214803",
    ]

    # Collect data strings in insertion order (locale always "en").
    data_strings: list[str] = []

    def _intern(s: str) -> int:
        """Return 1-based string table index, inserting if absent."""
        combined = class_names + data_strings
        if s in combined:
            return combined.index(s) + 1
        data_strings.append(s)
        return len(class_names) + len(data_strings)  # 1-based

    # Pre-compute string table refs for each food.
    food_refs = []
    for food in foods:
        locale_ref = _intern("en")
        measure_ref = _intern(food["measure_desc"])
        name_ref = _intern(food["name"])
        kw_ref = _intern(food["keywords"])
        food_refs.append(
            {
                "score": food["score"],
                "name_ref": name_ref,
                "food_id": food["food_id"],
                "measure_ref": measure_ref,
                "locale_ref": locale_ref,
                "food_source_id": food["food_source_id"],
                "popularity": food.get("popularity", 1000000),
                "kw_ref": kw_ref,
            }
        )

    string_table = class_names + data_strings
    searchhit_type_idx = 2  # always index 2 in class_names

    # Build data tokens.
    # Header: <string_table_size>, 0, <ArrayList type ref=1>
    data_tokens: list[int] = [len(string_table), 0, 1]
    # Number of SearchHit items.
    data_tokens.append(len(foods))

    for refs in food_refs:
        data_tokens += [
            refs["score"],
            0,                      # flags
            refs["name_ref"],
            refs["food_id"],
            refs["measure_ref"],
            refs["locale_ref"],
            refs["food_source_id"],
            refs["popularity"],
            refs["kw_ref"],
            searchhit_type_idx,     # SearchHit class ref
            3,                      # FoodSource class ref
            0,                      # FoodSource ordinal
            4,                      # FoodType class ref
            0,                      # FoodType ordinal
        ]

    import json as _json
    st_json = _json.dumps(string_table)
    tokens_str = ",".join(str(t) for t in data_tokens)
    return f"//OK[{tokens_str},{st_json},0,7]"


class TestParseFindFoods:
    """Unit tests for CronometerClient._parse_find_foods (no network calls)."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse(self, raw: str) -> list[dict]:
        return CronometerClient._parse_find_foods(raw)

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_three_results_from_docstring_example(self):
        """Validate the concrete 3-result 'EGGS COOKED' example from the spec."""
        string_table = [
            "java.util.ArrayList/4159755760",
            "com.cronometer.shared.foods.SearchHit/1606796888",
            "com.cronometer.shared.foods.FoodSource/4236433762",
            "com.cronometer.shared.foods.FoodType/3105214803",
            "en",
            "1 large - 50g",
            "Egg, whole, cooked, hard-boiled",
            "egg whole cooked hard boiled",
            "1 large - 50g",
            "Egg, whole, cooked, scrambled",
            "egg whole cooked scrambled",
            "1 large (61g) - 91g",
            "Egg, whole, cooked, omelet",
            "egg whole cooked omelet",
        ]
        import json as _json
        st_json = _json.dumps(string_table)

        # Data tokens assembled manually per spec:
        # header: 14, 0, 7 (ArrayList type=7? — using 1 per helper convention,
        # but for this test we replicate the raw example numbers exactly)
        # SearchHit 1: score=100,flags=0,name_ref=7,food_id=1072102,
        #              measure_ref=6,locale_ref=5,src_id=464674,
        #              pop=1010000,kw_ref=8,type=2, then 3,0,4,0
        # SearchHit 2: score=100,...,type=2, then -3,0,-1,0
        # SearchHit 3: score=100,...,type=2, then -7,0,-5,0
        data_tokens = (
            "14,0,7,3,"
            "100,0,7,1072102,6,5,464674,1010000,8,2,3,0,4,0,"
            "100,0,10,1072101,9,5,464674,1009000,11,2,-3,0,-1,0,"
            "100,0,13,1072100,12,5,464674,1006000,14,2,-7,0,-5,0"
        )
        raw = f"//OK[{data_tokens},{st_json},0,7]"

        results = self._parse(raw)

        assert len(results) == 3

        assert results[0] == {
            "food_id": 1072102,
            "food_source_id": 464674,
            "name": "Egg, whole, cooked, hard-boiled",
            "measure_desc": "1 large - 50g",
            "score": 100,
        }
        assert results[1] == {
            "food_id": 1072101,
            "food_source_id": 464674,
            "name": "Egg, whole, cooked, scrambled",
            "measure_desc": "1 large - 50g",
            "score": 100,
        }
        assert results[2] == {
            "food_id": 1072100,
            "food_source_id": 464674,
            "name": "Egg, whole, cooked, omelet",
            "measure_desc": "1 large (61g) - 91g",
            "score": 100,
        }

    def test_single_result(self):
        raw = _build_find_foods_response(
            [
                {
                    "name": "Chicken Breast",
                    "measure_desc": "1 oz",
                    "food_id": 999,
                    "food_source_id": 111,
                    "score": 95,
                    "keywords": "chicken breast",
                }
            ]
        )
        results = self._parse(raw)
        assert len(results) == 1
        r = results[0]
        assert r["food_id"] == 999
        assert r["food_source_id"] == 111
        assert r["name"] == "Chicken Breast"
        assert r["measure_desc"] == "1 oz"
        assert r["score"] == 95

    def test_multiple_results_via_builder(self):
        foods = [
            {
                "name": "Salmon, Atlantic, farmed",
                "measure_desc": "3 oz",
                "food_id": 10001,
                "food_source_id": 4001,
                "score": 100,
                "keywords": "salmon atlantic farmed",
            },
            {
                "name": "Salmon, Pacific, coho",
                "measure_desc": "3 oz",
                "food_id": 10002,
                "food_source_id": 4001,
                "score": 90,
                "keywords": "salmon pacific coho",
            },
            {
                "name": "Salmon, canned",
                "measure_desc": "1 can - 418g",
                "food_id": 10003,
                "food_source_id": 4001,
                "score": 80,
                "keywords": "salmon canned",
            },
        ]
        raw = _build_find_foods_response(foods)
        results = self._parse(raw)

        assert len(results) == 3
        assert results[0]["name"] == "Salmon, Atlantic, farmed"
        assert results[1]["name"] == "Salmon, Pacific, coho"
        assert results[2]["name"] == "Salmon, canned"
        assert results[0]["score"] == 100
        assert results[1]["score"] == 90
        assert results[2]["score"] == 80

    def test_returned_dict_keys(self):
        raw = _build_find_foods_response(
            [
                {
                    "name": "Tuna",
                    "measure_desc": "1 can",
                    "food_id": 42,
                    "food_source_id": 7,
                    "score": 88,
                    "keywords": "tuna",
                }
            ]
        )
        result = self._parse(raw)[0]
        assert set(result.keys()) == {
            "food_id", "food_source_id", "name", "measure_desc", "score"
        }

    def test_measure_desc_preserved(self):
        raw = _build_find_foods_response(
            [
                {
                    "name": "Eggs",
                    "measure_desc": "1 large (61g)",
                    "food_id": 1,
                    "food_source_id": 2,
                    "score": 100,
                    "keywords": "eggs",
                }
            ]
        )
        assert self._parse(raw)[0]["measure_desc"] == "1 large (61g)"

    def test_food_id_and_source_are_integers(self):
        raw = _build_find_foods_response(
            [
                {
                    "name": "Spinach",
                    "measure_desc": "1 cup",
                    "food_id": 123456,
                    "food_source_id": 654321,
                    "score": 77,
                    "keywords": "spinach",
                }
            ]
        )
        result = self._parse(raw)[0]
        assert isinstance(result["food_id"], int)
        assert isinstance(result["food_source_id"], int)
        assert isinstance(result["score"], int)
        assert result["food_id"] == 123456
        assert result["food_source_id"] == 654321

    # ------------------------------------------------------------------
    # Zero results
    # ------------------------------------------------------------------

    def test_zero_results_returns_empty_list(self):
        raw = _build_find_foods_response([])
        results = self._parse(raw)
        assert results == []

    def test_zero_results_no_searchhit_in_string_table(self):
        """A string table with no SearchHit entry → empty list."""
        import json as _json
        st = ["java.util.ArrayList/4159755760", "some.other.Class/12345"]
        raw = f'//OK[2,0,1,{_json.dumps(st)},0,7]'
        assert self._parse(raw) == []

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_raises_on_non_ok_prefix(self):
        with pytest.raises(ValueError, match="Unexpected GWT-RPC"):
            self._parse("//EX[some error]")

    def test_raises_on_missing_closing_suffix(self):
        with pytest.raises(ValueError, match="does not end with"):
            self._parse('//OK[1,["x"],0,6]')  # wrong closing number

    def test_raises_on_invalid_json_string_table(self):
        # Corrupt the string table JSON.
        raw = '//OK[1,[broken json,0,7]'
        with pytest.raises(Exception):
            self._parse(raw)

    # ------------------------------------------------------------------
    # Robustness / edge cases
    # ------------------------------------------------------------------

    def test_stray_type_ref_near_start_is_skipped(self):
        """A SearchHit type index appearing before 9 tokens are available
        must be silently skipped, not cause an IndexError."""
        import json as _json
        # Put the SearchHit type ref (2) as the very first data token.
        st = [
            "java.util.ArrayList/4159755760",
            "com.cronometer.shared.foods.SearchHit/1606796888",
        ]
        raw = f'//OK[2,{_json.dumps(st)},0,7]'
        # The token '2' at index 0 cannot look back 9 positions → skip it.
        assert self._parse(raw) == []

    def test_names_with_commas_and_special_chars(self):
        """Food names containing commas, hyphens, and parentheses survive
        the round-trip through the JSON string table correctly."""
        foods = [
            {
                "name": "Beef, ground, 80% lean / 20% fat (patty)",
                "measure_desc": "3 oz - 85g",
                "food_id": 55555,
                "food_source_id": 22222,
                "score": 100,
                "keywords": "beef ground 80 lean 20 fat patty",
            }
        ]
        raw = _build_find_foods_response(foods)
        result = self._parse(raw)[0]
        assert result["name"] == "Beef, ground, 80% lean / 20% fat (patty)"
        assert result["measure_desc"] == "3 oz - 85g"

    def test_deduplication_of_shared_measure_desc(self):
        """Two foods with the same measure_desc string share a single string
        table entry; both must still parse correctly."""
        foods = [
            {
                "name": "Apple",
                "measure_desc": "1 medium",
                "food_id": 1,
                "food_source_id": 10,
                "score": 100,
                "keywords": "apple",
            },
            {
                "name": "Pear",
                "measure_desc": "1 medium",
                "food_id": 2,
                "food_source_id": 10,
                "score": 90,
                "keywords": "pear",
            },
        ]
        raw = _build_find_foods_response(foods)
        results = self._parse(raw)
        assert len(results) == 2
        assert results[0]["measure_desc"] == "1 medium"
        assert results[1]["measure_desc"] == "1 medium"
        assert results[0]["name"] == "Apple"
        assert results[1]["name"] == "Pear"


class TestFindFoodsIntegration:
    """Tests for find_foods() — verifies it calls _parse_find_foods and returns
    a list[dict] instead of a raw string."""

    def test_find_foods_returns_list(self, client):
        raw = _build_find_foods_response(
            [
                {
                    "name": "Broccoli",
                    "measure_desc": "1 cup",
                    "food_id": 300,
                    "food_source_id": 100,
                    "score": 95,
                    "keywords": "broccoli",
                }
            ]
        )
        client._authenticated = True
        client.nonce = "n"
        client.user_id = "42"
        client.session.post = MagicMock(
            return_value=MagicMock(
                text=raw, raise_for_status=lambda: None
            )
        )

        results = client.find_foods("broccoli")

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["name"] == "Broccoli"
        assert results[0]["food_id"] == 300

    def test_find_foods_zero_results(self, client):
        raw = _build_find_foods_response([])
        client._authenticated = True
        client.nonce = "n"
        client.user_id = "42"
        client.session.post = MagicMock(
            return_value=MagicMock(
                text=raw, raise_for_status=lambda: None
            )
        )

        results = client.find_foods("xyzzy_no_match")
        assert results == []

    def test_find_foods_uppercases_query(self, client):
        """Verify the GWT-RPC body is sent with an uppercased query."""
        raw = _build_find_foods_response([])
        client._authenticated = True
        client.nonce = "n"
        client.user_id = "42"
        post_mock = MagicMock(
            return_value=MagicMock(
                text=raw, raise_for_status=lambda: None
            )
        )
        client.session.post = post_mock

        client.find_foods("chicken breast")

        call_body = post_mock.call_args[1].get("data") or post_mock.call_args[0][1]
        assert "CHICKEN BREAST" in call_body


# ---------------------------------------------------------------------------
# Helper to build synthetic GWT-RPC getFood responses for unit tests.
# ---------------------------------------------------------------------------

def _build_get_food_response(measures: list[dict], include_derived: bool = False) -> str:
    """Build a minimal //OK[...] getFood response with Measure objects.

    Each measure dict must have: description, measure_id, food_source_id, weight_grams
    """
    import json as _json

    class_names = [
        "com.cronometer.shared.foods.models.Food/1234567890",
        "com.cronometer.shared.foods.models.Measure/2345678901",
        "com.cronometer.shared.foods.models.Measure$Type/3456789012",
    ]
    if include_derived:
        class_names.append(
            "com.cronometer.shared.measurement.DerivedMeasure/9876543210"
        )

    data_strings: list[str] = []

    def _intern(s: str) -> int:
        combined = class_names + data_strings
        if s in combined:
            return combined.index(s) + 1
        data_strings.append(s)
        return len(class_names) + len(data_strings)

    measure_type_idx = 2  # Measure class is at index 2 (1-based)
    measure_subtype_idx = 3  # Measure$Type

    # Build data tokens: some food metadata, then Measure objects.
    # Food metadata prefix (simplified): string_table_size, 0, food_type_ref=1
    data_tokens: list = []

    for m in measures:
        desc_ref = _intern(m["description"])
        # Layout: weight_grams, ..., Measure$Type ref, ...,
        #         desc_ref, 0, measure_id, food_source_id, 0, 1.0, Measure type ref
        data_tokens += [
            m["weight_grams"],      # weight_grams (float)
            0,                       # padding
            measure_subtype_idx,     # Measure$Type ref
            0,                       # ordinal
            desc_ref,                # description (i-6 from Measure type ref)
            0,                       # flags (i-5)
            m["measure_id"],         # measure_id (i-4)
            m["food_source_id"],     # food_source_id (i-3)
            0,                       # flags (i-2)
            1.0,                     # quantity (i-1)
            measure_type_idx,        # Measure type ref (i)
        ]

    if include_derived:
        derived_type_idx = len(class_names)  # 1-based
        data_tokens += [
            100.0, 0, measure_subtype_idx, 0,
            _intern("mL"), 0, 999999, 12345, 0, 1.0,
            derived_type_idx,
        ]

    string_table = class_names + data_strings
    st_json = _json.dumps(string_table)

    # Convert tokens: floats with decimal, ints without
    token_strs = []
    for t in data_tokens:
        if isinstance(t, float):
            token_strs.append(str(t))
        else:
            token_strs.append(str(t))
    tokens_str = ",".join(token_strs)

    return f"//OK[{tokens_str},{st_json},0,7]"


class TestParseGetFood:
    """Unit tests for CronometerClient._parse_get_food (no network calls)."""

    def _parse(self, raw: str, fsid: int = 12345) -> dict:
        return CronometerClient._parse_get_food(raw, fsid)

    def test_single_nccdb_measure(self):
        raw = _build_get_food_response([
            {"description": "1 large - 50g", "measure_id": 65541,
             "food_source_id": 464674, "weight_grams": 50.0},
        ])
        result = self._parse(raw)
        assert len(result["measures"]) == 1
        m = result["measures"][0]
        assert m["measure_id"] == 65541
        assert m["description"] == "1 large - 50g"
        assert m["weight_grams"] == 50.0

    def test_multiple_measures(self):
        raw = _build_get_food_response([
            {"description": "1 cup", "measure_id": 100,
             "food_source_id": 500, "weight_grams": 240.0},
            {"description": "1 tbsp", "measure_id": 101,
             "food_source_id": 500, "weight_grams": 15.0},
        ])
        result = self._parse(raw)
        assert len(result["measures"]) == 2
        ids = {m["measure_id"] for m in result["measures"]}
        assert ids == {100, 101}

    def test_derived_measure_excluded(self):
        """DerivedMeasure entries must NOT appear in the measures list."""
        raw = _build_get_food_response(
            [{"description": "1 tbsp", "measure_id": 200,
              "food_source_id": 500, "weight_grams": 14.0}],
            include_derived=True,
        )
        result = self._parse(raw)
        # Should only have the real Measure, not the DerivedMeasure
        ids = [m["measure_id"] for m in result["measures"]]
        assert 200 in ids
        assert 999999 not in ids

    def test_invalid_response_returns_empty(self):
        result = self._parse("//EX[error]", 12345)
        assert result["measures"] == []

    def test_no_measure_in_string_table(self):
        import json as _json
        st = ["com.cronometer.shared.foods.models.Food/123"]
        raw = f'//OK[1,0,{_json.dumps(st)},0,7]'
        result = self._parse(raw)
        assert result["measures"] == []

    def test_food_source_id_echoed(self):
        raw = _build_get_food_response([
            {"description": "1 oz", "measure_id": 300,
             "food_source_id": 55985, "weight_grams": 28.35},
        ])
        result = self._parse(raw, fsid=55985)
        assert result["food_source_id"] == 55985


class TestAddServing:
    """Tests for add_serving — universal measure fallback and response parsing."""

    def _make_client(self):
        c = CronometerClient(username="test@x.com", password="pw")
        c._authenticated = True
        c.nonce = "n"
        c.user_id = "42"
        return c

    def _mock_update_response(self, serving_id="D9TEST", food_id=502518, fsid=55985):
        return (
            f'//OK[0,0,{food_id},"{serving_id}",{fsid},170.0,2107848,0,'
            f'124399,0,1,1,2026,3,5,4,3,2,1,1,'
            f'["java.util.ArrayList/4159755760"],0,7]'
        )

    def test_universal_measure_fallback(self):
        """measure_id=0 should auto-select UNIVERSAL_MEASURE_ID."""
        c = self._make_client()
        resp_text = self._mock_update_response()
        c.session.post = MagicMock(
            return_value=MagicMock(text=resp_text, raise_for_status=lambda: None)
        )

        result = c.add_serving(
            food_id=502518, food_source_id=55985,
            measure_id=0, quantity=170, weight_grams=170,
            day=date(2026, 3, 5),
        )

        # Verify the GWT body uses UNIVERSAL_MEASURE_ID
        call_body = c.session.post.call_args[1].get("data") or c.session.post.call_args[0][1]
        assert str(UNIVERSAL_MEASURE_ID) in call_body
        assert result["serving_id"] == "D9TEST"

    def test_explicit_measure_id_not_overridden(self):
        """Non-zero measure_id should be used as-is."""
        c = self._make_client()
        resp_text = self._mock_update_response()
        c.session.post = MagicMock(
            return_value=MagicMock(text=resp_text, raise_for_status=lambda: None)
        )

        c.add_serving(
            food_id=502518, food_source_id=55985,
            measure_id=65541, quantity=4, weight_grams=200,
            day=date(2026, 3, 5),
        )

        call_body = c.session.post.call_args[1].get("data") or c.session.post.call_args[0][1]
        assert "|65541|" in call_body

    def test_response_parsing(self):
        c = self._make_client()
        resp_text = self._mock_update_response(
            serving_id="D9FRtZ", food_id=176206122, fsid=53718799
        )
        c.session.post = MagicMock(
            return_value=MagicMock(text=resp_text, raise_for_status=lambda: None)
        )

        result = c.add_serving(
            food_id=176206122, food_source_id=53718799,
            measure_id=0, quantity=14, weight_grams=14,
            day=date(2026, 3, 5),
        )
        assert result["serving_id"] == "D9FRtZ"
        assert result["food_id"] == 176206122
        assert result["food_source_id"] == 53718799

    def test_diary_group_in_body(self):
        """diary_group should appear in the GWT body."""
        c = self._make_client()
        resp_text = self._mock_update_response()
        c.session.post = MagicMock(
            return_value=MagicMock(text=resp_text, raise_for_status=lambda: None)
        )

        c.add_serving(
            food_id=502518, food_source_id=55985,
            measure_id=0, quantity=170, weight_grams=170,
            day=date(2026, 3, 5), diary_group=2,
        )

        call_body = c.session.post.call_args[1].get("data") or c.session.post.call_args[0][1]
        # quantity|diary_group|0|measure_id pattern
        assert "|2|0|" in call_body

    def test_integer_quantity_no_decimal(self):
        """Integer quantities should be sent without decimal point."""
        c = self._make_client()
        resp_text = self._mock_update_response()
        c.session.post = MagicMock(
            return_value=MagicMock(text=resp_text, raise_for_status=lambda: None)
        )

        c.add_serving(
            food_id=502518, food_source_id=55985,
            measure_id=0, quantity=170.0, weight_grams=170.0,
            day=date(2026, 3, 5),
        )

        call_body = c.session.post.call_args[1].get("data") or c.session.post.call_args[0][1]
        # Should contain "170|" not "170.0|"
        assert "170|" in call_body


class TestRemoveServing:
    def test_remove_success(self):
        c = CronometerClient(username="test@x.com", password="pw")
        c._authenticated = True
        c.nonce = "n"
        c.user_id = "42"
        c.session.post = MagicMock(
            return_value=MagicMock(
                text="//OK[[],0,7]", raise_for_status=lambda: None
            )
        )

        result = c.remove_serving("D9TEST")
        assert result is True

        call_body = c.session.post.call_args[1].get("data") or c.session.post.call_args[0][1]
        assert "D9TEST" in call_body

    def test_remove_failure(self):
        c = CronometerClient(username="test@x.com", password="pw")
        c._authenticated = True
        c.nonce = "n"
        c.user_id = "42"
        c.session.post = MagicMock(
            return_value=MagicMock(
                text="//EX[error removing]", raise_for_status=lambda: None
            )
        )

        with pytest.raises(RuntimeError):
            c.remove_serving("D9BAD")


class TestSessionPersistence:
    def test_save_and_restore(self, client, tmp_path):
        """Session save/restore round-trip."""
        cookie_path = tmp_path / ".session_cookies"
        client._cookie_path = cookie_path
        client.nonce = "saved_nonce"
        client.user_id = "12345"
        client.gwt_permutation = "PERM123"
        client.gwt_header = "HDR456"
        client.session.cookies.set("sesnonce", "saved_nonce")

        client._save_session()
        assert cookie_path.exists()

        # Create a new client and restore
        c2 = CronometerClient(username="test@x.com", password="pw")
        c2._cookie_path = cookie_path

        with patch.object(c2, "_discover_gwt_hashes"), \
             patch.object(c2, "_generate_auth_token", return_value="token"):
            restored = c2._restore_session()

        assert restored is True
        assert c2.nonce == "saved_nonce"
        assert c2.user_id == "12345"
        assert c2.gwt_permutation == "PERM123"
        assert c2.gwt_header == "HDR456"

    def test_restore_missing_file(self, client, tmp_path):
        client._cookie_path = tmp_path / "nonexistent"
        assert client._restore_session() is False

    def test_restore_invalid_session_deletes_file(self, client, tmp_path):
        """If session validation fails, the cookie file should be deleted."""
        cookie_path = tmp_path / ".session_cookies"
        client._cookie_path = cookie_path
        client.nonce = "old"
        client.user_id = "1"
        client.session.cookies.set("sesnonce", "old")
        client._save_session()

        c2 = CronometerClient(username="test@x.com", password="pw")
        c2._cookie_path = cookie_path

        with patch.object(c2, "_discover_gwt_hashes"), \
             patch.object(c2, "_generate_auth_token", side_effect=RuntimeError("expired")):
            restored = c2._restore_session()

        assert restored is False
        assert not cookie_path.exists()
