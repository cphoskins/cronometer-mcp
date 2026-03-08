# Cronometer GWT-RPC Research: Copy Day, Repeat Items, Diary Operations

**Date:** 2026-03-08
**Method:** Playwright browser automation with network interception on https://cronometer.com

---

## Summary of Discovered GWT-RPC Methods

### 1. `copyDay` -- Copy All Entries From One Date to Another (SERVER-SIDE)

This is the most powerful method. It copies ALL diary entries from a source date to a destination date in a single server-side call.

**Request template:**
```
7|0|8|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
copyDay|java.lang.String/2004016611|
I|com.cronometer.shared.entries.models.Day/782579793|
{nonce}|
1|2|3|4|4|5|6|7|7|8|{user_id}|7|{src_day}|{src_month}|{src_year}|7|{dst_day}|{dst_month}|{dst_year}|
```

**Captured example (copy Mar 14 -> Mar 15):**
```
7|0|8|https://cronometer.com/cronometer/|76FC4464E20E53D16663AC9A96A486B3|com.cronometer.shared.rpc.CronometerService|copyDay|java.lang.String/2004016611|I|com.cronometer.shared.entries.models.Day/782579793|c78719cc094c992c69c122bd27287158|1|2|3|4|4|5|6|7|7|8|2107848|7|14|3|2026|7|15|3|2026|
```

**Parameters (positional after string table):**
- `{user_id}` = 2107848
- First Day: `7|{day}|{month}|{year}` = source date
- Second Day: `7|{day}|{month}|{year}` = destination date

**Response (empty source):**
```
//OK[0,1,["java.util.ArrayList/4159755760"],0,7]
```

**UI trigger:** Diary page > kebab menu (more_horiz) > "Copy Previous Day" or right-click food > "Copy to Today"

**Notes:**
- "Copy Previous Day" uses copyDay with src = (current_day - 1), dst = current_day
- "Copy to Today" (shown on past dates) uses copyDay with src = viewed_date, dst = today
- Copies ALL entries for the day (all diary groups: Breakfast, Lunch, Dinner, Snacks)
- Additive: does not clear existing entries on destination date

---

### 2. `editDiaryEntries` -- Paste Individual Items (CLIENT-SIDE COPY + SERVER PASTE)

Used when copying individual selected items and pasting them. The "Copy Selected Items" stores data client-side, and paste calls `editDiaryEntries`.

**Request template:**
```
7|0|11|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
editDiaryEntries|java.lang.String/2004016611|
java.util.List|I|{nonce}|
java.util.ArrayList/4159755760|
com.cronometer.shared.entries.models.Serving/2553599101|
com.cronometer.shared.entries.models.Day/782579793|
1|2|3|4|3|5|6|7|8|9|{count}|10|11|{day}|{month}|{year}|...serving fields...|{user_id}|
```

**Captured example (paste 1 Wasa crispbread serving to Mar 8):**
```
7|0|11|https://cronometer.com/cronometer/|76FC4464E20E53D16663AC9A96A486B3|com.cronometer.shared.rpc.CronometerService|editDiaryEntries|java.lang.String/2004016611|java.util.List|I|c78719cc094c992c69c122bd27287158|java.util.ArrayList/4159755760|com.cronometer.shared.entries.models.Serving/2553599101|com.cronometer.shared.entries.models.Day/782579793|1|2|3|4|3|5|6|7|8|9|1|10|11|8|3|2026|1|1|0|65540|0|2107848|28|461776|A|1055762|0|409412|2107848|
```

**Response:**
```
//OK[409412,0,1055762,"D9zN$G",461776,28.0,2107848,0,65540,0,1,1,2026,3,8,3,2,1,1,["java.util.ArrayList/4159755760","com.cronometer.shared.entries.models.Serving/2553599101","com.cronometer.shared.entries.models.Day/782579793"],0,7]
```

**Serving fields in the request:**
The Serving object fields after the Day: `{quantity}|{measure_id}|{serving_id_placeholder}|{food_source_id}|0|{food_category_id}|{user_id}|`

**UI trigger:** Right-click food entries > "Copy Selected Items" > navigate to new date > right-click > "Paste"

**Notes:**
- "Copy Selected Items" does NOT make an RPC call -- it stores selected serving data in GWT client memory (localStorage key `copyItems` was checked but empty; stored in GWT object state)
- "Copy Current Day" (from kebab menu) also stores all entries client-side for paste
- "Paste" calls `editDiaryEntries` with the stored servings targeting the current viewed date

---

### 3. `getDayInfo` -- Load All Diary Data for a Date

Returns all servings (food entries) for a given date with their IDs, quantities, and metadata.

**Request template:**
```
7|0|8|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
getDayInfo|java.lang.String/2004016611|
com.cronometer.shared.entries.models.Day/782579793|
I|{nonce}|
1|2|3|4|3|5|6|7|8|6|{day}|{month}|{year}|{user_id}|
```

**Response structure (Mar 7 with multiple entries):**
```
//OK[0,0,0,3,1,
  0,{food_category_id},"{serving_id}",{food_source_id},{quantity},{user_id},0,{diary_group_flags},0,1,1,{year},{month},{day},2,4,
  {food_category_id2},0,{food_source_id2},"{serving_id2}",{measure_id2},{quantity2},...
  ...more servings...
  ,{count},3,{year},{month},{day},2,0,1,
  ["com.cronometer.shared.entries.models.DayInfo/416556043",
   "com.cronometer.shared.entries.models.Day/782579793",
   "java.util.ArrayList/4159755760",
   "com.cronometer.shared.entries.models.Serving/2553599101"],0,7]
```

**Serving object fields (decoded from response):**
Each Serving in the response contains:
- `food_category_id` (e.g., 409412, 13263444, 0)
- `food_source_id` (e.g., 1055762, 998804)
- `serving_id` (string, e.g., "D9xgQv", "D9xf3f")
- `measure_id` (e.g., 461776, 450836, 5682887)
- `quantity` (e.g., 170.0, 42.0, 271.0)
- `user_id`
- `diary_group` encoded in flags (65537=Breakfast item 1, 65538=Breakfast item 2, 131073=Lunch item 1, etc.)
  - Group bits: 0x10000=Breakfast, 0x20000=Lunch, 0x30000=Dinner, etc.

---

### 4. `addRepeatItem` -- Create/Update a Repeating Food Entry

**Request template:**
```
7|0|11|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
addRepeatItem|java.lang.String/2004016611|
I|com.cronometer.shared.repeatitems.RepeatItem/477684891|
{nonce}|
java.util.ArrayList/4159755760|
java.lang.Integer/3438268394|
{food_name}|
1|2|3|4|3|5|6|7|8|{user_id}|7|3|9|{day_count}|10|{day_int}|...|0|{food_name_ref}|{quantity}|0|{measure_id}|{food_source_id}|0|
```

**Captured example (add Wasa to Monday):**
```
7|0|11|https://cronometer.com/cronometer/|76FC4464E20E53D16663AC9A96A486B3|com.cronometer.shared.rpc.CronometerService|addRepeatItem|java.lang.String/2004016611|I|com.cronometer.shared.repeatitems.RepeatItem/477684891|c78719cc094c992c69c122bd27287158|java.util.ArrayList/4159755760|java.lang.Integer/3438268394|Wasa, Crispbread, Multi Grain|1|2|3|4|3|5|6|7|8|2107848|7|3|9|1|10|1|0|11|1|0|461776|1055762|0|
```

**Parameters:**
- `{user_id}` = 2107848
- RepeatItem object (type ref 7) with:
  - Diary group = 3 (may encode Breakfast=1, Lunch=2, etc.)
  - Days list: `9|{count}|10|{day_int}|...` where day_int: 0=Sun, 1=Mon, 2=Tue, etc.
  - Food name reference (string table index 11 = "Wasa, Crispbread, Multi Grain")
  - quantity = 1 (1.0 serving)
  - measure_id = 461776
  - food_source_id = 1055762
  - food_category_id = 0

**Response:**
```
//OK[[],0,7]
```

---

### 5. `getRepeatedItems` -- List All Repeat Items

**Request template:**
```
7|0|7|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
getRepeatedItems|java.lang.String/2004016611|
I|{nonce}|
1|2|3|4|2|5|6|7|{user_id}|
```

**Response (with 1 item):**
```
//OK[0,1055762,461776,658384,1,4,0,1,3,1,1,3.0,2,1,1,
  ["java.util.ArrayList/4159755760",
   "com.cronometer.shared.repeatitems.RepeatItem/477684891",
   "java.lang.Integer/3438268394",
   "Wasa, Crispbread, Multi Grain"],0,7]
```

**RepeatItem fields in response:**
- `food_source_id` = 1055762
- `measure_id` = 461776
- `repeat_item_id` = 658384
- `day_of_week` = 1 (Monday, 0-indexed: 0=Sun, 1=Mon, ...)
- `diary_group` = 0 (Breakfast)
- `quantity` = 1 (count of items) / 3.0 (serving amount)
- Food name = "Wasa, Crispbread, Multi Grain"

**Response (empty):**
```
//OK[0,1,["java.util.ArrayList/4159755760"],0,7]
```

---

### 6. `deleteRepeatItem` -- Delete a Repeat Item

**Request template:**
```
7|0|7|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
deleteRepeatItem|java.lang.String/2004016611|
I|{nonce}|
1|2|3|4|3|5|6|6|7|{user_id}|{repeat_item_id}|
```

**Captured example:**
```
7|0|7|https://cronometer.com/cronometer/|76FC4464E20E53D16663AC9A96A486B3|com.cronometer.shared.rpc.CronometerService|deleteRepeatItem|java.lang.String/2004016611|I|c78719cc094c992c69c122bd27287158|1|2|3|4|3|5|6|6|7|2107848|658384|
```

**Parameters:**
- `{user_id}` = 2107848
- `{repeat_item_id}` = 658384

**Response:**
```
//OK[[],0,7]
```

**UI trigger:** Repeat Items page > item detail > kebab menu (more_horiz) > "Delete" > confirm "YES"

---

### 7. `areThereRepeatItemsToBeLoggedForDay` -- Check Pending Repeat Items

Called on page load to check if any repeat items need to be logged for the current date.

**Request template:**
```
7|0|7|https://cronometer.com/cronometer/|
{gwt_header}|
com.cronometer.shared.rpc.CronometerService|
areThereRepeatItemsToBeLoggedForDay|java.lang.String/2004016611|
I|{nonce}|
1|2|3|4|3|5|6|6|7|{user_id}|0|
```

**Response (false = no pending items):**
```
//OK[0,1,["java.lang.Boolean/476441737"],0,7]
```

The `0` before the string table = false. If true, the UI shows a "LOG ITEMS" button in the diary.

---

## Repeat Item Recurrence Types (from GWT source)

| Enum Value | Name | Display | Recurrence Rule |
|-----------|------|---------|----------------|
| 0 | NO_RECCURANCE | Not Repeating | null |
| 1 | DAY | Daily | (daily pattern) |
| 2 | WEEK | Every Week | FREQ=WEEKLY |
| 3 | TWO_WEEKS | Every Two Weeks | FREQ=WEEKLY;INTERVAL=2 |
| 4 | THREE_WEEKS | Every Three Weeks | FREQ=WEEKLY;INTERVAL=3 |
| 5 | FOUR_WEEKS | Every Four Weeks | FREQ=WEEKLY;INTERVAL=4 |

**Note:** The day-of-week selection uses: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat (matching Java Calendar convention).

---

## Repeat Items UI Details

The Repeat Items configuration page (accessible via Foods > Repeat Items in sidebar, or right-click food entry > "Repeat Item") allows:

- **Timestamp**: Hour, Minute, AM/PM + "Include time" toggle
- **Diary Group**: Breakfast / Lunch / Dinner / Snacks
- **Serving Size**: Amount + Measure dropdown
- **Day Selection**: Individual day toggles (Sun-Sat) + "All Days" shortcut
- **Actions**: Save, Revert Changes, Disable (via kebab), Delete (via kebab)

The kebab menu (more_horiz) on a repeat item detail page has:
- **Disable** -- disables without deleting
- **Delete** -- permanently removes (calls `deleteRepeatItem`)

---

## Diary Day-Level Context Menu

Accessed via the kebab menu (more_horiz) button in the diary header area:

| Menu Item | Description | Server Call |
|-----------|-------------|-------------|
| Mark Day Complete | Toggle day completion | (not captured) |
| Copy Previous Day | Copy all entries from (date-1) to current date | `copyDay(src=date-1, dst=date)` |
| Copy to Today | Copy current viewed date entries to today | `copyDay(src=viewed, dst=today)` |
| Copy Current Day | Store all entries in client memory for paste | Client-side only |
| Paste | Paste previously copied entries | `editDiaryEntries` |
| Clear All | Remove all entries for the day | (not captured) |
| Clear Amounts | Reset all serving amounts | (not captured) |
| Sort All By Time | Sort entries by timestamp | (not captured) |
| Diary Settings... | Open settings | (not captured) |

---

## Diary Entry-Level Context Menu

Accessed via right-click on a food entry row:

| Menu Item | Description | Server Call |
|-----------|-------------|-------------|
| View/Edit Selected Food | Open food editor | (navigation only) |
| Repeat Item | Navigate to repeat item setup | `getRepeatedItems` |
| Copy to Today | (shown on past dates) Copy item to today | `copyDay` or `editDiaryEntries` |
| Copy Selected Items | Store selected items in client memory | Client-side only |
| Create Recipe From Selected Items... | Create recipe | (not captured) |
| Create Meal From Selected Items... | Create meal | (not captured) |
| Paste | Paste items from clipboard | `editDiaryEntries` |
| Delete Selected Items | Remove selected entries | `removeServing` |

---

## Key GWT-RPC Constants (Current as of 2026-03-08)

| Constant | Value |
|----------|-------|
| GWT Permutation | 93261B906D607D730D0261397A065ADA |
| GWT Header (serialization policy) | 76FC4464E20E53D16663AC9A96A486B3 |
| GWT Module Base | https://cronometer.com/cronometer/ |
| GWT Content-Type | text/x-gwt-rpc; charset=UTF-8 |
| CronometerService | com.cronometer.shared.rpc.CronometerService |
| Serving class | com.cronometer.shared.entries.models.Serving/2553599101 |
| Day class | com.cronometer.shared.entries.models.Day/782579793 |
| DayInfo class | com.cronometer.shared.entries.models.DayInfo/416556043 |
| RepeatItem class | com.cronometer.shared.repeatitems.RepeatItem/477684891 |
| RepeatItemNotFound | com.cronometer.shared.repeatitems.RepeatItemNotFound/1835377525 |

---

## Other Captured Methods (Login/Load)

Methods captured during session initialization:
- `authenticate` - GWT auth, returns user_id
- `getNutrientInfo` - nutrient definitions
- `setTrackingId` - analytics
- `getMetrics` - user metrics/units config
- `getPublicStripeKey` - payment
- `flagActiveForUser` - activity tracking
- `setTimeZone` - timezone config
- `getDailyMacroTargetTemplate` - macro targets for a date
- `getAllMacroSchedules` - weekly macro schedule
- `getBrazeSDKConfig` - push notifications
- `getDrinkingWater` - water tracking data
- `getDayInfo` - diary entries for a date
- `updateDevices` - device sync
- `getOffer` - promotional offers
- `checkMessages` - in-app messages
- `getCalendarInfo` - calendar overview data
- `getChartsConfigData` - chart settings
- `areThereRepeatItemsToBeLoggedForDay` - pending repeat items
- `getAllFood` - full nutrition data for all foods in current diary
- `getDashboardConfig` - dashboard layout
- `getBrazeEvents` - analytics events
- `getFood` - detailed food data for a specific food
- `getCaloriesConsumedAndBurned` - energy balance
- `getBiometrics` - biometric measurements
- `getSleepBiometricsByDayAndMetricId` - sleep data
- `getUserFasts` - fasting records
- `getFastingStats` - fasting statistics
- `keepAlive` - session keepalive
- `getRepeatedItems` - list repeat items
- `setBrazeEvent` - log analytics event

---

## Recommendations for MCP Server Implementation

### Priority 1: `copyDay` (Highest Value)
- Single RPC call copies ALL entries from any date to any date
- Perfect for "apply meal plan" workflow: log a template day, then copyDay to target dates
- Template: `GWT_COPY_DAY = "7|0|8|...copyDay...|1|2|3|4|4|5|6|7|7|8|{user_id}|7|{src_day}|{src_month}|{src_year}|7|{dst_day}|{dst_month}|{dst_year}|"`

### Priority 2: `getDayInfo` (Supporting)
- Already essentially captured in existing exports, but the RPC version gives serving-level detail (IDs, measures, quantities)
- Useful for reading a day's entries to selectively copy individual items

### Priority 3: Repeat Items (`addRepeatItem`, `getRepeatedItems`, `deleteRepeatItem`)
- Enables "set and forget" recurring entries
- Good for supplements, daily staples, etc.
- Could build a "meal plan template" by adding all items as repeat items

### Priority 4: `editDiaryEntries` (Selective Paste)
- More complex than copyDay but allows copying specific items
- Useful for adding individual entries with full control

### "Plans" Page
- The "Plans" nav item is the subscription/billing page, NOT a meal planning feature
- Cronometer does not have a dedicated meal plan/template feature
- Meal planning is done through Repeat Items + Copy Day + Custom Meals
