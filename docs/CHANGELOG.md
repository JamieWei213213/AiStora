# Changelog

## 8 September 2026 — remaining review fixes

Closes the rest of `docs/REVIEW_2026-09-04.md`. Test suite: **196 tests**
(13 new in `tests/test_review_fixes.py`).

### The CSV parser stopped silently and rejected common encodings

A field over Python's default 128 KB csv limit raised `csv.Error`, which was
printed and swallowed: parsing stopped, the upload "succeeded" with a smaller
row count, and later queries silently missed rows. A Windows-1252 file (what
Excel writes by default) raised `UnicodeDecodeError`, which was not caught, so
the upload returned a 500 with a codec message. Duplicate header names
collapsed into one column with no warning. `engine/parser.py` now detects the
encoding (UTF-8 with or without BOM, Windows-1252, Latin-1, UTF-16), raises
`CsvParseError` — shown to the uploader as a 400 — instead of printing,
raises the field limit to 1 MB, and makes duplicate or blank header names
unique (`amount`, `amount_2`, `column_3`).

### `inf` broke the response

A float column containing `inf` cast to `float('inf')`; `jsonify` emitted bare
`Infinity`, which the browser's `JSON.parse` rejects, so the whole answer was
lost. Non-finite floats are now missing values in the parser and `null` in the
display path.

### Chart labels could carry identifiers to a third party

`create_chart` sends group labels and values to QuickChart. Nothing stopped
`group_by=customer_name`, so names or emails ended up in an external GET URL.
Charts now refuse any group column that `classify_column` does not rate as
ordinary, are capped at 60 groups, and the approval prompt names the column
and the number of labels that will be sent.

### Session id was not rotated on login

The comment claimed fixation protection, but `session.clear()` keeps the same
server-side id. Login now regenerates the id through Flask-Session's
`regenerate()` — called *before* `clear()`, because `regenerate()` is a no-op
on an empty session.

### Ephemeral disk grew without bound

The S3 materialise cache kept every dataset version forever. It is now
evicted least-recently-used above `DATASET_CACHE_MAX_BYTES` (2 GB).

### Four request threads per task

Two workers × two threads while an agent run can hold a thread for 45 s meant
four slow questions starved `/health` and ECS killed the task mid-run.
`GUNICORN_THREADS` defaults to 8 (the wait is I/O on the model) and the ECS
service has a 90 s health-check grace period.

### Frontend: CDN scripts, blocked inline script, no form

Tailwind's Play CDN and `unpkg.com/lucide@latest` were unpinned third-party
scripts trusted by the CSP; the inline `lucide.createIcons()` block in
`base.html` was refused by that same CSP, so landing-page icons rendered
empty. Tailwind is now compiled to `static/css/tailwind.css` and committed
(`docs/FRONTEND_BUILD.md`), lucide 1.43.0 is vendored, the icon bootstrap is a
static file, and the CSP is `script-src 'self'; style-src 'self'`. The login
screen is a real `<form>` (Enter submits; password managers work); switching
databases clears the previous database's relationship list; rename and delete
have loading states and error handling and cannot be double-submitted; the
chat header wraps on phones instead of clipping its buttons.

### Housekeeping

`selenium` and `webdriver-manager` were removed from `requirements-dev.txt` —
`tests/test_agent_ui.py` never used them. The README now says the app is not
publicly hosted and gives a contact address.

---

## 4 September 2026 — abuse guardrails before public launch

Pre-launch review (`docs/REVIEW_2026-09-04.md`). These changes close the ways
a stranger on the internet could crash the server or run up the Gemini bill.
Test suite: **183 tests** (17 new in `tests/test_abuse_guardrails.py`).

### Login endpoint could exhaust memory

`routes/auth.py` used the raw email from the request body — up to the 50 MB
request cap — as a rate-limiter key, and `SlidingWindowRateLimiter` never
released keys. One client could OOM a 1 GB task in about a minute. The email
is now truncated to 120 characters before it is used as a key, limiter keys
longer than 160 characters are replaced by their SHA-256 hash, and the
in-memory limiter is capped at 50,000 keys with eviction.

### Rate limits were per process

Every limiter was in-process, so two Gunicorn workers doubled every limit and
N containers multiplied it again. `SharedRateLimiter` now keeps the sliding
window in Redis (a sorted set per key) whenever the app has a Redis client,
and falls back to the in-memory limiter otherwise. Routes did not change.

### No daily ceiling on model spend

Per-minute limits stop bursts, not a patient client: 20 requests a minute all
day is 28,800 agent runs. `services/usage_budget.py` adds per-user daily
request and token ceilings and a **global** daily token ceiling that protects
the API key however many accounts are registered. Counters live in Redis when
available and reset at midnight UTC. Exceeding one returns HTTP 429 with
`error_type: daily_budget` and a `Retry-After`. `/api/chat/metrics` now
reports the caller's usage against the limits. Configure with
`AGENT_DAILY_REQUESTS_PER_USER`, `AGENT_DAILY_TOKENS_PER_USER`,
`AGENT_DAILY_TOKENS_GLOBAL` (0 disables).

### Upload shape was unbounded

Column names are sent to the model on every turn, so a 5,000-column header
turned one upload into an open-ended prompt. Uploads are now rejected above
`MAX_UPLOAD_COLUMNS` (200) or with names over `MAX_COLUMN_NAME_CHARS` (64) or
containing control characters, a project may hold at most
`MAX_TABLES_PER_PROJECT` (20) tables, and a user at most
`MAX_PROJECTS_PER_USER` (10) databases.

### Oversized uploads returned HTML

`MAX_CONTENT_LENGTH` was enforced but there was no 413 handler, so Werkzeug
returned an HTML page and the UI's `response.json()` failed with a generic
message. `app.py` now returns JSON with `error_type: input_limit`.

### Simple questions were routed to the expensive model

"How many rows are in sales?" matched no keyword, classified as `general`, and
with more than one table was sent to `gemini-3.6-flash` (5–6× the price of
Flash-Lite). "many", "number", "rows", "records", "entries" now classify as
aggregation, and the bare word "over" no longer forces the comparison tier
("sales over 100" is a filter; "sales over time" still routes to advanced).

---

## 2 September 2026 — code review remediation

A review of the whole codebase produced the changes below. Each entry records
what was wrong, why it mattered, and what replaced it.

Test suite before: 74 tests, 82% line coverage. After: **163 tests, 84% line
coverage**. `routes/auth.py` went from 32% to 97%.

---

## Critical

### The router was withholding tools from the agent

`services/request_router.py` decided which tools the agent was allowed to call
by matching about thirty English keywords against the question, and fell
through to a `conversation` intent with an **empty** tool set. Ordinary
questions matched nothing:

```
NONE | conversation | Which client billed the most last quarter?
NONE | conversation | How much revenue did we bring in during Q3?
NONE | conversation | Who are my biggest customers?
NONE | conversation | Break down expenses by category
NONE | conversation | Are any invoices overdue?
OK   | aggregation  | What is the average transaction amount?
OK   | lookup       | Show me the top 10 clients by revenue
```

Five of seven plainly-phrased questions reached the agent with no way to read
data. The product's entire promise is "ask in plain English"; the router
understood about thirty words of it.

Keyword classification is now advisory. It selects the model tier and gates
`create_chart`, which sends data to a third party. Every request receives the
full analytical tool set. `join_sources` is offered only when the project has
more than one table, which narrows the model's choices without ever removing
its ability to answer.

`tests/test_request_router.py` (10 tests) asserts that every question in a
list of realistic phrasings receives analytical tools.

### Session state overflowed the browser cookie limit

The project schema, detected relationships, per-project agent memory, pending
approvals and cleaning previews all lived in Flask's signed client-side
cookie. Measured against the real serializer:

| Payload | Cookie size | Browser limit 4093 bytes |
|---|---|---|
| 3 tables × 12 columns | 578 B | fine |
| 10 tables × 40 columns | 3 974 B | marginal |
| 6 tables × 25 columns + full agent memory | **6 654 B** | **over** |
| 20 tables × 60 columns (the app's own ceiling) | **11 435 B** | **over** |

Past the limit the browser discards the cookie silently. The user is logged
out mid-session and nothing anywhere raises an error.

Session payloads are now server-side via Flask-Session: Redis in deployment,
a filesystem cachelib store as the local and CI fallback. In production an
unreachable Redis is fatal at startup rather than silently falling back to
per-container sessions, which would present as random logouts as requests move
between tasks.

Separately, the schema is no longer stored in the session at all.
`services/schema_service.active_schema()` reads it from Postgres, which
removed four hand-written session-resync call sites and made the database the
single source of truth.

`tests/test_session_size.py` (4 tests) asserts the cookie stays under the
limit at the application's configured ceiling.

### Cross-site scripting through uploaded CSV column names

`static/js/scripts.js` escaped table cell contents but not database names,
table names, **column names**, filenames or relationship labels, all of which
were interpolated into `innerHTML`.

Column names come from uploaded CSV headers. The product's stated user is an
accountant handling files supplied by clients. A CSV whose header is
`<img src=x onerror=...>` executed script in that accountant's authenticated
session — the exact threat model the product claims to address.

- Every user-controlled interpolation now passes through `escapeHtml()`.
- `escapeHtml()` itself was unsafe for attribute contexts: it used a
  `textContent` → `innerHTML` round-trip, which does not escape quotes, so a
  name containing `"` could break out of `value="..."`. It now escapes
  `& < > " ' \``.
- A `Content-Security-Policy` is now sent, with no `unsafe-inline` on
  `script-src`, so injected markup is inert even if an escape is missed.
- Names are validated server-side on the way in (`services/validation.py`).

`tests/test_frontend_escaping.py` (13 tests) reads the bundle and fails if a
known user-controlled value is interpolated without escaping.

---

## Security

### Secrets and transport

- `SECRET_KEY` now **hard-fails in production** instead of falling back to
  `os.urandom(24)`. The fallback ran once per process, so with two Gunicorn
  workers each signed sessions with a different key and users were logged out
  at random as requests landed on different workers.
- `SESSION_COOKIE_SECURE` now defaults to on in production. It can still be
  disabled explicitly, which the application logs loudly at startup — needed
  today because the deployed ALB serves plain HTTP.
- `Strict-Transport-Security` is sent on HTTPS responses.
- A configurable `Content-Security-Policy` is sent on every response.
- New `APP_ENV` setting (`development` | `production`) drives these defaults.

### Authentication

`routes/auth.py` was the least-covered module in the project at 32%, despite
being the only thing between an anonymous request and someone's financial data.

- Login and registration are now rate-limited — per client address *and* per
  targeted account, so one account cannot be hammered from many addresses and
  one address cannot spray many accounts.
- Login returns an identical error for "no such account" and "wrong password",
  so the endpoint no longer discloses which emails are registered.
- Emails are validated and normalised to lowercase, closing a duplicate-account
  path where `User@x.com` and `user@x.com` were different accounts.
- A minimum password length is enforced.
- The session is cleared and reissued on login, so a session fixed before
  authentication cannot be reused after it.
- `/api/auth/status` no longer reports `isLoggedIn: true` with a blank email
  when the account has been deleted.

`tests/test_auth_routes.py`: 14 tests, coverage 32% → 97%.

### Authorization

`POST /api/chat/cancel` accepted any well-formed UUID from any authenticated
user, so one user could cancel another's in-flight run. It now verifies the run
belongs to the caller via `agent_history.run_belongs_to_user()`, failing closed
when no run record exists.

### Privacy policy applied inconsistently

`_format_finished()` applied the result policy on the table and dataframe paths
but not the aggregate path, so grouping by a sensitive column returned values
that selecting the same column would have masked. The policy now applies on
every path.

`AGENT_RESULT_PRIVACY` now defaults to `full`. This governs what the **data
owner** sees in their own browser, and masking an accountant's own client names
in their own session protects data from the person it belongs to. `masked` and
`aggregate_only` remain available.

**This does not touch the model boundary.** `AGENT_SCHEMA_PRIVACY` still
defaults to `classified` and still drops credential columns before any prompt
is built. `PrivacyPolicy` now carries a docstring making the two boundaries
explicit, because conflating them is the easy mistake here.

### Server path leaked into column names

`DataFrame.join` disambiguated colliding columns by prefixing the right frame's
**absolute filepath**. Column names are sent to the model, so a server path
could have crossed the privacy boundary. It now uses a fixed `right.` prefix.

---

## Correctness

### Filters could return confidently wrong rows

`_matches` compared `str(actual)` against `str(expected)` whenever either side
failed to parse as a number. For ordering operators that is a lexicographic
comparison: `"9" > "10"` is true as text and false as a number. A filter
against a value the model had quoted returned wrong rows and reported nothing.

`filter_rows` now resolves a comparison strategy once, before scanning:

- Numeric value → numeric comparison; non-numeric rows do not match.
- Unambiguous date → chronological comparison. `03/04/2026` is rejected as
  ambiguous (3 April or 4 March?); `2026-03-04` and `25/12/2026` are accepted.
- Ordering an unorderable value → an explicit tool error the agent can see and
  correct, rather than a silent wrong answer.
- Equality and text operators → case-insensitive text comparison.

`tests/test_agent_comparisons.py` (7 tests).

### Type inference sampled 50 rows

A column that was numeric for its first fifty rows and contained `N/A` at row
five thousand was typed `int`, and every later value that failed to cast was
returned as the raw string — leaving one column holding two Python types.

- The sample is now 1000 rows, configurable via `CSV_TYPE_SAMPLE_ROWS`.
- Known null markers (`n/a`, `-`, `null`, `(blank)`, and similar) are treated
  as missing rather than as evidence that a column is text.
- A value that cannot be cast becomes `None`, so a column holds one type.
- A column with no observed values is typed `str`, not `int`.

### Base tables were re-read on every tool call

`_load` fetched a base table fresh each time: a database round trip, a storage
materialise and a new file handle per call. A three-step analysis paid it three
times. One run sees one immutable snapshot, so base tables are now cached for
the life of the run.

### Other engine fixes

- `DataFrame.__len__` re-parsed the entire file on every call; the count is now
  memoised.
- `top_k_by` buffered and sorted every row to return five of them; it now uses
  a bounded heap, O(k) instead of O(rows).
- Class docstrings now state precisely which operations stream and which
  materialise, rather than describing the whole engine as streaming.

---

## Operations

### Schema migrations

`db.create_all()` ran on every boot. It can create a missing table and can
never alter an existing one, so any model change required manual SQL against
the production database.

Alembic now owns the schema via Flask-Migrate. `entrypoint.sh` runs
`flask db upgrade` before starting Gunicorn. The initial migration
(`1d8ab2e053ae`) covers `user`, `project`, `table` and `agent_run` and was
verified to apply cleanly to an empty database. `create_all()` remains
available in development and tests via `AUTO_CREATE_TABLES`, and is off by
default in production.

### Dependencies

`requirements.txt` had **no version pins at all**, so the image built today and
the image built last month could contain different libraries with no commit to
explain a behaviour change. It also shipped `pytest`, `selenium` and
`webdriver-manager` into the production image.

- All runtime dependencies are pinned.
- Test tooling moved to `requirements-dev.txt`, which the Dockerfile does not
  copy. CI installs the dev file.

### Cancellation across tasks

Cancellation used on-disk marker files, which are correct within one container
and invisible to another. It now uses Redis when configured — the same instance
that backs sessions — with the on-disk markers as fallback, and TTLs so
abandoned markers expire.

### Local and deployed infrastructure

- `docker-compose.yml`: added Redis; pinned Postgres to 16; added health checks
  with `depends_on: condition: service_healthy`; moved the hardcoded
  `user`/`password` database credentials to overridable variables.
- `infra/terraform/cache.tf`: ElastiCache for Redis with encryption in transit
  and at rest, reachable only from the application tasks. **Disabled by
  default** (`enable_elasticache = false`) so adopting it is a deliberate
  decision and `terraform plan` shows no change until then.
- `REDIS_URL` and `APP_ENV` wired into the ECS task definition.
- No AWS resources were created, modified or destroyed by this work.

### Line endings

Files had **mixed** CRLF and LF endings — `static/js/scripts.js` alone had 1201
CRLF and 315 LF line terminators. This inflated every diff: an apparent 11 924
insertions across 78 files was about 963 real lines of change.

All text files are normalised to LF and `.gitattributes` now declares
`* text=auto` with explicit rules per file type, so the mix cannot return on
the next Windows checkout. Run `git add --renormalize .` once to record it.

### .gitignore

The pattern `*.json` ignored every JSON file in the repository, which would
silently hide legitimate files. Replaced with targeted credential patterns.
Added `instance/sessions/` and `.env.*`.

### Reproducible engine benchmark

`benchmarks/engine_benchmark.py` (new) times the operations the agent's tools
actually execute against a synthetic finance-style CSV, parse time included.
Previously the engine's performance was described but never measured.

On 200,000 rows: 220–280K rows/second, worst-case p95 0.93 s.

---

## Documentation

- **`docs/ARCHITECTURE.md`** (new) — component map, request lifecycle, the two
  privacy boundaries, where each kind of state lives, the data model, engine
  semantics, bounds and failure behaviour, deployment topology, and a list of
  invariants that must survive future changes.
- **`docs/project/PROJECT_GUIDE.md`** (new) — the former interview guide,
  rewritten as a technical project guide. Interview scaffolding removed
  (resume bullets, STAR story, question-and-answer coaching, "final interview
  rules"); the engineering rationale inside those sections was preserved and
  rewritten as a "Design decisions and rationale" section. Stale claims updated.
- **`benchmarks/README.md`** (new) — how to run the engine benchmark and the
  reference results.
- **`docs/CHANGELOG.md`** (this file).
- `docs/README.md` and the root `Readme.md` updated.

---

## Follow-ups not done here

Honest list of what remains.

1. **The deployed ALB still serves plain HTTP.** Session cookies travel in
   cleartext. The Terraform already supports an HTTPS listener and an HTTP
   redirect; it needs `certificate_arn` set, which needs a certificate for a
   domain. This is the highest-value remaining item.
2. **ElastiCache is defined but disabled.** Until `enable_elasticache = true`,
   the deployed environment uses the per-container session fallback. Correct
   for a single task; not for two.
3. **No CSRF tokens.** Currently mitigated by `SameSite=Lax`, which blocks
   cross-site POSTs in current browsers. A token would be defence in depth.
4. **`project_metrics` loads every run for a project into memory** to compute
   counters. Fine at present scale; should become aggregate queries.
5. **`services/chart_builder.py` is at 17% coverage**, the lowest in the
   project, and it is the one component that sends data off the server.
6. **No JS test runner.** `tests/test_frontend_escaping.py` is a static guard
   over the bundle, not a substitute for testing the rendering.
7. **Uncommitted working tree.** These changes are on disk and not committed.
