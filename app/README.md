# s2cd app: results, database explorer, date comparison and the analyst

The single place to inspect the solution, so evaluators never need a database client. A FastAPI service over the PostGIS results (the provided pair), a database explorer, an area-of-interest calculator, a date picker that runs the same pipeline on any two Sentinel-2 dates, and a small grounded analyst agent (in the spirit of Solafune's ShigenBot) that answers with numbers taken from tools and returns **map actions, charts and tables**. The endpoint contract is `docs/api.md`; the evidence is RGB only (B02, B03, B04) and two dates.

```mermaid
flowchart LR
  UI[Viewer<br/>localhost:5173 / s2change.dzaky.ai.id] -->|GET runs, changes, facilities, brief| API[FastAPI app/main.py]
  UI -->|db/schema, db/examples| API
  UI -->|POST db/query 30/min| RLD[slowapi + SQL guard]
  UI -->|POST aoi/stats| API
  UI -->|compare/dates, POST compare, poll job| JOBS[CompareJobs<br/>1 worker thread, queue, daily cap]
  UI -->|POST agent/chat 6/min + daily cap| RLA[slowapi]
  RLD --> EXP[explorer.py]
  RLA --> API
  API --> RES[results.py<br/>brief, facilities, aoi_stats]
  API --> AG[deepagents agent<br/>Gemini or OpenRouter]
  AG --> T[12 typed tools app/tools.py<br/>+ read_context over app/knowledge]
  RES -->|SELECT only| DB[(PostGIS / Supabase<br/>role s2cd_reader)]
  EXP -->|SELECT only| DB
  T -->|SELECT only| DB
  T -->|map_action, make_chart, make_table| C[per-request Collector<br/>contextvar]
  JOBS -->|s2cd.compare| STAC[Planetary Computer STAC<br/>B02/B03/B04 of one date]
  JOBS --> FILES[(data/compare/pair/<br/>GeoJSON, WebP, summary)]
  API -->|compare/files| FILES
  PUB[python -m app.publish] -->|owner role, idempotent| DB
  SQ[(data/change_detection.sqlite)] --> PUB
```

## Run locally

```bash
uv sync --all-extras
docker compose up -d db                     # or docker-compose; PostGIS on :5432
cp app/.env.example .env                    # fill GEMINI_API_KEY and the passwords
set -a; source .env; set +a                 # or export the variables yourself
uv run --all-extras python -m app.publish   # loads data/change_detection.sqlite, creates s2cd_reader; safe to rerun
uv run --all-extras uvicorn app.main:app --port 8000
uv run --all-extras python -m app.evals     # needs the API running; writes app/evals/results.{md,json}
```

Run the API from the repository root: the comparison worker reads `inputs/` and writes `data/compare/` and `data/scenes/` (both git-ignored). Comparing other dates needs the `stac` extra (`uv sync --extra app --extra stac`) and network access to Planetary Computer.

Production data lives in Supabase PostGIS. Publish runs from a workstation with the owner URL (pooler, session mode, port 5432); the VPS only gets the read-only `s2cd_reader` URL. On Supabase, `schema_app.sql` also revokes the auto-exposed REST roles (`anon`, `authenticated`) on every table and grants the reader `USAGE` on the `extensions` schema where PostGIS lives.

`publish` runs `sql/schema_postgis.sql` then `sql/schema_app.sql` (facility tables and the read-only role; it drops the retired `change_context`, `timeline_months` and `likely_cause`), makes the database hold exactly the runs in the SQLite file, loads `facilities` and `facility_changes`, then sets the reader password.

| Variable | Used by | Default / note |
|---|---|---|
| `GEMINI_API_KEY` | agent | required for chat with `LLM_PROVIDER=google` (default); read from the environment, never stored |
| `GEMINI_MODEL` | agent, `/api/health` | `gemini-3.5-flash` |
| `LLM_PROVIDER` | agent | `google` (default) or `openrouter` (same model, for hosts where Google's API refuses the region) |
| `OPENROUTER_API_KEY`, `AGENT_OPENROUTER_MODEL` | agent | only with `LLM_PROVIDER=openrouter`; model defaults to `google/<GEMINI_MODEL>` |
| `DATABASE_URL` | `app.publish` only | owner connection (SQLAlchemy URL) |
| `READER_PASSWORD` | `app.publish` | password given to `s2cd_reader` |
| `READER_DATABASE_URL` | API, agent, evals | connection as `s2cd_reader` |
| `AGENT_DAILY_CAP` | API | `300` chat calls per day, whole service |
| `AGENT_RATE` | API | `6/minute` per client IP |
| `DB_RATE` | API | `30/minute` per client IP for `POST /api/db/query` |
| `COMPARE_RATE` | API | `10/minute` per client IP for date lookups and comparison submissions |
| `COMPARE_DAILY_CAP` | API | `12` new (uncached) comparisons per day, whole service |
| `EVAL_API_URL` | evals | `http://127.0.0.1:8000`; start the API with a high `AGENT_RATE` for evals |

## API

See `docs/api.md` for every endpoint and its shape. By module:

- `app/results.py` (shared by the endpoints and the agent tools): `resolve_run`, `change_type_summary`, `facility_rows`, `facilities_geojson`, `aoi_stats`, and `brief`, a deterministic markdown monitoring brief (headline, top facilities, change types, caveats, follow-up list) built from the database with no LLM.
- `app/explorer.py`: `/api/db/schema` (tables, row counts, columns, geometry SRID), `/api/db/examples` (the titled blocks of `sql/queries_postgis.sql` with `:'run_id'` filled in) and `/api/db/query` (guarded SELECT, 500 rows max, `elapsed_ms`; a geometry column comes back as a GeoJSON FeatureCollection converted to EPSG:4326, and the table shows the cell as `geometry`).
- `app/compare_jobs.py` + `src/s2cd/compare.py`: `POST /api/compare` validates both dates (`YYYY-MM-DD`, `provided-before`/`provided-after`; not in the future; after later than before), returns a cached result at once, otherwise queues one job. One worker thread runs `s2cd.scenes.fetch_scene` for each date and `pipeline.run` (no IR-MAD, no figures), then writes `changes.geojson`, `summary.json` and the WebP overlays to `data/compare/<pair>/`. At most 5 jobs wait, and new uncached jobs are capped per day. Cloudy or uncovered dates come back as a friendly `error` message. Compare results live on disk, not in the database, so the analyst cannot query them.
- Chat: `POST /api/agent/chat` with `{"messages":[{"role","content"}]}` returning `{answer, actions, charts, tables, tool_calls}`. Action types: `fly_to {bbox}`, `highlight {run_id, ids}`, `show_run {run_id}`.

## Agent design

- **Framework:** `create_deep_agent` on `ChatGoogleGenerativeAI`, or on `ChatOpenAI` through OpenRouter (`LLM_PROVIDER=openrouter`). deepagents also injects planning (`write_todos`), filesystem, shell and sub-agent (`task`) tools. They are **switched off** with a harness profile (`excluded_tools`), because a question takes 1 to 4 tool calls, there is nothing to plan, delegate or write, and every extra tool is attack surface. The model only sees the 12 tools below.
- **Tools** (`app/tools.py`, typed, docstrings are what the model reads, each returns compact JSON or `{"error": ...}`): `list_runs`, `area_summary(group_by)`, `change_type_summary(run_id?)` (ha and count per change_type and brief_category), `facility_summary(facility_id?)` (register plus change per facility), `aoi_stats(geometry)` (same logic as the endpoint), `top_changes(n, order_by, filters)` (ids + bbox), `explain_change(id)`, `read_context(topic)` (background notes from `app/knowledge/*.md`, topics listed in `00_index.md`), `readonly_sql(query)`, `map_action`, `make_chart`, `make_table`. Runs default to the provided pair (robust CVA, nominal 2023-08-12 to 2023-09-02).
- **Explainability:** the response includes `tool_calls` (name + args, including the SQL), so the UI can show how a number was obtained. `highlight` ids are checked against the database before they reach the map.
- **Honesty:** the system prompt (`app/agent.py`) makes the agent a mine-monitoring analyst with RGB-only, two-date evidence. It cites numbers from tools, uses `read_context` for background, leads with the finding, then the number, then the caveat, and states the caveats: the dates are nominal mosaic dates (true acquisitions 2023-07-01/07-04 and 2023-08-25/09-02, an effective gap of 52 to 60 days), confidence is a magnitude score and not accuracy, and `change_type` is a rule-based hypothesis.

## Security model

| Layer | What it does |
|---|---|
| Read-only role | `s2cd_reader`: `SELECT` on 5 relations only (`change_runs`, `change_features`, `change_features_wgs84`, `facilities`, `facility_changes`), `statement_timeout=5s`, `default_transaction_read_only=on`. Writes and `pg_sleep(6)` fail. This is the real boundary. |
| REST roles | On Supabase, `anon` and `authenticated` have no privileges on these tables, so the auto-generated REST API answers 401/permission denied. |
| SQL guard (`app/guard.py`) | Shared by the agent and the explorer. sqlglot parse: exactly one `SELECT` (CTEs ok), no INTO/locking/DDL/DML anywhere in the tree, tables must be in the allow-list (no schema-qualified names, so no `pg_catalog`/`information_schema`), `pg_*`, `lo_*`, `dblink`, `set_config`, `current_setting` functions refused, `LIMIT` forced to at most 200 (agent) or 500 (explorer). |
| Input limits | Chat: 2,000 chars per message, last 10 messages, tool inputs validated. Explorer: 5,000 chars of SQL. AOI: valid lon/lat polygon, at most 2,000 vertices. Compare: dates parsed strictly, files served from a fixed list under `data/compare/<pair>/` with the pair matched against `\d{8}_\d{8}`. |
| Rate limits | slowapi per client IP keyed on the **last** `X-Forwarded-For` entry (the one Caddy appends, which a client cannot forge): chat 6/min plus a daily cap (`AGENT_DAILY_CAP`), explorer 30/min, compare 10/min. New uncached comparisons are capped per day (`COMPARE_DAILY_CAP`) and at most 5 wait in the queue; identical pairs share one job. Caps are in memory: they reset on restart and are per process (run one worker). |
| Compare cost | One job at a time; scenes are cached in `data/scenes/`, results in `data/compare/` (both git-ignored). Only B02/B03/B04 are fetched. |
| Prompt injection | The agent has no write or file tools, so a successful injection can at worst produce a wrong read-only answer. Prompt tells it to treat user and tool text as data. |
| Secrets | Keys and passwords come from the environment only; `.env.example` has placeholders; errors returned to clients are generic (details go to the server log). |
| CORS | `http://localhost:5173` and `https://s2change.dzaky.ai.id` only. |

## Evals

`app/evals/cases.yaml`: 17 cases. Expected values come from ground-truth SQL run at eval time against the same database, never hard-coded. Checks: numbers within 2 %, required tool called, required action type present (and highlighted ids equal the truth top-5), chart/table returned, refusal or "cannot answer" wording, and unchanged row counts after destructive requests. Refusal detection is a keyword regex, so it is a coarse check.

Results: `app/evals/results.md` was produced with the previous data model (`likely_cause`, a timeline case) and is **stale**; the cases were updated for `change_type` and the facility register but the evals were not re-run after that change.

## Tests

`uv run --all-extras pytest tests/test_app.py -q`: SQL guard accept/reject lists, collector validation and per-request isolation, the chat contract with a fake agent, input limits, per-IP rate limit, daily cap, CORS, and friendly 502s. No network, LLM or database needed. The explorer, AOI, brief and compare endpoints have no automated tests; they were exercised by hand against Supabase.
