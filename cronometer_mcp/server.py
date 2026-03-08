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


@mcp.tool()
def get_macro_targets(
    target_date: str | None = None,
) -> str:
    """Get current daily macro targets from Cronometer.

    Returns the effective macro targets (protein, fat, carbs, calories)
    and the template name for a specific date or all days of the week.

    Args:
        target_date: Date as YYYY-MM-DD to get targets for (defaults to today).
                     Pass "all" to get the full weekly schedule.
    """
    try:
        client = _get_client()

        if target_date == "all":
            schedules = client.get_all_macro_schedules()
            return json.dumps({
                "status": "success",
                "type": "weekly_schedule",
                "schedules": schedules,
            }, indent=2)

        day = _parse_date(target_date)
        targets = client.get_daily_macro_targets(day)
        return json.dumps({
            "status": "success",
            "type": "daily_targets",
            "date": target_date or str(date.today()),
            "targets": targets,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def set_macro_targets(
    protein_grams: float | None = None,
    fat_grams: float | None = None,
    carbs_grams: float | None = None,
    calories: float | None = None,
    target_date: str | None = None,
    template_name: str | None = None,
) -> str:
    """Update daily macro targets in Cronometer.

    Reads current targets first, then updates only the provided values.
    Omitted values remain unchanged.

    Args:
        protein_grams: Protein target in grams.
        fat_grams: Fat target in grams.
        carbs_grams: Net carbs target in grams.
        calories: Calorie target in kcal.
        target_date: Date as YYYY-MM-DD (defaults to today).
        template_name: Template name (defaults to "Custom Targets").
    """
    try:
        from datetime import date as date_type

        client = _get_client()
        day = date_type.fromisoformat(target_date) if target_date else date.today()

        # Read current targets to preserve unchanged values
        current = client.get_daily_macro_targets(day)

        new_protein = protein_grams if protein_grams is not None else current["protein_g"]
        new_fat = fat_grams if fat_grams is not None else current["fat_g"]
        new_carbs = carbs_grams if carbs_grams is not None else current["carbs_g"]
        new_calories = calories if calories is not None else current["calories"]
        name = template_name or "Custom Targets"

        client.update_daily_targets(
            day=day,
            protein_g=new_protein,
            fat_g=new_fat,
            carbs_g=new_carbs,
            calories=new_calories,
            template_name=name,
        )

        # Read back to confirm
        updated = client.get_daily_macro_targets(day)
        return json.dumps({
            "status": "success",
            "date": str(day),
            "previous": current,
            "updated": updated,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday",
              "Thursday", "Friday", "Saturday"]


@mcp.tool()
def set_weekly_macro_schedule(
    template_name: str,
    days: str = "all",
) -> str:
    """Set the recurring weekly macro schedule by assigning a template to days.

    This updates the DEFAULT schedule that applies to all future dates,
    not just a specific date override.

    First finds the template by name (from existing saved templates or
    from a recently created per-date template), then assigns it to the
    specified days of the week.

    Args:
        template_name: Name of a saved macro target template
                       (e.g. "Retatrutide GI-Optimized", "Keto Rigorous").
        days: Comma-separated day names or "all" (default).
              E.g. "Monday,Wednesday,Friday" or "all".
    """
    try:
        client = _get_client()

        # Get available templates
        templates = client.get_macro_target_templates()
        template_map = {t["template_name"]: t for t in templates}

        if template_name not in template_map:
            return json.dumps({
                "status": "error",
                "message": f"Template '{template_name}' not found.",
                "available_templates": [t["template_name"] for t in templates],
            }, indent=2)

        template_id = template_map[template_name]["template_id"]

        # Parse which days to update
        if days.strip().lower() == "all":
            target_days = list(range(7))  # 0=Sun through 6=Sat (US ordering)
        else:
            day_name_map = {name.lower(): i for i, name in enumerate(_DOW_NAMES)}
            target_days = []
            for d in days.split(","):
                d = d.strip().lower()
                if d in day_name_map:
                    target_days.append(day_name_map[d])
                else:
                    return json.dumps({
                        "status": "error",
                        "message": f"Invalid day name: '{d}'",
                        "valid_days": _DOW_NAMES,
                    }, indent=2)

        # Apply template to each day
        results = []
        for dow in target_days:
            client.save_macro_schedule(dow, template_id)
            results.append({
                "day": _DOW_NAMES[dow],
                "template_name": template_name,
                "template_id": template_id,
            })

        # Read back the full schedule to confirm
        updated_schedule = client.get_all_macro_schedules()

        return json.dumps({
            "status": "success",
            "days_updated": results,
            "current_schedule": updated_schedule,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def list_macro_templates() -> str:
    """List all saved macro target templates in Cronometer.

    Returns template names, IDs, and their macro values.
    Use this to find the template_name for set_weekly_macro_schedule.
    """
    try:
        client = _get_client()
        templates = client.get_macro_target_templates()
        return json.dumps({
            "status": "success",
            "count": len(templates),
            "templates": templates,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def create_macro_template(
    template_name: str,
    protein_grams: float,
    fat_grams: float,
    carbs_grams: float,
    calories: float,
    assign_to_all_days: bool = False,
) -> str:
    """Create a new saved macro target template in Cronometer.

    Optionally assigns it to all days of the week as the recurring default.

    Args:
        template_name: Name for the new template (e.g. "Retatrutide GI-Optimized").
        protein_grams: Protein target in grams.
        fat_grams: Fat target in grams.
        carbs_grams: Net carbs target in grams.
        calories: Calorie target in kcal.
        assign_to_all_days: If True, also set this as the recurring weekly
                            schedule for all 7 days (default False).
    """
    try:
        client = _get_client()

        # Check if template already exists
        existing = client.get_macro_target_templates()
        for t in existing:
            if t["template_name"] == template_name:
                return json.dumps({
                    "status": "error",
                    "message": (
                        f"Template '{template_name}' already exists "
                        f"(id={t['template_id']}). Use set_weekly_macro_schedule "
                        "to assign it to days."
                    ),
                    "existing_template": t,
                }, indent=2)

        # Create the template
        template_id = client.save_macro_target_template(
            template_name=template_name,
            protein_g=protein_grams,
            fat_g=fat_grams,
            carbs_g=carbs_grams,
            calories=calories,
        )

        result = {
            "status": "success",
            "template_name": template_name,
            "template_id": template_id,
            "macros": {
                "protein_g": protein_grams,
                "fat_g": fat_grams,
                "carbs_g": carbs_grams,
                "calories": calories,
            },
        }

        # Optionally assign to all days
        if assign_to_all_days and template_id:
            for dow in range(7):
                client.save_macro_schedule(dow, template_id)
            schedule = client.get_all_macro_schedules()
            result["weekly_schedule_updated"] = True
            result["current_schedule"] = schedule

        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_fasting_history(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get fasting history from Cronometer.

    Returns all fasts (or fasts within a date range) with their status,
    names, recurrence rules, and timestamps.

    Args:
        start_date: Start date as YYYY-MM-DD (omit for all history).
        end_date: End date as YYYY-MM-DD (omit for all history).
    """
    try:
        client = _get_client()

        if start_date and end_date:
            start = _parse_date(start_date)
            end = _parse_date(end_date)
            fasts = client.get_user_fasts_for_range(start, end)
        else:
            fasts = client.get_user_fasts()

        active = [f for f in fasts if f.get("is_active")]
        completed = [f for f in fasts if not f.get("is_active")]

        return json.dumps({
            "status": "success",
            "total_fasts": len(fasts),
            "active_fasts": len(active),
            "completed_fasts": len(completed),
            "fasts": fasts,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_fasting_stats() -> str:
    """Get aggregate fasting statistics from Cronometer.

    Returns total fasting hours, longest fast, 7-fast average,
    and completed fast count.
    """
    try:
        client = _get_client()
        stats = client.get_fasting_stats()
        return json.dumps({
            "status": "success",
            "stats": stats,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def delete_fast(fast_id: int) -> str:
    """Delete a fast entry from Cronometer.

    Use get_fasting_history first to find the fast_id.

    Args:
        fast_id: The fast ID to delete.
    """
    try:
        client = _get_client()
        client.delete_fast(fast_id)
        return json.dumps({
            "status": "success",
            "fast_id": fast_id,
            "message": "Fast deleted.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def cancel_active_fast(fast_id: int) -> str:
    """Cancel an active (in-progress) fast while preserving the recurring schedule.

    Use get_fasting_history to find active fasts (is_active=true).

    Args:
        fast_id: The fast ID of the active fast to cancel.
    """
    try:
        client = _get_client()
        client.cancel_fast_keep_series(fast_id)
        return json.dumps({
            "status": "success",
            "fast_id": fast_id,
            "message": "Active fast cancelled. Recurring schedule preserved.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_recent_biometrics() -> str:
    """Get the most recently logged biometric entries from Cronometer.

    Returns recent values for weight, blood glucose, blood pressure,
    heart rate, body fat, and other tracked biometrics.
    """
    try:
        client = _get_client()
        biometrics = client.get_recent_biometrics()
        return json.dumps({
            "status": "success",
            "count": len(biometrics),
            "biometrics": biometrics,
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def add_biometric(
    metric_type: str,
    value: float,
    entry_date: str,
) -> str:
    """Add a biometric entry to Cronometer.

    Supported metric types: weight (lbs), blood_glucose (mg/dL),
    heart_rate (bpm), body_fat (%).

    Args:
        metric_type: One of 'weight', 'blood_glucose', 'heart_rate', 'body_fat'.
        value: The value in display units (lbs, mg/dL, bpm, %).
        entry_date: Date as YYYY-MM-DD.
    """
    try:
        from datetime import date as date_type

        client = _get_client()
        day = date_type.fromisoformat(entry_date)
        biometric_id = client.add_biometric(
            metric_type=metric_type,
            value=value,
            day=day,
        )
        return json.dumps({
            "status": "success",
            "metric_type": metric_type,
            "value": value,
            "date": entry_date,
            "biometric_id": biometric_id,
            "note": "Use biometric_id with remove_biometric to delete this entry.",
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def remove_biometric(biometric_id: str) -> str:
    """Remove a biometric entry from Cronometer.

    Use get_recent_biometrics to find biometric_id values.

    Args:
        biometric_id: The biometric entry ID (e.g. "BXW0DA").
    """
    try:
        client = _get_client()
        client.remove_biometric(biometric_id)
        return json.dumps({
            "status": "success",
            "biometric_id": biometric_id,
            "message": "Biometric entry removed.",
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
