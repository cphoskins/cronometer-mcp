# Spec — Custom Diary Group Support

**Origin:** feature request from Douglas Lankler, 2026-07-13. He uses all 8 of
Cronometer's diary groups (`Data`, morning fluids, breakfast, ...) and the MCP
can only reach four of them.

**Status of the investigation:** the protocol questions are answered and
verified. See `RESEARCH-diary-groups.md` conclusions inlined below. What remains
is implementation plus one open question (§3).

---

## Established facts (verified 2026-08-19, do not re-derive)

Cronometer Gold exposes **8 diary group slots**, stored in user settings as keys
`DG01`–`DG08`, each with a companion enable flag `DG01ON`–`DG08ON`.

**The `diary_group` integer sent in `updateDiary` is 0-based over those slots:**

| wire value | settings key | name on the test account |
|---|---|---|
| 0 | `DG01` | `Test` |
| 1 | `DG02` | `Breakfast` |
| 2 | `DG03` | `Lunch` |
| 3 | `DG04` | `Dinner` |
| 4 | `DG05` | `Snacks` |
| 5 | `DG06` | `Group 6` |
| 6 | `DG07` | `Group 7` |
| 7 | `DG08` | `Group 8` |

Proven by writing a serving at wire index 1 and observing it render under
`Breakfast` in the web UI while `Test` (`DG01`) sat above it, empty.

Three consequences that shape the work:

1. **The existing `_DIARY_GROUP_MAP` is correct for a default account.**
   `breakfast=1 … snacks=4` maps to `DG02`–`DG05`. Enabling a first-slot group
   does **not** renumber the others. An earlier renumbering hypothesis was
   tested and disproved.
2. **Cronometer performs no server-side validation of this field.** Writes at
   wire 8 and 9 were accepted with a normal success response and the entries
   were not retrievable afterwards. All range checking must be client-side.
3. **The group names already arrive for free.** `GWT_AUTHENTICATE` in
   `client.py` is byte-identical to the live call the web app makes, and its
   response body contains the whole settings map including all `DG01`–`DG08`
   names and `DG*ON` keys. `_gwt_authenticate()` currently discards it, keeping
   only `user_id`. **No new RPC, no new capture, and no extra round-trip are
   required for this feature.**

Extraction, verified against the live account:

```python
dict(re.findall(r'"(DG0[1-8])","([^"]*)"', resp.text))
# {'DG01': 'Test', 'DG02': 'Breakfast', ..., 'DG08': 'Group 8'}
```

> ⚠️ **Do not verify any of this by writing entries and reading them back
> through the CSV export.** The `/export` endpoint throttles hard — a burst of
> ~10 writes produced a **>70 minute** lockout of *all* read access, since every
> read tool routes through that endpoint. Use the web UI or a browser session as
> the read path during development.

---

## 1. Capture diary groups during authentication

**DO**
- Parse the `authenticate` response inside `_gwt_authenticate()` and store the
  result on the client as an ordered structure keyed by wire index (0–7), each
  entry carrying `wire_index`, `settings_key` (`DG0n`), `name`, and `enabled`.
- Populate it on both the fresh-login path and the `_restore_session()` path, so
  a restored session is not missing group data.
- Expose it as a public attribute or accessor (e.g. `client.diary_groups`).
- Fall back to the four hardcoded defaults at wire 1–4 if parsing yields
  nothing, so a parse regression degrades to today's behavior instead of
  breaking all writes.

**DON'T**
- Don't add a second network call to fetch groups. The data is in a response the
  client already receives.
- Don't cache group names to disk alongside the session pickle. Group config can
  change between runs and a stale name silently misfiles food.
- Don't assume slot count is 8 forever — Cronometer has a public feature request
  open to raise it. Derive the range from what's parsed, don't hardcode `range(8)`
  in more than one place.

**DONE WHEN**
- `client.diary_groups` on the test account returns 8 entries whose names are
  exactly `Test, Breakfast, Lunch, Dinner, Snacks, Group 6, Group 7, Group 8` at
  wire indices 0–7 respectively.
- A unit test feeds a captured `authenticate` response fixture and asserts that
  mapping, with no network access.
- A unit test asserts the fallback produces `breakfast=1 … snacks=4` when the
  response contains no `DG` keys.

---

## 2. Expose the groups as an MCP tool

**DO**
- Add a `list_diary_groups` tool returning each group's wire index, name, and
  enabled state.
- State in the tool docstring that the names are user-configured, so the model
  reads them rather than assuming Breakfast/Lunch/Dinner/Snacks.

**DON'T**
- Don't return the raw `DG0n` settings keys as the primary identifier; they are
  1-based and the wire value is 0-based, which is precisely the confusion this
  tool exists to prevent.
- Don't include the rest of the settings map in the output. The `authenticate`
  response also carries weight, height, macro targets and other personal
  fields — parse only the `DG` keys.

**DONE WHEN**
- Calling `list_diary_groups` against the test account returns the 8 groups with
  correct enabled flags.
- The response contains no keys other than group index, name, and enabled.

---

## 3. Determine each group's enabled state — OPEN QUESTION

The `DG01ON`–`DG08ON` keys are present in the `authenticate` response, but their
**values were never read**. Booleans are de-duplicated in the GWT string table,
so adjacency-based extraction (which works for the unique name strings) does not
work for the flags. Reading them requires properly walking the response's data
section against its string table.

On the test account the web UI renders exactly 5 groups (`Test` through
`Snacks`), so `DG01`–`DG05` are on and `DG06`–`DG08` are off. Name is **not** a
usable proxy — a user can enable a slot without renaming it.

**DO**
- Extend the existing `_tokenize_gwt_data` / `_extract_gwt_string_table` helpers
  to resolve the `DG*ON` values, and assert them against the known test-account
  state (5 on, 3 off).
- If that proves impractical, ship §1/§2 with `enabled: None` (meaning unknown)
  and have §4 treat unknown as "not confirmed enabled".

**DON'T**
- Don't infer enabled state from whether the name looks like a default
  (`"Group 6"`). It is wrong for anyone who enables a slot without renaming it.
- Don't guess that all 8 are enabled. Writing to a disabled slot has **unknown**
  behavior — that specific probe was lost to the export lockout and was never
  re-run.

**DONE WHEN**
- `client.diary_groups` reports `enabled=True` for wire 0–4 and `enabled=False`
  for wire 5–7 on the test account, **or** the fallback path is implemented and
  §4's guard is proven to reject unknown-state groups.

---

## 4. Resolve and validate the `diary_group` argument

**DO**
- Accept a case-insensitive, whitespace-trimmed **name** matched against the
  user's actual group names, and accept an explicit integer wire index.
- Validate the integer against the parsed range and reject anything outside it.
- Refuse to write to a group that is not confirmed enabled, and say so in the
  error.
- On an unmatched name, return an error listing the user's actual group names.
  This is the whole fix for the reporter's case: his `DG02` is named "morning
  fluids", so `"Breakfast"` must not silently resolve to it.
- Apply identical resolution to `add_food_entry` **and** `add_repeat_item`; both
  currently share the same hardcoded map and the same defect.

**DON'T**
- Don't keep `_DIARY_GROUP_MAP` as the primary lookup. It may only be used as
  the no-data fallback from §1.
- Don't fall back to a default group when a name fails to match. Silently
  logging food to the wrong meal is worse than an error — that failure is the
  origin of this ticket.
- Don't rely on Cronometer to reject a bad index. It accepts them and the entry
  becomes unreachable.

**DONE WHEN**
- `diary_group="test"`, `"Test"`, and `" test "` all resolve to wire 0 on the
  test account.
- `diary_group="Breakfast"` resolves to wire 1 on the test account.
- `diary_group=8`, `diary_group=-1`, and `diary_group="Elevenses"` each return
  an error and issue **no** write; the error for the name case lists the real
  group names.
- `diary_group="Group 7"` (present but disabled) is refused.
- Equivalent tests exist for `add_repeat_item`.
- An end-to-end run adds one entry to a non-default group, confirms placement in
  the web UI, and removes it.

---

## 5. Fix the time-of-day default

`_default_diary_group()` returns one of the four English names, which may not
exist on a customized account. On the reporter's account it would return
`"Breakfast"` — a name he does not have.

**DO**
- Return a wire index, not a name.
- Choose only among **enabled** groups.
- When the account's groups are non-default, prefer an explicit argument over
  guessing; if no sensible default exists, error and tell the caller to pass
  `diary_group` explicitly.

**DON'T**
- Don't map times of day onto renamed slots by position. Wire 1 being "morning
  fluids" does not make it breakfast.
- Don't keep returning a string that then needs re-resolution — that round trip
  is how the name/index confusion re-enters.

**DONE WHEN**
- On a default account, the 11:00/14:00/19:00/23:00 boundaries still select
  Breakfast/Lunch/Dinner/Snacks (regression test, no behavior change).
- On the test account, the default never selects a disabled group.
- On a fully renamed account fixture, `add_food_entry` without `diary_group`
  either picks an enabled group or errors — it never writes to an unmatched slot.

---

## 6. Ship it

**DO**
- Update `README.md`: the `diary_group` argument accepts names or 0-based wire
  indices, names are user-specific, and `list_diary_groups` exists.
- Bump the version and publish.
- Reply to the reporter. His entries have very likely been landing in the wrong
  group since July — that is worth saying plainly, not burying in a changelog.

**DON'T**
- Don't document the four English names as *the* valid values. That framing is
  the original bug.
- Don't ship without confirming his group layout. His description ("Group 1 for
  Data, Group 2 is my morning fluids, 3 is Bfast") is prose, not a settings
  dump, and the fix's correctness for him depends on which slots he actually
  uses.

**DONE WHEN**
- README documents names, indices, and the new tool.
- `pytest` is green and the version is bumped.
- The reporter has confirmed a non-default group works on his account.

---

## Out of scope

- **Deleting the dead GWT search code.** `GWT_FIND_FOODS` and
  `_parse_find_foods` are unreachable (Cronometer removed the RPC) and are
  annotated `DEAD` in the source. Their ~370 lines of tests still pass and still
  exercise the GWT string-table decoder. Removing them is a judgment call, not
  part of this feature.
- **Renaming diary groups from the MCP.** Read-only here; writing settings is a
  different RPC and a different risk profile.
- **The wider `/api/v3/` surface.** Worth a separate look — food search already
  moved there, and other GWT calls may follow.
