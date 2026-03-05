"""MCP server for Cronometer nutrition data."""

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import CronometerClient
from .markdown import generate_food_log_md

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "cronometer",
    instructions=(
        "Cronometer MCP server for nutrition tracking. "
        "Provides access to detailed food logs, daily macro/micro summaries, "
        "exercise data, and biometrics from Cronometer Gold. "
        "Use get_food_log for individual food entries with full nutrition, "
        "get_daily_nutrition for daily macro totals, and get_micronutrients "
        "for detailed vitamin/mineral breakdowns."
    ),
)

_client: CronometerClient | None = None


def _get_client() -> CronometerClient:
    global _client
    if _client is None:
        _client = CronometerClient()
    return _client


def _parse_date(d: str | None) -> date | None:
    if d is None:
        return None
    return date.fromisoformat(d)


# Non-nutrient metadata columns to exclude from nutrient extraction
_META_COLS = {
    "Day", "Date", "Time", "Group", "Food Name", "Amount", "Unit",
    "Category", "Completed",
}

# Macro columns (energy + macronutrients)
_MACRO_KEYWORDS = {
    "Energy", "Protein", "Carbs", "Fat", "Fiber", "Net Carbs",
    "Sugars", "Sugar Alcohol", "Starch", "Saturated", "Monounsaturated",
    "Polyunsaturated", "Trans-Fats", "Cholesterol", "Sodium", "Potassium",
    "Water", "Alcohol", "Caffeine", "Omega-3", "Omega-6",
}

# Amino acid columns
_AMINO_KEYWORDS = {
    "Cystine", "Histidine", "Isoleucine", "Leucine", "Lysine",
    "Methionine", "Phenylalanine", "Threonine", "Tryptophan",
    "Tyrosine", "Valine",
}


def _classify_column(col: str) -> str:
    """Classify a column as 'meta', 'macro', 'amino', or 'micro'."""
    if col in _META_COLS:
        return "meta"
    base = col.split("(")[0].strip()
    if base in _MACRO_KEYWORDS:
        return "macro"
    if base in _AMINO_KEYWORDS:
        return "amino"
    return "micro"


def _extract_nutrients(row: dict, category: str | None = None) -> dict:
    """Extract nutrient values from a row, optionally filtered by category."""
    result = {}
    for col, val in row.items():
        if _classify_column(col) == "meta":
            continue
        if category and _classify_column(col) != category:
            continue
        val = str(val).strip()
        if val:
            try:
                num = float(val)
                if num != 0.0:
                    result[col] = round(num, 2)
            except ValueError:
                pass
    return result


def _format_servings(rows: list[dict]) -> list[dict]:
    """Format servings export into a cleaner structure."""
    formatted = []
    for row in rows:
        entry = {
            "date": row.get("Day", ""),
            "time": row.get("Time", ""),
            "meal": row.get("Group", ""),
            "food": row.get("Food Name", ""),
            "amount": row.get("Amount", ""),
            "category": row.get("Category", ""),
            "macros": _extract_nutrients(row, "macro"),
            "micros": _extract_nutrients(row, "micro"),
        }
        formatted.append(entry)
    return formatted


@mcp.tool()
def get_food_log(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get detailed food log with individual food entries and full nutrition.

    Returns every food entry with macros and micronutrients.
    Great for analyzing what was eaten and spotting nutrient gaps.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        rows = client.get_food_log(start, end)
        formatted = _format_servings(rows)

        # Group by date
        by_date: dict[str, list] = {}
        for entry in formatted:
            d = entry["date"]
            by_date.setdefault(d, []).append(entry)

        return json.dumps({
            "status": "success",
            "date_range": {
                "start": start_date or str(date.today()),
                "end": end_date or str(date.today()),
            },
            "total_entries": len(formatted),
            "days": {
                d: {
                    "entries": entries,
                    "total_calories": round(sum(
                        e["macros"].get("Energy (kcal)", 0) for e in entries
                    ), 1),
                    "total_protein": round(sum(
                        e["macros"].get("Protein (g)", 0) for e in entries
                    ), 1),
                    "total_carbs": round(sum(
                        e["macros"].get("Carbs (g)", 0) for e in entries
                    ), 1),
                    "total_fat": round(sum(
                        e["macros"].get("Fat (g)", 0) for e in entries
                    ), 1),
                }
                for d, entries in by_date.items()
            },
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_daily_nutrition(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get daily nutrition summary with macro totals per day.

    Returns calorie, protein, carb, fat, and fiber totals for each day.
    Use this for quick daily overviews and trend analysis.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to 7 days ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or (date.today() - timedelta(days=7))
        end = _parse_date(end_date)
        rows = client.get_daily_summary(start, end)

        summaries = []
        for row in rows:
            summaries.append({
                "date": row.get("Date", ""),
                "macros": _extract_nutrients(row, "macro"),
                "micros": _extract_nutrients(row, "micro"),
            })

        return json.dumps({
            "status": "success",
            "date_range": {
                "start": str(start),
                "end": str(end or date.today()),
            },
            "days": summaries,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_micronutrients(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get detailed micronutrient breakdown for meal planning.

    Shows vitamins, minerals, and other micronutrients per day with
    period averages. Use this to identify nutrient gaps and plan meals.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to 7 days ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or (date.today() - timedelta(days=7))
        end = _parse_date(end_date)
        rows = client.get_daily_summary(start, end)

        days = []
        for row in rows:
            micros = _extract_nutrients(row, "micro")
            if micros:
                days.append({
                    "date": row.get("Date", ""),
                    "micronutrients": micros,
                })

        # Compute averages across the range
        averages = {}
        if days:
            all_keys = set()
            for d in days:
                all_keys.update(d["micronutrients"].keys())
            for key in sorted(all_keys):
                vals = [
                    d["micronutrients"][key]
                    for d in days
                    if key in d["micronutrients"]
                    and isinstance(d["micronutrients"][key], (int, float))
                ]
                if vals:
                    averages[key] = round(sum(vals) / len(vals), 2)

        return json.dumps({
            "status": "success",
            "date_range": {
                "start": str(start),
                "end": str(end or date.today()),
            },
            "daily_breakdown": days,
            "period_averages": averages,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def export_raw_csv(
    export_type: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export raw CSV data from Cronometer for any data type.

    Useful when you need the full unprocessed export.

    Args:
        export_type: One of 'servings', 'daily_summary', 'exercises',
                    'biometrics', 'notes'.
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        raw = client.export_raw(export_type, start, end)
        if len(raw) > 50000:
            return json.dumps({
                "status": "success",
                "truncated": True,
                "total_chars": len(raw),
                "data": raw[:50000] + "\n... (truncated)",
            })
        return json.dumps({"status": "success", "data": raw})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_DIARY_GROUP_MAP: dict[str, int] = {
    "breakfast": 1,
    "lunch": 2,
    "dinner": 3,
    "snacks": 4,
}


@mcp.tool()
def search_foods(query: str) -> str:
    """Search Cronometer's food database by name.

    Returns matching foods with their IDs and source information needed
    to add a serving (food_id, food_source_id, measure_id).

    Args:
        query: Food name or keyword to search for (e.g. "eggs", "chicken breast").
    """
    try:
        client = _get_client()
        foods = client.find_foods(query)
        return json.dumps(
            {
                "status": "success",
                "query": query,
                "count": len(foods),
                "foods": foods,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_food_details(food_source_id: int) -> str:
    """Get detailed food information including available serving measures.

    Use this after search_foods to get the measure_id needed for add_food_entry.
    Returns all available serving sizes with their numeric IDs and gram weights.

    Args:
        food_source_id: Food source ID from search_foods results.
    """
    try:
        client = _get_client()
        result = client.get_food(food_source_id)
        # Remove raw_response from the output to keep it clean
        output = {
            "status": "success",
            "food_source_id": result["food_source_id"],
            "measures": result["measures"],
        }
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def add_food_entry(
    food_id: int,
    food_source_id: int,
    weight_grams: float,
    date: str,
    measure_id: int = 0,
    quantity: float = 0,
    diary_group: str = "Breakfast",
) -> str:
    """Add a food entry to the Cronometer diary.

    Use search_foods to find food_id and food_source_id, then
    get_food_details for measure_id and weight_grams.

    For CRDB/custom foods, you can omit measure_id (defaults to a
    universal NCCDB measure that works for all food sources).
    When measure_id is omitted, quantity is set to weight_grams.

    Args:
        food_id: Numeric food ID from search_foods results.
        food_source_id: Food source ID from search_foods results.
        weight_grams: Weight of the serving in grams.
        date: Date to log the entry as YYYY-MM-DD (e.g. "2026-03-04").
        measure_id: Measure/unit ID. Pass 0 (default) to use the universal
                    measure that works for all food sources.
        quantity: Number of servings. Defaults to weight_grams when
                  measure_id is 0 (universal gram-based measure).
        diary_group: Meal slot — one of "Breakfast", "Lunch", "Dinner", "Snacks"
                     (case-insensitive, defaults to "Breakfast").
    """
    try:
        group_key = diary_group.strip().lower()
        group_int = _DIARY_GROUP_MAP.get(group_key)
        if group_int is None:
            return json.dumps({
                "status": "error",
                "message": (
                    f"Invalid diary_group '{diary_group}'. "
                    "Must be one of: Breakfast, Lunch, Dinner, Snacks."
                ),
            })

        if measure_id == 0 and quantity == 0:
            quantity = weight_grams

        from datetime import date as date_type
        log_date = date_type.fromisoformat(date)

        client = _get_client()
        result = client.add_serving(
            food_id=food_id,
            food_source_id=food_source_id,
            measure_id=measure_id,
            quantity=quantity,
            weight_grams=weight_grams,
            day=log_date,
            diary_group=group_int,
        )
        return json.dumps({
            "status": "success",
            "entry": result,
            "note": (
                "Use the serving_id to remove this entry with remove_food_entry "
                "if needed."
            ),
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def remove_food_entry(serving_id: str) -> str:
    """Remove a food entry from the Cronometer diary.

    Args:
        serving_id: The serving ID returned by add_food_entry (e.g. "D80lp$").
    """
    try:
        client = _get_client()
        client.remove_serving(serving_id)
        return json.dumps({
            "status": "success",
            "serving_id": serving_id,
            "message": "Serving removed from diary.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


def _get_data_dir() -> Path:
    """Get the data directory for sync output.

    Uses CRONOMETER_DATA_DIR env var if set, otherwise defaults to
    ~/.local/share/cronometer-mcp/.
    """
    env_dir = os.environ.get("CRONOMETER_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "cronometer-mcp"


@mcp.tool()
def sync_cronometer(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 14,
    diet_label: str | None = None,
) -> str:
    """Download Cronometer data and save locally as JSON + food-log.md.

    Downloads servings and daily summary data, saves JSON exports,
    and regenerates food-log.md.

    Output directory defaults to ~/.local/share/cronometer-mcp/ but can
    be overridden with the CRONOMETER_DATA_DIR environment variable.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to `days` ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
        days: Number of days to look back if start_date not specified (default 14).
        diet_label: Optional diet label for the markdown header (e.g., "Keto Rigorous").
    """
    try:
        client = _get_client()

        end = _parse_date(end_date) or date.today()
        start = _parse_date(start_date) or (end - timedelta(days=days))

        # Download both exports
        servings = client.get_food_log(start, end)
        daily_summary = client.get_daily_summary(start, end)

        # Save to data directory
        data_dir = _get_data_dir()
        exports_dir = data_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        servings_path = exports_dir / f"servings_{start}_{end}.json"
        servings_path.write_text(json.dumps(servings, indent=2))

        summary_path = exports_dir / f"daily_summary_{start}_{end}.json"
        summary_path.write_text(json.dumps(daily_summary, indent=2))

        # Also save a "latest" copy for easy access
        latest_servings = exports_dir / "servings_latest.json"
        latest_servings.write_text(json.dumps(servings, indent=2))

        latest_summary = exports_dir / "daily_summary_latest.json"
        latest_summary.write_text(json.dumps(daily_summary, indent=2))

        # Generate food-log.md
        food_log_path = data_dir / "food-log.md"
        md_content = generate_food_log_md(
            servings, daily_summary, start, end, diet_label=diet_label,
        )
        food_log_path.write_text(md_content)

        return json.dumps({
            "status": "success",
            "date_range": {"start": str(start), "end": str(end)},
            "servings_count": len(servings),
            "days_count": len(daily_summary),
            "files_saved": [
                str(servings_path),
                str(summary_path),
                str(latest_servings),
                str(latest_summary),
                str(food_log_path),
            ],
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
