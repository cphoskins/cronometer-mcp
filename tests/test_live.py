"""Live smoke tests against a real Cronometer account.

Run with ``pytest -m live``; needs CRONOMETER_USERNAME and CRONOMETER_PASSWORD.

Cronometer rations its CSV export per account (HTTP 429 for hours once
exceeded, which also breaks the MCP's own reads), so a run makes exactly two
export calls, both for TEST_DAY: servings and biometrics. Both must be real
CSVs with no rows before anything is written; other entry types (notes,
exercises) aren't checked, since that would take more exports and the writes
only remove the ids they added. Writes touch TEST_DAY only and are checked by
add-then-remove of the returned id, without further exports. The first failure
stops the writes. A failed remove is never cleaned up automatically; the next
run's empty check refuses to write until a person has looked.
"""

import csv
import io
import os
from datetime import date, timedelta

import pytest

from cronometer_mcp.client import CronometerClient

pytestmark = pytest.mark.live

TEST_DAY = date(2000, 1, 1)
TODAY = date.today()
WEEK_AGO = TODAY - timedelta(days=7)


@pytest.fixture(scope="module")
def c():
    client = CronometerClient()
    client.authenticate()
    return client


def refuse_parallel_run():
    # Each xdist worker would make its own two exports.
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.fail("Live tests must not run in parallel: export budget is per run.")


@pytest.fixture(scope="module")
def test_day_exports(c):
    """The run's only two exports: (servings CSV text, biometrics CSV text)."""
    refuse_parallel_run()
    return (
        c.export_raw("servings", TEST_DAY, TEST_DAY),
        c.export_raw("biometrics", TEST_DAY, TEST_DAY),
    )


def _rows(csv_text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(csv_text)))


def require_clean_day(servings_csv: str, biometrics_csv: str) -> None:
    """Fail unless both exports are real CSVs with no rows."""
    for name, text in (("servings", servings_csv), ("biometrics", biometrics_csv)):
        if "Day" not in next(csv.reader(io.StringIO(text)), []):
            pytest.fail(f"The {name} export has no Day column. Not writing.")
    servings, biometrics = _rows(servings_csv), _rows(biometrics_csv)
    if servings or biometrics:
        pytest.fail(
            f"Leftover entries on {TEST_DAY} (servings={len(servings)}, "
            f"biometrics={len(biometrics)}); clean up first. Not writing."
        )


def write_round_trips(c) -> None:
    """Add then remove one serving, then one biometric. Stops at the first failure."""
    food = c.find_foods("egg")[0]
    added = c.add_serving(
        food_id=food["food_id"], food_source_id=food["food_source_id"],
        measure_id=0, quantity=50, weight_grams=50, day=TEST_DAY,
    )
    assert added["serving_id"]
    assert c.remove_serving(added["serving_id"])

    biometric_id = c.add_biometric("weight", 150, TEST_DAY)
    assert biometric_id, "addBiometric succeeded but its id could not be parsed"
    assert c.remove_biometric(biometric_id)


# ── Reads ─────────────────────────────────────────────────────────────


def test_servings_export_columns(test_day_exports):
    header = next(csv.reader(io.StringIO(test_day_exports[0])), [])
    assert {"Day", "Group", "Food Name", "Amount"} <= set(header)


def test_food_search_and_details(c):
    hits = c.find_foods("egg")
    assert hits and {"food_id", "food_source_id", "name"} <= hits[0].keys()
    assert c.get_food(hits[0]["food_source_id"])


def test_diary_groups_parse(c):
    # diary_groups() falls back to defaults on a parse failure; catch that here.
    groups = c._fetch_diary_groups()
    assert groups and all("name" in g and "wire_index" in g for g in groups)


def test_recent_biometrics(c):
    assert isinstance(c.get_recent_biometrics(), list)


def test_macro_targets(c):
    assert isinstance(c.get_all_macro_schedules(), list)
    assert c.get_daily_macro_targets(TODAY)
    assert isinstance(c.get_macro_target_templates(), list)


def test_fasting(c):
    assert isinstance(c.get_user_fasts_for_range(WEEK_AGO, TODAY), list)
    assert isinstance(c.get_fasting_stats(), dict)


def test_repeated_items(c):
    assert isinstance(c.get_repeated_items(), list)


# ── Writes (TEST_DAY only) ────────────────────────────────────────────


def test_write_round_trips(c, test_day_exports):
    require_clean_day(*test_day_exports)
    write_round_trips(c)
