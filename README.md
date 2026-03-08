# cronometer-mcp

<!-- mcp-name: io.github.cphoskins/cronometer -->

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that provides access to your [Cronometer](https://cronometer.com/) nutrition data. Pull detailed food logs, daily macro/micro summaries, manage diary entries, fasting, biometrics, and recurring foods — all from Claude, Cursor, or any MCP-compatible client.

**Requires a Cronometer Gold account** (or any paid tier that supports web login).

## Features

- **Food log** — individual food entries with full macro and micronutrient breakdown
- **Daily nutrition** — daily calorie, protein, carb, fat, and fiber totals
- **Micronutrients** — detailed vitamin/mineral breakdown with period averages
- **Diary management** — add/remove food entries, copy entire days, mark days complete
- **Recurring foods** — create, list, and delete repeat items that auto-log on selected days
- **Macro targets** — read/write daily targets, create templates, set weekly schedules
- **Fasting** — view history and stats, cancel or delete fasts
- **Biometrics** — log and remove weight, blood glucose, heart rate, body fat
- **Raw CSV export** — servings, daily summary, exercises, biometrics, or notes
- **Sync to disk** — download JSON exports and generate a markdown food log

## Quick Start

### 1. Install

```bash
pip install cronometer-mcp
```

Or install from source:

```bash
git clone https://github.com/cphoskins/cronometer-mcp.git
cd cronometer-mcp
pip install -e .
```

### 2. Set credentials

```bash
export CRONOMETER_USERNAME="your@email.com"
export CRONOMETER_PASSWORD="your-password"
```

Or add them to a `.env` file in your project root (if your MCP client supports it).

### 3. Configure your MCP client

#### Claude Code (`.mcp.json`)

```json
{
  "mcpServers": {
    "cronometer": {
      "command": "cronometer-mcp",
      "env": {
        "CRONOMETER_USERNAME": "your@email.com",
        "CRONOMETER_PASSWORD": "your-password"
      }
    }
  }
}
```

#### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "cronometer": {
      "command": "cronometer-mcp",
      "env": {
        "CRONOMETER_USERNAME": "your@email.com",
        "CRONOMETER_PASSWORD": "your-password"
      }
    }
  }
}
```

If you installed from source with `pip install -e .`, you can also use the full Python path:

```json
{
  "command": "/path/to/venv/bin/python",
  "args": ["-m", "cronometer_mcp.server"]
}
```

## Available Tools

### Food Log & Nutrition

| Tool | Description |
|------|-------------|
| `get_food_log` | Individual food entries with macros + micros for a date range |
| `get_daily_nutrition` | Daily macro totals (calories, protein, carbs, fat, fiber) |
| `get_micronutrients` | Detailed vitamin/mineral breakdown with period averages |
| `export_raw_csv` | Raw CSV export for any data type (servings, exercises, biometrics, etc.) |

### Food Search & Diary Management

| Tool | Description |
|------|-------------|
| `search_foods` | Search the Cronometer food database |
| `get_food_details` | Get full nutrition info and serving measure IDs for a food |
| `add_food_entry` | Add a food entry to the diary |
| `remove_food_entry` | Remove a food entry from the diary |
| `copy_day` | Copy all diary entries from one date to another |
| `set_day_complete` | Mark a diary day as complete or incomplete |

### Recurring Foods (Repeat Items)

| Tool | Description |
|------|-------------|
| `get_repeated_items` | List all recurring food entries |
| `add_repeat_item` | Add a recurring food entry that auto-logs on selected days |
| `delete_repeat_item` | Delete a recurring food entry |

### Macro Targets & Templates

| Tool | Description |
|------|-------------|
| `get_macro_targets` | Get daily macro targets (or full weekly schedule with `target_date="all"`) |
| `set_macro_targets` | Update daily macro targets (partial updates supported) |
| `list_macro_templates` | List all saved macro target templates |
| `create_macro_template` | Create a new saved macro target template |
| `set_weekly_macro_schedule` | Assign a template to days of the week as the recurring default |

### Fasting

| Tool | Description |
|------|-------------|
| `get_fasting_history` | View all fasts or fasts within a date range |
| `get_fasting_stats` | Aggregate fasting statistics (total hours, longest, averages) |
| `cancel_active_fast` | Cancel an in-progress fast while preserving the recurring schedule |
| `delete_fast` | Delete a fast entry |

### Biometrics

| Tool | Description |
|------|-------------|
| `get_recent_biometrics` | Get recently logged biometric entries |
| `add_biometric` | Log weight (lbs), blood glucose (mg/dL), heart rate (bpm), or body fat (%) |
| `remove_biometric` | Remove a biometric entry |

### Sync

| Tool | Description |
|------|-------------|
| `sync_cronometer` | Download JSON exports + generate food-log.md to disk |

### Tool Parameters

All date parameters use `YYYY-MM-DD` format. Most tools default to today or the last 7 days when dates are omitted.

Key parameter patterns:
- `diary_group` — one of `"Breakfast"`, `"Lunch"`, `"Dinner"`, `"Snacks"` (case-insensitive)
- `days_of_week` — `"all"`, `"weekdays"`, `"weekends"`, or comma-separated day numbers (`0`=Sun through `6`=Sat)
- `measure_id` — pass `0` to use the universal gram-based measure (works for all food sources)
- `target_date` — pass `"all"` on `get_macro_targets` to get the full weekly schedule

### Sync Output

The `sync_cronometer` tool saves files to `~/.local/share/cronometer-mcp/` by default. Override with the `CRONOMETER_DATA_DIR` environment variable:

```bash
export CRONOMETER_DATA_DIR="/path/to/your/project/data/cronometer"
```

Output files:
- `exports/servings_{start}_{end}.json`
- `exports/daily_summary_{start}_{end}.json`
- `exports/servings_latest.json`
- `exports/daily_summary_latest.json`
- `food-log.md`

## How It Works

Cronometer does not have a public API for individual users. This server uses the same GWT-RPC (Google Web Toolkit Remote Procedure Call) protocol that the Cronometer web app uses internally:

1. Fetches the login page to get an anti-CSRF token
2. POSTs credentials to authenticate
3. Calls GWT-RPC `authenticate` to get a user ID
4. Calls GWT-RPC `generateAuthorizationToken` for short-lived export tokens
5. Downloads CSV exports using the token
6. Calls GWT-RPC methods directly for diary edits, fasting, biometrics, macro targets, and repeat items

Session cookies are persisted to `~/.local/share/cronometer-mcp/.session_cookies` so that subsequent invocations reuse the session without re-authenticating (Cronometer has aggressive login rate limiting).

### GWT Magic Values

The GWT protocol uses a permutation hash and header value that are baked into each Cronometer web deploy. These values are hardcoded in the client and **may break when Cronometer pushes a new build**.

Current values (as of February 2026):
- Permutation: `7B121DC5483BF272B1BC1916DA9FA963`
- Header: `2D6A926E3729946302DC68073CB0D550`

If authentication starts failing with GWT errors, these values likely need updating. You can find the current values by:

1. Opening Cronometer in your browser
2. Going to Developer Tools → Network tab
3. Looking for requests to `cronometer.com/cronometer/app`
4. Checking the `x-gwt-permutation` header and the payload structure

You can override them via the `CronometerClient` constructor:

```python
from cronometer_mcp import CronometerClient

client = CronometerClient(
    gwt_permutation="NEW_PERMUTATION_HASH",
    gwt_header="NEW_HEADER_VALUE",
)
```

## Python API

You can also use the client directly in Python:

```python
from datetime import date, timedelta
from cronometer_mcp import CronometerClient

client = CronometerClient()  # reads from env vars

# Get today's food log
foods = client.get_food_log()

# Get last 7 days of daily summaries
start = date.today() - timedelta(days=7)
summaries = client.get_daily_summary(start)

# Raw CSV export
csv_text = client.export_raw("exercises", start, date.today())

# Copy a day's diary entries
client.copy_day(date(2026, 3, 1), date(2026, 3, 8))

# Add a recurring food entry (every weekday)
client.add_repeat_item(
    food_source_id=12345,
    measure_id=0,        # universal gram-based measure
    quantity=200,
    food_name="Oatmeal",
    diary_group=1,       # Breakfast
    days_of_week=[1, 2, 3, 4, 5],
)

# Log a biometric
client.add_biometric("weight", 218.5, date.today())
```

## License

MIT
