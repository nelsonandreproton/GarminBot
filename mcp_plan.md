# GarminBot MCP Server — Implementation Plan

> **Status:** Phase 1 BUILT (2026-06-19) — local stdio, 11 read-only tools, 110 tests. Phase 2 (remote deploy + auth) not yet started.
> **Goal:** Let Nelson interact with ALL his GarminBot data from Claude — ask questions,
> see averages, track evolution of values over time, and discuss strategy (deficit, weight,
> training). Origin: task #5.
>
> **Phase 1 delivered:** `src/mcp/{server,tools,formatting}.py` + `get_garmin_activities_range`
> in repository.py. SDK: official `mcp` (FastMCP), `mcp.run(transport="stdio")`. DB opened
> **read-only at the driver level** (`?mode=ro&uri=true`) in the server entrypoint — writes are
> refused by SQLite, not just by convention. Verified static-bearer auth (Phase 2) is supported
> by Claude Code (`--header`/`.mcp.json headers`) and Claude Desktop (JSON config) — no OAuth needed.
>
> **Phase 2 caveats to honour before deploy** (from security review): cap date-range width on
> get_metrics_range/get_activities; use `hmac.compare_digest` for token compare; validate `Origin`
> header; verify SQLite WAL + `:ro` bind-mount actually opens (likely needs RW dir mount + `mode=ro`);
> lower DB-path log to DEBUG. Not exposed in v1: waist, water-trend, newsletter.

---

## Decisions taken (defaults — correct me if any is wrong)

These are decided so the plan is concrete. Flag any you disagree with and I'll revise.

| Decision | Choice | Why |
|---|---|---|
| **Data access** | Import `Repository` directly (in-process) | Repository already has ~50 read methods incl. the aggregates that ARE the value (weekly/monthly stats, weight/nutrition trends, training load). The existing REST API (`src/utils/api.py`) only exposes 3 endpoints — far short of "todos os dados". Single source of truth, least new code. |
| **Language / SDK** | Python + official MCP SDK (FastMCP) | The entire value is reusing the Python `Repository`. A TS server would re-implement everything. ⚠️ Verify current FastMCP API via Context7 before coding (SDK evolves). |
| **Scope** | Read-only analytics | Nelson's words: "fazer perguntas aos meus dados, perceber médias, evolução de valores, discutir estratégias". Query/analyze, not logging. Write tools = optional Phase 3 only. |
| **Tool design** | ~8–10 typed tools mapping to Repository methods. NO raw-SQL tool. | Typed tools are safe and self-documenting. A free-form SQL tool is an injection/safety hole we don't need — the aggregates are already computed in the repo. |
| **Transport** | Streamable HTTP behind Caddy + sslip.io | Remote access from Claude (Desktop/web). We already run Caddy. Local stdio is dev-only. |
| **Auth** | Bearer token (non-negotiable — it's public-facing) | Reuse `GARMIN_API_KEY` pattern or a dedicated `GARMIN_MCP_TOKEN`. |
| **Deployment** | Separate homeserver compose service, DB volume mounted READ-ONLY | Isolation from the bot process; read-only mount enforces no-writes. SQLite reader+writer → enable WAL mode. |

---

## Data inventory (what "all the data" means)

Single SQLite file: `data/garmin_data.db`. 14 models. Key ones for querying:

- **DailyMetrics** — sleep (hours/score/quality/stages), steps, calories (active/resting/total),
  floors, intensity minutes, resting HR, stress, body battery, SpO2, **weight_kg**. One row/day.
- **FoodEntry** — nutrition log (calories + protein/fat/carbs/fiber), source (fatsecret/manual). Multi/day.
- **GarminActivity** — synced workouts (type, duration, calories, distance).
- **TrainingEntry** — manual training log (one/day).
- **WaterEntry** — hydration (ml, multi/day). **WaistEntry** — waist cm. **UserGoal** — targets.
- **SyncLog** — sync audit. NewsletterPost/Insight — Arnold's Pump Club (lower priority for MCP).

Date convention: `Date` columns = naive local calendar days; `DateTime` = UTC audit timestamps.
MCP date params → treat as local calendar dates; return DateTimes as ISO-8601 UTC.

---

## Phase 1 — Read-only MCP server + core tools

### Project layout (new, separate from bot runtime)
```
src/mcp/
  __init__.py
  server.py          # FastMCP server, tool registration, entrypoint
  tools.py           # tool functions wrapping Repository read methods
  formatting.py      # ORM row / dict → JSON-serialisable, ISO dates, rounded values
tests/test_mcp_tools.py
```
- The server constructs ONE `Repository(DATABASE_PATH)` (read-only intent) and the tools call it.
- Keep tool logic transport-independent so stdio (local dev) and HTTP (prod) both work.
- Reuse, don't re-derive: every aggregate already exists in `repository.py`.

### Tools (each = typed params + documented return shape)

1. **get_daily_metrics(day: date = today)** → full DailyMetrics dict for one day
   (sleep, steps, calories, HR, stress, body battery, SpO2, weight). Wraps `get_metrics_by_date`.
2. **get_metrics_range(start: date, end: date)** → list of daily metric dicts in range.
   Wraps `get_metrics_range`. This is the "everything else over a window" workhorse.
3. **get_weekly_stats(end_date: date = today)** → 7-day averages (sleep avg/best/worst, steps
   avg/total, calories totals). Wraps `get_weekly_stats`.
4. **get_monthly_stats(end_date: date = today)** → 30-day averages. Wraps `get_monthly_stats`.
5. **get_weight_trend(days: int = 90)** → list of (date, kg) + stats (current, delta, min, max).
   Wraps `get_weight_records_range` + `get_weekly_weight_stats`. For "evolução do peso".
6. **get_nutrition(day: date = today)** → daily totals (calories + macros + entry_count).
   Wraps `get_daily_nutrition`. Optionally include the individual `get_food_entries` list.
7. **get_nutrition_trend(end_date: date = today)** → 7-day nutrition averages.
   Wraps `get_weekly_nutrition`. For "médias de calorias/macros".
8. **get_training_load(end_date: date = today)** → 7-day activity totals by type (minutes/km/count)
   + recent training log. Wraps `get_weekly_training_load` + `get_recent_training`.
9. **get_activities(start: date, end: date)** → Garmin activities in range. Wraps
   `get_food_entries_range` analogue for activities (`get_garmin_activities_for_date` per day,
   or add a range method).
10. **get_goals()** → user targets (steps/sleep/weight/calories/macros). Wraps `get_goals`.
11. **get_deficit(day: date = today)** → combined: total burn (Garmin) vs calories eaten
    (FatSecret/manual) → current deficit kcal + %. The "discutir défice" tool. Composes
    `get_metrics_by_date` + `get_daily_nutrition` (mirror `calculate_deficit` logic).

> Coverage note: tools 1–11 cover metrics, nutrition, weight, training, activities, goals, deficit.
> If a model isn't covered by a typed tool (waist, water, newsletter), `get_metrics_range` +
> dedicated small tools can be added — but DO log in the plan that those are not yet exposed,
> rather than implying full coverage silently.

### Tests (TDD)
- Temp SQLite DB fixture (`tempfile.NamedTemporaryFile(delete=False)`, `engine.dispose()` before
  unlink — Windows lock rule). Seed a few days of metrics/food/weight.
- One test per tool: correct values, empty-range → empty/zeroed, date defaulting to today.
- JSON-serialisability: every tool's return must `json.dumps` cleanly (dates → ISO strings).
- Mock nothing external — the MCP server only touches the local DB.

---

## Phase 2 — Deploy behind Caddy + auth (Hetzner)

- **New homeserver compose service** `garminbot-mcp`:
  - Same image/repo as garminbot (or a thin variant); command runs the MCP HTTP server.
  - Mount the DB volume **read-only**: `../GarminBot/data:/app/data:ro`.
  - Enable SQLite **WAL mode** so the MCP reader doesn't contend with the bot writer.
  - `env_file: ../GarminBot/.env`; if it gets its own `.env`, remember: after any `.env` change use
    `docker compose up -d garminbot-mcp`, NOT `restart` (restart doesn't reload env_file — lesson 2026-06-18).
  - `restart: unless-stopped`.
- **Caddy**: route a subdomain/path (sslip.io) → the MCP HTTP port. TLS auto.
- **Auth**: require `Authorization: Bearer <GARMIN_MCP_TOKEN>` on the MCP endpoint. Token in `.env`,
  gitignored. Never log it.
- ⚠️ **VERIFY BEFORE BUILDING** (against current MCP docs / Context7): exactly how a remote Claude
  client (Desktop / claude.ai) connects to a remote Streamable HTTP MCP server with a custom bearer
  token — URL format, header passing, and whether the client supports it. This is the one thing that
  blocks implementation; do not start on a guess.

---

## Phase 3 — OPTIONAL write tools (only if Nelson wants it later)

Not in scope for the stated goal (query/analyze). If desired later:
- `log_weight(kg)`, `log_water(ml)`, `log_training(text)`, `set_goal(metric, value)`.
- These need a writable DB mount (drop the `:ro`) and careful auth — a public write surface is
  higher risk. Decide explicitly before adding.

---

## Risks / open items

- **SQLite concurrency**: bot writes, MCP reads the same file. WAL mode + read-only mount mitigates.
  Verify no "database is locked" under concurrent access.
- **Remote client auth** (Phase 2 ⚠️): the one unverified assumption — confirm via Context7/MCP docs.
- **FastMCP API drift**: pin the SDK version; verify current registration/transport API before coding.
- **Coverage honesty**: if some models (waist/water/newsletter) aren't exposed in v1, say so — don't
  imply "all data" is queryable when it's a documented subset.
- **Data sensitivity**: this exposes personal health data over the internet. Strong token + TLS only;
  consider IP allowlist in Caddy if feasible.

---

## Suggested build order (when budget allows)

1. Verify FastMCP current API (Context7) + remote-client auth mechanism (MCP docs).
2. Phase 1 tools 1–7 (the high-value aggregates) with stdio + tests → validate locally via Claude Desktop.
3. Add tools 8–11; full test pass.
4. Phase 2: compose service + Caddy + token; deploy (`up -d`); verify remote connection from Claude.
5. (Optional) Phase 3 write tools.

Code to be written by Sonnet subagents (per global rule); orchestration/review/security by main model.
