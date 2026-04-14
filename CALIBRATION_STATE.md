# Calibration State

Live observations from the user's Claude.ai Settings → Usage page, used
to ground-truth this tool's assumptions. Each entry records the screen
state + any tool snapshot from the same moment.

---

## Observation #1 — 2026-04-09, Max (5x)

**Claude.ai Settings → Usage UI showed:**

| UI row               | % used | Reset                     |
|----------------------|-------:|---------------------------|
| Current session (5h) |     7% | 4 hr 43 min remaining     |
| Weekly · All models  |    42% | Wed 4:00 PM               |
| Weekly · Sonnet only |     0% | Thu 8:00 PM               |

**Plan:** Max (5x) — confirms `--plan max5`.

**Tool snapshot at the same moment:** _(to be filled in)_

**Inferences about Anthropic's actual model (important — these supersede
anything earlier in this codebase):**

1. **The weekly secondary bucket is "Sonnet only", NOT "Opus only".**
   The `weekly_opus` field throughout this codebase is pointed at the
   wrong model family. Rename / re-target to `weekly_sonnet`.

2. **Weekly windows are NOT rolling 7 days.** The two weekly bars have
   different reset days (Wed vs Thu). Each is anchored to a specific
   period start, not `now - 7d`. Likely behavior: window starts at the
   first message after the previous reset, and rolls forward by a
   fixed period (7 days? or aligned to a calendar week?).

3. **Session is a fixed 5-hour BLOCK, not a rolling window.** The UI
   shows a countdown to reset, implying a hard start anchor. This
   matches the "5h block" convention used by ccusage and
   Claude-Code-Usage-Monitor. Our rolling `[now-5h, now]` is close
   but not identical — during the last few minutes of a block it
   will under-count because records just before the anchor fall off.

4. **No Opus-only weekly bar is visible** even though the user is a
   heavy Opus user. Either:
   - Anthropic dropped the Opus weekly cap since the Aug 2025
     announcement, or
   - It's hidden until the user gets close to it, or
   - Opus only counts toward the "All models" bucket.

   Default behavior of this tool should now be: track a third bar
   for **Sonnet only**, hide the Opus-specific one by default, and
   let the user re-enable it with a flag if their UI shows it.

---

## Action items (not yet implemented)

- [ ] Rename `weekly_opus` → `weekly_sonnet` (or more flexibly: a
      generic `weekly_model_specific` with a family filter setting).
- [ ] Change 5h window to a **block-anchored** mode by default. Expose
      the block start/end and a "resets in" countdown matching the UI.
- [ ] Record the anchor times for the two weekly windows (from the
      reset timestamps shown in the UI). Use these anchors instead of
      rolling 7-day math.
- [ ] Once the user pastes their snapshot output at the same moment
      as observation #1, compute:
      - `real_5h_session_limit ≈ snapshot.billable_5h / 0.07`
      - `real_weekly_all_limit ≈ snapshot.billable_weekly / 0.42`
      - `real_weekly_sonnet_limit` — need more data (0% gives no signal)
