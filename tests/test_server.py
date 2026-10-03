"""Tests for MCP tool wrappers in cronometer_mcp.server."""

import asyncio
import importlib
import json
from unittest.mock import MagicMock, patch

import pytest

from cronometer_mcp import server


# Every tool that changes data in the Cronometer account.
WRITE_TOOLS = {
    "add_food_entry", "remove_food_entry",
    "set_macro_targets", "set_weekly_macro_schedule", "create_macro_template",
    "delete_fast", "cancel_active_fast",
    "add_biometric", "remove_biometric",
    "copy_day", "set_day_complete",
    "add_repeat_item", "delete_repeat_item",
}


def _tool_names(env: dict) -> set[str]:
    with patch.dict("os.environ", env):
        importlib.reload(server)
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


class TestReadOnlyMode:
    @pytest.fixture(autouse=True)
    def _restore_default_registration(self):
        yield
        with patch.dict("os.environ", {"CRONOMETER_READ_ONLY": ""}):
            importlib.reload(server)

    def test_default_registers_write_tools(self):
        names = _tool_names({"CRONOMETER_READ_ONLY": ""})
        assert WRITE_TOOLS <= names
        assert len(names) == 27

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_read_only_drops_every_write_tool(self, value):
        names = _tool_names({"CRONOMETER_READ_ONLY": value})
        assert not names & WRITE_TOOLS
        assert len(names) == 27 - len(WRITE_TOOLS)
        assert {"get_food_log", "search_foods", "get_macro_targets",
                "sync_cronometer"} <= names

    @pytest.mark.parametrize("value", ["0", "false", "no"])
    def test_falsy_values_keep_write_tools(self, value):
        assert WRITE_TOOLS <= _tool_names({"CRONOMETER_READ_ONLY": value})


class TestSetMacroTargets:
    def _client(self, current: dict) -> MagicMock:
        client = MagicMock()
        client.get_daily_macro_targets.return_value = current
        return client

    def test_refuses_to_fill_an_unset_macro_with_zero(self):
        """Issue #5: an unset calorie target must not be written back as 0."""
        client = self._client({
            "protein_g": 150.0, "fat_g": 70.0, "calories": None,
            "carbs_g": 40.0, "template_name": "Fast Day",
        })
        with patch.object(server, "_get_client", return_value=client):
            result = json.loads(server.set_macro_targets(protein_grams=160))
        assert result["status"] == "error"
        assert "calories" in result["message"]
        client.update_daily_targets.assert_not_called()

    def test_explicit_value_fills_the_unset_macro(self):
        client = self._client({
            "protein_g": 150.0, "fat_g": 70.0, "calories": None,
            "carbs_g": 40.0, "template_name": "Fast Day",
        })
        with patch.object(server, "_get_client", return_value=client):
            result = json.loads(
                server.set_macro_targets(protein_grams=160, calories=1900)
            )
        assert result["status"] == "success"
        kwargs = client.update_daily_targets.call_args.kwargs
        assert kwargs["protein_g"] == 160
        assert kwargs["calories"] == 1900
        assert kwargs["fat_g"] == 70.0
