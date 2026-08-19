# Hourly WISER position / occupancy maps

One PNG per completed 1-hour window from the **live** WISER tracking database — a per-tag grid of
panels plus an all-tags overlay. Two styles:

- `--style scatter` (default): per-tag position scatter, points coloured by time within the hour
  (early → late). Fast QC: shows movement, coverage, dropouts, and frozen tags at a glance.
- `--style occupancy`: per-tag 2-D position-density heatmaps (log-scaled, 4-inch bins). Shows dwell.

## Safety (do not weaken)

- Reads are strictly **read-only**: `file:<db>?mode=ro` + `PRAGMA query_only=ON`, one bounded
  one-hour query at a time. Never `immutable=1` / `nolock=1` (the DB is a live WAL writer).
- The **in-progress hour is never plotted** — "completed" is decided from the DB's own
  `MAX(timestamp)`, not the system clock.
- The script **refuses** any `--output` equal to or under the source DB tree `D:\Wiser`
  (a sibling like `D:\Wiser_plot` is fine).
- Nothing is ever written to `D:\Wiser`. Outputs default to **`D:\Wiser_plot`** — off the C: drive
  and outside the git repo. The plot-extent cache (`arena_extent.json`) is written there too.

## Run (conda env `cv`: pandas, numpy, matplotlib)

```bash
cd wiser_tracking_analysis

# Verify a single completed hour first (UTC or local):
python scripts/plot_hourly_occupancy.py --hour 2026-06-29T01 --tz utc

# Plot every completed hour not yet plotted (idempotent; default mode):
python scripts/plot_hourly_occupancy.py --backfill

# Custom window (either side optional: omit --from for DB start, --to for DB end):
python scripts/plot_hourly_occupancy.py --to 2026-06-28T20:00            # beginning -> 8 PM
python scripts/plot_hourly_occupancy.py --from 2026-06-28T19:30 --to 2026-06-28T21:15

# Occupancy heatmap instead of scatter:
python scripts/plot_hourly_occupancy.py --backfill --style occupancy

# Continuous fallback (prefer the scheduled task below):
python scripts/plot_hourly_occupancy.py --watch
```

Key flags: `--db` (default `D:\Wiser\data\1stcohort_2026.sqlite`), `--output`
(default `D:\Wiser_plot`), `--tz local|utc`, `--style scatter|occupancy`, `--bin-inches`
(occupancy), `--xmin/--xmax/--ymin/--ymax` + `--refresh-extent` (override/recompute the cached plot
extent `<output>/arena_extent.json`), `--force` (overwrite / allow the in-progress hour).

Output files: `D:\Wiser_plot\<style>_<tz>_YYYY-MM-DD_HH.png`
(~0.3–0.7 MB each; ~10–17 MB/day; ~1–2 GB per season).

## Per-hour coverage QC → Slack warning

Each plotted hour is also checked for **tag coverage**, and a Slack warning is posted when either:

- a **rat is missing** — an expected tag has *zero* fixes that hour (including a completely empty
  hour), or
- a **tag has an open-field data hole** — its longest no-data gap (lead-in from the hour start, any
  inter-fix gap, or run-out to the hour end) is **≥ `--gap-minutes`** (default **15 min**) **and it
  did not drop out in/at a shelter**.

**Shelter-occlusion is suppressed.** WISER is line-of-sight: a rat resting motionless inside or at
the mouth of a refuge/house/tunnel loses signal to the anchors (NLOS) and reappears when it moves —
this is expected, not a lost tag. So a gap whose position on either side is within
`--shelter-margin-in` (default **18 in**) of a `refuge`/`tunnel` ROI (from `configs/wiser_rois.json`)
is **not alerted** — it's logged and rolled into a one-line "N shelter-occlusion gap(s) suppressed"
FYI on the alert instead. Use `--no-shelter-suppress` to alert on every gap regardless. Diagnosed
2026-07-05: the midday gaps were all rats sitting in `refuge_4`, while the other tags reported
normally — see [change_log/2026-07-04-wiser-hourly-coverage-slack.md](../change_log/2026-07-04-wiser-hourly-coverage-slack.md).

The expected roster comes from `configs/rat_identities.csv`. A tag whose `valid_until` has passed is
**not expected** (e.g. Sova, retired 2026-06-29) so it never triggers a false "missing" alert; for a
tag retired *mid-hour* the window ends at its cutoff. Verified against live hours: an all-present
hour stays silent, refuge_4 rest gaps are suppressed, and a synthetic 20-min open-field hole alerts.

Slack credentials are read from the **same recorder-QC file** as the exposure/disk alerts,
`E:\recording_qc\overexposure.config.psd1` (kept out of git). It needs `SlackBotToken` plus a
destination list — the plot uses **`WiserAlertChannels`** if present, otherwise falls back to
`SlackChannels` (team channel + Hongyu DM). To send WISER warnings to a DM only, add to that file:

```powershell
WiserAlertChannels = @( 'U02E2F0SNUR' )   # Hongyu's DM only
```

Each problem hour alerts **once** — a small `coverage_alert_state.json` next to the PNGs records
what has been sent (survives `--force`/re-runs). All Slack calls are best-effort: a Slack outage
prints a note but never breaks plotting or QC.

Flags: `--gap-minutes <n>` (hole threshold, default 15), `--shelter-margin-in <n>` (shelter
proximity, default 18), `--no-shelter-suppress` (alert on all gaps), `--roi-config <path>`
(default `configs/wiser_rois.json`), `--slack-config <path>` (default the E: QC file), `--no-slack`
(QC still runs, nothing posted), `--test-slack` (post a test message and exit).

## Battery watch (DM-only)

The same hourly job also watches tag batteries (from the DB's `battery_voltage`), running **once per
invocation** with the DB scan rate-limited to every `--battery-interval-h` (default 6 h). It **DMs a
heads-up** — never the team channel — when any expected tag's recent-window median voltage is
**≤ `--battery-warn-volts`** (default **2.70 V**) *or* **≥ `--battery-below-cohort-mv`** (default
**40 mV**) below the cohort median. Deduped to at most once/day/tag.

Destinations are DM-only: `WiserBatteryChannels` in the psd1 if present, else the user-id (DM) subset
of the normal list (so the team channel is excluded). Run `--battery-now` to check immediately
(ignores the interval) and exit.

> ⚠️ The default thresholds are **placeholders** — the tags' true low-battery cutoff is unknown, so
> the trigger is currently relative (a tag diverging below the cohort) plus a conservative absolute
> floor. Set `--battery-warn-volts` once the real cutoff is known. See
> [QC_LOG.md](QC_LOG.md) (2026-07-10) for the cohort-drain analysis.

Battery flags: `--battery-warn-volts`, `--battery-below-cohort-mv`, `--battery-window-h`,
`--battery-interval-h`, `--no-battery-check`, `--battery-now`.

## Hourly scheduled task (field PC)

Install **after** a manual single-hour run looks correct. From an elevated PowerShell:

```powershell
.\install_wiser_occupancy_task.ps1 -RunNow
# options: -Style scatter|occupancy  -Tz local|utc  -DbPath <path>  -OutputDir <path>  -PythonExe <path>
```

Runs `--backfill` hourly as SYSTEM. Point `-DbPath` at a new `.sqlite` for later cohorts.
