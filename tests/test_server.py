"""Tests for MCP tool wrappers in cronometer_mcp.server."""

import json
from unittest.mock import MagicMock, patch

from cronometer_mcp import server


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
