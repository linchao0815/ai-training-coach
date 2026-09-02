---
name: marathon-coach
description: Build/review the runner's next single training week (goal race block, any concurrent injury management, COROS/Garmin sync, wiki-backed health/training context). Use when asked to review last week, plan/schedule next week, or adjust the training plan.
---

# Marathon Coach — one-week-at-a-time training plan skill

Repo-specific skill template. It is **not** a generic periodization engine — the whole
point of this pattern is to encode *this specific athlete's* profile, constraints, and
working process, grounded in their real synced data. What you get out of the box here
is the shape and working process; fill the athlete profile section below with your own
facts before relying on this for real planning decisions.

## Athlete profile (template — replace every bracket with your own facts)

> This section is deliberately verbose in a real, in-use version of this skill — the
> original project this template is derived from ran this section to ~250 lines,
> because a coaching skill is only as good as the specifics it's grounded in. Vague
> profile notes produce vague, generically-safe plans. Be concrete: dates, numbers,
> confirmed decisions, and the *reasoning* behind them (so a future planning session
> doesn't silently re-litigate something already settled).

- **Goal race**: [date, race name, target time, and target pace]. State whether the
  target is a PB attempt, a floor-not-stretch goal, or something else, and say why —
  future planning sessions should design against the *stated* target, not quietly
  drift toward the athlete's lifetime best.
- **Tune-up races** (if any): [date, race, role in the block — hard effort/fitness
  check vs. another goal race].
- **Training volume context**: [recent weekly/monthly km, historical year-over-year
  volume] — so a proposed week can be sanity-checked against what's realistic.
- **Equipment constraints** (if any): [e.g. "never suggest changing footwear/gear as
  part of a fix" — hard constraints the plan must respect regardless of what would
  otherwise be the textbook recommendation].
- **Injury/limitation status** (if any): location, current pain pattern, what's
  improved vs. still limiting, and **the athlete's own self-report as of the last
  check-in** — pain status especially should be treated as changing week to week, not
  a fixed fact carried forward indefinitely.
- **Known chronic issues specific to this athlete** (pacing habits, recurring fade
  patterns, cramping history, etc.) — the kind of thing that only shows up after
  looking at years of this specific athlete's race data, not generic sports-science
  advice.
- **Whole-health context** (if relevant): anything from a broader health knowledge
  base (see `wiki/`) worth factoring into fatigue/recovery/training decisions, without
  over-reaching into diagnosis.

### Injury-specific load rules (example section — replace with your own)

If the athlete has an active injury, this is where the concrete do's/don'ts live
(mechanism, what to avoid, what to prefer, the pain-tolerance rule used as a gate,
and any data-backed leading indicator you've found in their own training data — e.g.
"metric X reliably predicts a bad long run before pain shows up"). Cite the evidence
source (a `doc/references.md`-style file, or a `wiki/concepts/` page) rather than
asserting it here without a source.

### Race-day pacing / fade-prevention rules (example section — replace with your own)

If historical race data shows a recurring pattern (e.g. consistent second-half
fade), this is where the objective gates go: what "green/yellow/red" looks like on a
long run (pace/cadence/stride/ground-contact-time deltas, checked **early vs. late in
the same run and on terrain-matched segments** — a naive early-vs-late comparison on a
hilly run will misread grade as fatigue), and what the plan should do in response to
each. Keep this grounded in the athlete's own analyzed data, not generic pacing advice.

## Current week structure (verify, don't assume — the athlete changes it)

State the athlete's current weekly skeleton (which day is the long run, which days are
easy/recovery, where the one standalone quality session sits) **as a snapshot with a
date**, and say explicitly: **read the actual calendar rather than trusting this
paragraph** — `list_scheduled_workouts` or the synced `scheduled_workouts` table shows
what is really on the calendar, and athletes reschedule without necessarily telling you.

## Working process: ONE WEEK AT A TIME

The athlete plans a single week, reviews execution, then plans the next — not a full
multi-week block upfront. Every time this skill runs:

0. **Wiki preflight first.** Follow the project wiki rules before planning: run
   `python3 verify_wiki_numbers.py` (stdlib only, no venv needed) and fix any drift
   before building on those pages, then check `wiki/.pending-ingest` when the request
   is related to training, injury, health data, or analysis. If it is non-empty, tell
   the athlete what is pending and ask whether to ingest before planning. If it is
   empty, start from `wiki/index.md`, then read the relevant entity/concept pages.
   Prefer wiki facts over older `my.md` notes or stale memory.
1. **Always sync via `fetch_training_data.py` first — never call the `mcp__coros__*`
   read/list/query tools (`list_activities`, `get_daily_metrics`, `get_sleep_data`,
   `get_activity_detail`, `list_scheduled_workouts`, ...) directly for data review.**
   Those MCP tools are live queries against COROS and do **not** write to
   `data/coros.db` — using them for review guarantees the sqlite copy drifts out of
   sync. The **only** legitimate uses of the `mcp__coros__*` tools in this skill are
   the actual write actions in step 6 (`create_run_workout`, `schedule_workout`,
   `move_scheduled_workout`, `remove_scheduled_workout`, etc.) — there is no `.py`
   equivalent for those. For any read/review need, instead:
   1. Get the last synced date: read `data/fetch_meta.json`'s `end_day` (or
      `SELECT MAX(date) FROM daily_metrics` in `data/coros.db`).
   2. Run `fetch_training_data.py --since <that date minus 1 day, YYYYMMDD>` (the
      1-day overlap is deliberate — it re-covers the last fetched day in case a feel
      rating/note was added after the fact). Use the coros-training-mcp venv python
      per the script's own docstring; don't pass `--months`/`--full-history` for a
      routine review — those re-fetch far more than needed.
   3. Run `analyze_training.py` to remirror `data/analysis/*` and `data/coros.db`'s
      analysis tables.
   4. Do the actual review by querying `data/coros.db` (sqlite3) / `data/analysis/*`,
      not by re-querying COROS live.
2. **Review the week just completed** (or in progress): pull actual executed sessions
   from `data/coros.db` / `data/analysis/sessions.json` (per step 1 — synced first,
   not a live MCP query), compare against what was scheduled, and explicitly ask the
   athlete about pain/response this week if it's not obvious from the data — pain
   status is self-reported and changes weekly. Also ask whether any other current
   health issue from the broader knowledge base affects the coming week.
3. **Calibrate paces from actual recent data**, not assumption — pull recent entries
   from `data/analysis/quality_sessions.json` and `data/analysis/long_runs.json`.
   Paces/HR bands drift over weeks; re-derive them each planning session rather than
   carrying forward an old number — a stale easy-pace band, in particular, tends to
   silently drift and then reads as the athlete "not following the plan" when it's
   actually the plan that's out of date.
4. **Position the week against the race calendar**: compute weeks-remaining to each
   upcoming race from the current date, and design the week's structure (volume,
   quality, long run) appropriately for that point in the block (base/build/peak/
   taper) rather than a fixed week-number template. If you're tracking training phases
   with graduation criteria (see any `wiki/concepts/load-management.md`-equivalent
   page), check and update that page after this week's decision so the next planning
   session doesn't lose track of the overall arc.
5. **Draft the week as a doc first** (e.g. `doc/YYYY-WNN-training-plan.md`, see
   `doc/example-training-plan.md` for the format) for the athlete to review before
   anything is pushed to COROS/Garmin — creating/scheduling workouts mutates their
   real training calendar, so don't call `create_run_workout` / `schedule_workout` /
   `create_workout` until the athlete confirms the draft.
6. **After confirmation, push to whichever platforms the athlete uses — these are two
   independent sync targets for the same confirmed plan doc, not sequential steps.**
   - **COROS**: use the `coros-sync` skill. Do NOT hand-compose `create_run_workout`
     MCP calls — that's exactly the failure mode `coros-sync` exists to prevent
     (silent pace-preset collapse, wrong display units, no read-back verification).
   - **Garmin** (if the athlete uses it): use the `garmin-sync` skill. Garmin has no
     same-day-name dedup — re-running the plan file after a day was already pushed
     **will duplicate** that day's entry — only re-run it for days that actually
     changed, or remove the stale entry first.
   - Both skills share the same `<!-- coros: day=YYYY-MM-DD name=<workout name> -->`
     anchor and table syntax (distance `8km`, time `15分鐘`/`90秒`, and
     `>>> 重複 N 組 ... <<<` repeat blocks for interval sessions — see either skill
     for the full syntax reference, and `doc/example-training-plan.md` for a worked
     example). The segment table's 距離/時間 column must be segment length, not
     cumulative range (`0-8km`), and the 配速 cell must be a bare pace with no extra
     condition folded in — put HR caps or other conditions as prose below the table.

## Data pitfalls when reviewing COROS data

These are generic engineering gotchas in the data itself, independent of who the
athlete is — worth keeping even in a stripped-down template, because they cost a wrong
conclusion at least once in the original project. Check them before reporting.

- **`activities.start_time` is epoch seconds, and `strftime('%s','YYYY-MM-DD')` parses
  the string as UTC.** In UTC+8 (or any non-UTC zone) that puts the day boundary at a
  shifted local time, silently dropping early-morning sessions from a "this week" query
  if the athlete trains before the shift point. Always filter with
  `datetime(start_time,'unixepoch','localtime') >= 'YYYY-MM-DD'`.
- **Indoor runs (`sport_name = 'Sport 101'`) can have unusable optical HR.** At matched
  pace, indoor HR readings can run tens of bpm above outdoor readings for the same
  effort — check for this before trusting indoor HR data (constant pace with HR
  swinging non-monotonically, or an implausible peak against the athlete's known LTHR,
  are the tells; real heat drift is monotonic under constant load, sensor noise is
  not). Consequence: indoor-run HR/training-load figures can corrupt weekly aggregates
  in proportion to that week's indoor share — don't compare weekly training-load across
  weeks with different indoor/outdoor mixes without checking this. Prescribe indoor
  sessions by pace + RPE rather than an HR ceiling if the sensor is known-unreliable
  indoors on this device.
- **Running-dynamics fields can drop out mid-activity.** Cadence/ground-contact-time
  fields have been observed present for part of an activity and zero for the rest.
  Always check coverage before running a gait-quality gate, and report the gap rather
  than silently gating on a partial series.
- **Weather lives in a nested detail field, all values ×10**: e.g. temperature 280
  means 28.0°C, humidity 500 means 50.0%. It's typically a single snapshot near
  activity start, not an average over a multi-hour run. Missing values can show up as
  an int32 sentinel (a huge negative number after dividing by 10) — treat any absurd
  magnitude as null rather than data.

## COROS unit gotchas

**Push-path gotchas** (pace-preset collapse, the two display-unit fields, an
`estimated_time_seconds` unit bug) live in the `coros-sync` skill and
`wiki/concepts/coros-unit-pitfall.md` — `create_workouts.py`/`coros_workout_plan.py`
already guard against all of them automatically.

**Read-path gotchas** (about reviewing/analyzing existing data, not pushing new
workouts):

- Distance target_value = meters × 100 (i.e. centimeters) in raw COROS payloads, if
  you ever inspect one directly.
- **Some COROS pace/speed field names are mislabeled** — a field named like a speed
  can actually store plain seconds/km, not a value meant to be inverted via
  `1000/speed_m_s`. Before reporting any pace derived from a field you haven't used
  before, sanity-check it against a known real pace rather than trusting the field
  name. Full writeup in `wiki/concepts/coros-unit-pitfall.md`.
- **`get_activity_detail` responses are large** (a single run can exceed the tool
  result token limit and get saved to a scratch file instead of returned inline).
  Don't request full detail just to read a couple of summary fields — use `jq`/
  PowerShell `ConvertFrom-Json` on the saved file to pull just the summary fields you
  need rather than re-requesting or reading the raw file directly.

## Where things live

- `wiki/` — primary compiled knowledge base. Start with `wiki/index.md`; use
  `wiki/WIKI.md` for ingest/query/lint rules.
- `my.md` (copy from `my.md.example`) — background/intake notes (historical; may be
  superseded by wiki).
- `data/`, `data/analysis/` — actual training history for calibration (see step 3).
- `data/coros.db`, `data/garmin.db` — structured activity history; query summaries
  instead of dumping whole DBs.
- `doc/example-training-plan.md` — weekly plan doc format to reuse, including the
  repeat-block syntax for interval sessions.
- `doc/coros-estimated-time-bug.md`, `docs/superpowers/specs/*.md` — bug diagnosis and
  design records for the sync tooling itself.
