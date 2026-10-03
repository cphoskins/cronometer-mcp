"""Exercise the installed entry point without credentials or network access."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


SERVER = """
from unittest.mock import Mock, patch
import cronometer_mcp.server as server
server._client = Mock()
server._client.get_daily_summary.return_value = [{
    "Date": "2026-10-01", "Completed": "true",
    "Energy (kcal)": "2100", "Protein (g)": "150",
    "Vitamin C (mg)": "90",
}]
server._client.export_raw.side_effect = lambda kind, *args: (
    "Date,Completed\\n2026-10-01,true\\n" if kind == "daily_summary"
    else "Day,Time,Metric,Amount,Unit\\n2026-10-01,22:00:00,Sleep (Fitbit),8,hr\\n"
)
with patch("requests.sessions.Session.request", side_effect=AssertionError("Network prohibited")):
    server.main()
"""


async def _capture(python):
    params = StdioServerParameters(
        command=python, args=["-c", SERVER],
        cwd=str(Path(python).parent.parent.parent),
        env={key: value for key, value in os.environ.items()
             if not key.startswith("CRONOMETER_")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "cronometer"
            tools = (await session.list_tools()).tools
            catalog = {
                tool.name: {
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                    "outputSchema": tool.output_schema,
                }
                for tool in tools
            }
            dates = {"start_date": "2026-10-01", "end_date": "2026-10-01"}
            results = {}
            for name, args in [
                ("get_daily_nutrition", dates),
                ("get_micronutrients", dates),
                ("export_raw_csv", {**dates, "export_type": "daily_summary"}),
                ("export_raw_csv", {**dates, "export_type": "biometrics"}),
            ]:
                result = await session.call_tool(name, args)
                assert not result.is_error
                body = json.loads(result.content[0].text)
                assert body["status"] == "success"
                results[f"{name}:{args.get('export_type', '')}"] = result.model_dump(
                    exclude_none=True
                )
            invalid = await session.call_tool("export_raw_csv", {})
            assert invalid.is_error
            return catalog, results


def test_stdio_catalog_and_dashboard_calls():
    with patch.dict(os.environ, {}, clear=True):
        catalog, results = asyncio.run(asyncio.wait_for(_capture(sys.executable), 20))
    assert len(catalog) == 27
    nutrition = results["get_daily_nutrition:"]["content"][0]["text"]
    assert json.loads(nutrition)["days"][0]["macros"]["Protein (g)"] == 150


def test_mcp1_wire_parity():
    baseline = os.environ.get("MCP1_BASELINE_PYTHON")
    if not baseline:
        import pytest
        pytest.skip("Set MCP1_BASELINE_PYTHON to compare against the original release")
    with patch.dict(os.environ, {}, clear=True):
        current = asyncio.run(asyncio.wait_for(_capture(sys.executable), 20))
        original = asyncio.run(asyncio.wait_for(_capture(baseline), 20))
    assert current == original
