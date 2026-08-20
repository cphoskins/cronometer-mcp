"""Tests for diary group parsing and resolution.

Fixtures here are synthetic. The real authenticate response also carries
weight, height, macro targets and the user id, so a captured one must never
be committed to this repo.
"""

import json
from unittest.mock import MagicMock

import pytest

from cronometer_mcp.client import CronometerClient
from cronometer_mcp.server import _resolve_diary_group


STRING_TYPE_REF = "54"


def build_settings_response(settings: dict[str, str]) -> str:
    """Build a synthetic authenticate() response carrying a settings map.

    Mirrors the real encoding: a flat ``type_ref, string_ref`` stream in which
    the value precedes its key, with a string table at the end. Strings are
    de-duplicated, which is what breaks naive adjacency for shared values like
    "true"/"false".
    """
    table: list[str] = ["java.lang.String/2004016611"]
    # pad so the type ref lands on 54, matching the real payload's layout
    while len(table) < int(STRING_TYPE_REF):
        table.append(f"filler.{len(table)}")

    def ref(s: str) -> str:
        if s not in table:
            table.append(s)
        return str(table.index(s) + 1)

    tokens: list[str] = ["0", "0", "1"]
    for key, value in settings.items():
        tokens += [STRING_TYPE_REF, ref(value), STRING_TYPE_REF, ref(key)]

    return f'//OK[{",".join(tokens)},{json.dumps(table)},0,7]'


DEFAULT_ACCOUNT = {
    "DG01": "Group 1", "DG01ON": "false",
    "DG02": "Breakfast", "DG02ON": "true",
    "DG03": "Lunch", "DG03ON": "true",
    "DG04": "Dinner", "DG04ON": "true",
    "DG05": "Snacks", "DG05ON": "true",
    "DG06": "Group 6", "DG06ON": "false",
    "DG07": "Group 7", "DG07ON": "false",
    "DG08": "Group 8", "DG08ON": "false",
}

# Mirrors the account the protocol was verified against: an extra enabled
# group in the first slot, defaults pushed into DG02-DG05.
TEST_ACCOUNT = dict(DEFAULT_ACCOUNT, **{"DG01": "Test", "DG01ON": "true"})

# Every slot renamed, as reported by the user who filed the feature request.
RENAMED_ACCOUNT = {
    "DG01": "Data", "DG01ON": "true",
    "DG02": "Morning Fluids", "DG02ON": "true",
    "DG03": "Bfast", "DG03ON": "true",
    "DG04": "Second Bfast", "DG04ON": "true",
    "DG05": "Lunch", "DG05ON": "true",
    "DG06": "Dinner", "DG06ON": "true",
    "DG07": "Evening", "DG07ON": "true",
    "DG08": "Group 8", "DG08ON": "false",
}


class TestParseDiaryGroups:
    def test_parses_all_eight_slots(self):
        groups = CronometerClient._parse_diary_groups(
            build_settings_response(TEST_ACCOUNT)
        )
        assert len(groups) == 8
        assert [g["name"] for g in groups] == [
            "Test", "Breakfast", "Lunch", "Dinner", "Snacks",
            "Group 6", "Group 7", "Group 8",
        ]

    def test_wire_index_is_zero_based(self):
        """DG01 is wire 0. This is the whole crux of the feature."""
        groups = CronometerClient._parse_diary_groups(
            build_settings_response(TEST_ACCOUNT)
        )
        by_key = {g["settings_key"]: g for g in groups}
        assert by_key["DG01"]["wire_index"] == 0
        assert by_key["DG01"]["name"] == "Test"
        # Verified live: a serving written at wire 1 renders under Breakfast.
        assert by_key["DG02"]["wire_index"] == 1
        assert by_key["DG02"]["name"] == "Breakfast"

    def test_enable_flags_survive_string_dedup(self):
        """"true"/"false" are shared across keys, so adjacency alone fails."""
        groups = CronometerClient._parse_diary_groups(
            build_settings_response(TEST_ACCOUNT)
        )
        assert [g["enabled"] for g in groups] == [
            True, True, True, True, True, False, False, False
        ]

    def test_default_account_keeps_legacy_indices(self):
        """breakfast=1 ... snacks=4 must still hold on an untouched account."""
        groups = CronometerClient._parse_diary_groups(
            build_settings_response(DEFAULT_ACCOUNT)
        )
        by_name = {g["name"].lower(): g["wire_index"] for g in groups}
        assert by_name["breakfast"] == 1
        assert by_name["lunch"] == 2
        assert by_name["dinner"] == 3
        assert by_name["snacks"] == 4

    def test_returns_empty_when_no_dg_keys(self):
        assert CronometerClient._parse_diary_groups(
            build_settings_response({"weightInKG": "80.0"})
        ) == []

    def test_returns_empty_on_garbage(self):
        assert CronometerClient._parse_diary_groups("not a gwt response") == []


class TestDiaryGroupsFallback:
    def test_falls_back_to_legacy_defaults(self, monkeypatch):
        """A parse regression must degrade to old behavior, not block writes."""
        c = CronometerClient(username="u", password="p")
        monkeypatch.setattr(c, "authenticate", lambda: None)
        c._diary_groups = []

        groups = c.diary_groups

        assert [(g["wire_index"], g["name"]) for g in groups] == [
            (1, "Breakfast"), (2, "Lunch"), (3, "Dinner"), (4, "Snacks")
        ]


def _client_with(settings: dict[str, str]):
    groups = CronometerClient._parse_diary_groups(build_settings_response(settings))
    client = MagicMock()
    client.diary_groups = groups
    return client


class TestResolveDiaryGroup:
    def test_resolves_name_case_and_whitespace_insensitively(self):
        c = _client_with(TEST_ACCOUNT)
        for value in ("test", "Test", "  TEST  "):
            assert _resolve_diary_group(c, value) == (0, None)

    def test_resolves_breakfast_to_wire_one(self):
        assert _resolve_diary_group(_client_with(TEST_ACCOUNT), "Breakfast") == (1, None)

    def test_resolves_explicit_index(self):
        assert _resolve_diary_group(_client_with(TEST_ACCOUNT), 0) == (0, None)
        assert _resolve_diary_group(_client_with(TEST_ACCOUNT), "4") == (4, None)

    @pytest.mark.parametrize("bad", [8, 9, -1, 99])
    def test_rejects_out_of_range_index(self, bad):
        """Cronometer accepts these silently and the entry becomes unreachable."""
        idx, err = _resolve_diary_group(_client_with(TEST_ACCOUNT), bad)
        assert idx is None
        assert "out of range" in err

    def test_rejects_disabled_group_by_index(self):
        idx, err = _resolve_diary_group(_client_with(TEST_ACCOUNT), 6)
        assert idx is None
        assert "not enabled" in err

    def test_rejects_disabled_group_by_name(self):
        idx, err = _resolve_diary_group(_client_with(TEST_ACCOUNT), "Group 7")
        assert idx is None
        assert "not enabled" in err

    def test_unknown_name_errors_and_lists_real_groups(self):
        idx, err = _resolve_diary_group(_client_with(TEST_ACCOUNT), "Elevenses")
        assert idx is None
        assert "Elevenses" in err
        assert "Test" in err and "Breakfast" in err

    def test_breakfast_does_not_silently_hit_a_renamed_slot(self):
        """The reporter's bug: his DG02 is 'Morning Fluids', not Breakfast.

        Resolving "Breakfast" to wire 1 on his account would file breakfast
        into morning fluids, which is exactly what used to happen.
        """
        idx, err = _resolve_diary_group(_client_with(RENAMED_ACCOUNT), "Breakfast")
        assert idx is None
        assert "Morning Fluids" in err

    def test_renamed_account_can_use_its_own_names(self):
        c = _client_with(RENAMED_ACCOUNT)
        assert _resolve_diary_group(c, "Data") == (0, None)
        assert _resolve_diary_group(c, "Bfast") == (2, None)
        assert _resolve_diary_group(c, "Evening") == (6, None)


class TestTimeOfDayDefault:
    @pytest.mark.parametrize("hour,expected", [(8, 1), (13, 2), (19, 3), (23, 4)])
    def test_default_account_boundaries_unchanged(self, hour, expected, monkeypatch):
        import cronometer_mcp.server as server
        monkeypatch.setattr(
            server, "_default_group_name",
            lambda: {1: "breakfast", 2: "lunch", 3: "dinner", 4: "snacks"}[expected],
        )
        assert _resolve_diary_group(_client_with(DEFAULT_ACCOUNT), None) == (expected, None)

    def test_never_selects_a_disabled_group(self, monkeypatch):
        import cronometer_mcp.server as server
        disabled = dict(DEFAULT_ACCOUNT, **{"DG02ON": "false"})
        monkeypatch.setattr(server, "_default_group_name", lambda: "breakfast")

        idx, err = _resolve_diary_group(_client_with(disabled), None)

        assert idx is None
        assert "cannot be applied" in err

    def test_renamed_account_errors_rather_than_guessing(self, monkeypatch):
        import cronometer_mcp.server as server
        monkeypatch.setattr(server, "_default_group_name", lambda: "breakfast")

        idx, err = _resolve_diary_group(_client_with(RENAMED_ACCOUNT), None)

        assert idx is None
        assert "explicitly" in err

    def test_empty_string_treated_as_omitted(self, monkeypatch):
        import cronometer_mcp.server as server
        monkeypatch.setattr(server, "_default_group_name", lambda: "breakfast")
        assert _resolve_diary_group(_client_with(DEFAULT_ACCOUNT), "") == (1, None)


class TestAddRepeatItemWiring:
    """add_repeat_item shares the resolver with add_food_entry.

    Exercised with a mock rather than live: get_repeated_items currently
    parses repeat_item_id as 0, so delete_repeat_item cannot remove a
    created item, and a repeat item recurs every day. Not worth creating
    one that cannot be cleaned up.
    """

    def _patched(self, monkeypatch, settings=TEST_ACCOUNT):
        import cronometer_mcp.server as server
        client = _client_with(settings)
        client.add_repeat_item = MagicMock(return_value=True)
        monkeypatch.setattr(server, "_get_client", lambda: client)
        return server, client

    def test_resolves_custom_group_name_to_wire_index(self, monkeypatch):
        server, client = self._patched(monkeypatch)

        result = json.loads(server.add_repeat_item(
            food_source_id=1, food_id=2, quantity=1, food_name="X",
            diary_group="Test",
        ))

        assert result["status"] == "success"
        assert client.add_repeat_item.call_args[1]["diary_group"] == 0

    def test_rejects_unknown_group_without_writing(self, monkeypatch):
        server, client = self._patched(monkeypatch)

        result = json.loads(server.add_repeat_item(
            food_source_id=1, food_id=2, quantity=1, food_name="X",
            diary_group="Elevenses",
        ))

        assert result["status"] == "error"
        client.add_repeat_item.assert_not_called()

    def test_rejects_disabled_group_without_writing(self, monkeypatch):
        server, client = self._patched(monkeypatch)

        result = json.loads(server.add_repeat_item(
            food_source_id=1, food_id=2, quantity=1, food_name="X",
            diary_group="Group 7",
        ))

        assert result["status"] == "error"
        client.add_repeat_item.assert_not_called()

    def test_renamed_account_rejects_assumed_breakfast(self, monkeypatch):
        server, client = self._patched(monkeypatch, RENAMED_ACCOUNT)

        result = json.loads(server.add_repeat_item(
            food_source_id=1, food_id=2, quantity=1, food_name="X",
            diary_group="Breakfast",
        ))

        assert result["status"] == "error"
        client.add_repeat_item.assert_not_called()
