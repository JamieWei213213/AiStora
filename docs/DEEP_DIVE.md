# AIStora — a deep explanation

*Written 8 Sep 2026 from the code as it stands (183 passing tests), not from the README.*

## 1. The problem it solves, and the one idea behind it

A bookkeeper with a client's QuickBooks export wants to ask "which customers are more than 60 days overdue?" without learning SQL. The obvious move — paste the CSV into ChatGPT — is off the table, because it is a client's financial data and it would leave the building. Hiring a data person for questions like this is absurd at that scale.

AIStora's answer is a single architectural decision that everything else hangs off: **the language model plans, the server executes, the browser renders, and no cell value ever crosses from the server to the model.** The model is shown the *shape* of the data — table names, column names, inferred types, a privacy classification per column, row counts — and from that shape it chooses a sequence of typed tool calls. The server runs those calls locally against the CSV with a hand-written engine. What goes back to the model after each call is metadata only: "result `top_overdue` exists, kind `dataframe`, 25 rows, columns `customer_id, days_overdue, balance`." The actual rows travel from the server straight to the data owner's browser on a path the model is not part of.

That is the whole product. The rest is making that split hold under pressure: adversarial CSVs, model mistakes, runaway loops, and now strangers on the internet.

## 2. What happens when you ask a question

Walk one request through, because every subsystem is on this path.

**Authorize and throttle.** `routes/chat.py` requires a logged-in session, resolves the active "database" (a *Project* in the data model — a named group of uploaded CSVs), and applies a per-user sliding-window limit (20 requests/minute, shared across workers through Redis) and, since this week, a daily request/token budget.

**Load the schema from Postgres, not from the session.** Earlier versions cached the schema in the cookie; it overflowed the 4 KB cookie limit at around six tables and logged people out silently. Now the database is the single source of truth and the session is small.

**Route.** `request_router.py` classifies the question with a keyword heuristic into an intent (aggregation, lookup, comparison, relationship, visualization, general). This does *not* decide what the agent can do — every request gets the full analytical tool set, after an earlier bug where unmatched questions arrived with no tools at all. It decides two cheaper things: which model tier to use (Flash-Lite for simple work, the more capable Flash model for comparisons, joins, charts, and long or unclassifiable questions) and whether the `create_chart` tool is offered, since that one talks to a third party.

**Redact.** `privacy.redact_schema()` builds the only representation of the data that is allowed into a prompt. Each column is classified by name into `credential`, `direct_identifier`, `identifier`, `financial`, `unstructured_text`, or `ordinary`. Credential columns are dropped entirely; the rest are annotated so the model can reason about sensitivity ("group by region, not by customer_name") without seeing a value. An `aliases` mode replaces every table and column name with a salted hash for deployments where even the names are sensitive.

**Plan and execute.** `agent_service.run_agent()` opens a Gemini chat with the system prompt, the redacted schema, any detected relationships, the last few turns of memory for this project, and up to three privacy-safe summaries of past successful runs. Gemini's native function calling is set to *validated* mode with an explicit allow-list of function names, and automatic function calling is disabled, so the loop is under the server's control:

```
model turn → tool calls → runtime.execute() → privacy metadata → model turn
                        (until finish / clarification / approval / a budget)
```

The ten tools are `record_plan`, `inspect_schema`, `count_rows`, `filter_rows`, `select_columns`, `join_sources`, `aggregate_rows`, `top_rows`, `create_chart`, `ask_clarification`, and `finish`. Every one has a typed declaration; there is no `eval`, no generated SQL, no generated Python anywhere. Each call is budget-checked, executed, appended to a trace, and written to an audit log that deliberately strips filter *values*. Intermediate results are *named* (`save_as="overdue"`) and later tools accept a name as their source, which is what lets the model do multi-step work — filter, then aggregate, then top-k — while never seeing the intermediate data.

**Verify.** When the model calls `finish`, `agent_verifier.verify_outcome()` runs ordinary code, not another model: did it actually select a named result, does the aggregate operation match the words in the question ("average" → `avg`), is a scalar finite, is the output within the row bound, and — if it answered in prose — did the question look like it wanted data? A failed required check is fed back to the model as a tool error so it can repair the analysis, up to three self-corrections.

**Render.** `_format_finished()` reads the rows from the named result and applies the *result* policy — a separate boundary from the prompt policy, governing what the data owner sees in their own browser (`full` by default, `masked` for shared screens, `aggregate_only`). The frontend escapes every value on its way into the DOM, because a CSV header is an untrusted string from a file.

**Record.** `finish_run()` stores an `AgentRun` row: model, tier, plan, trace, turns, tool calls, token counts, latency, the verification result, and later a thumbs-up/down. The question itself is not stored; a `goal_signature` is, built with an *allow-list* — a token survives only if it is a known analytical word or appears in the project's own schema, everything else becomes `[value]` or `[number]`. A blocklist leaks whatever it failed to anticipate; an allow-list fails closed.

## 3. The engine

`engine/` is a CSV parser and a DataFrame written from scratch — no Pandas — which is a real design choice, not just a course constraint. Pandas would load the whole file into memory and, more to the point, would make it easy to reach for arbitrary expressions; the hand-built engine exposes exactly the operations the tools need and nothing else.

`CsvParser` streams rows and infers types from a 1,000-row sample. Null markers like `n/a`, `-`, `(blank)` are treated as missing rather than as evidence a column is text, and a value that cannot be cast to the column's type becomes `None`, so a column holds exactly one Python type. `DataFrame` is precise about what streams: `count`, `max_by`, `min_by`, and `top_k_by` (a bounded heap) run in constant memory; `filter`, `project`, and `join` materialize their output and are capped by the agent at 25,000 rows and 500 groups. The benchmark on 200,000 rows measures roughly 220–280K rows/second including parse time, with parsing dominating.

Comparison semantics deserve a mention because they were once wrong in a way that produced confident nonsense: `filter_rows` used to compare `str(actual)` with `str(expected)`, so `"9" > "10"` was true. It now resolves a strategy before scanning — numeric if the value parses as a number, chronological if it parses as an *unambiguous* date (`03/04/2026` is rejected, `2026-03-04` is not), case-insensitive text for equality, and an explicit tool error for an ordering operator on text so the model can correct itself.

## 4. Learning without leaking

Because the run records contain signatures and plans but never values, they can be reused. `successful_examples()` finds past runs in the same project whose goal signature is similar to the new question and hands their plans to the model as "strategy hints," with the system prompt instructing it to re-check every source and column against the current schema. User feedback (thumbs up/down with a short comment) and per-project metrics — success rate, verification pass rate, latency, tool usage, routing tier mix, and estimated cost when prices are configured — are exposed at `/api/chat/metrics`. This is a small, deliberately bounded form of in-context learning rather than fine-tuning, and it is scoped per project so one client's patterns never inform another's.

## 5. The deterministic side: EDA and cleaning

Two features use no model at all, which is worth stating clearly in an interview because people assume "agentic" means "LLM does everything."

**The EDA report** (`eda_service.py`) is a planner plus a profiler. `build_eda_plan()` decides which sections apply from the schema — overview, data quality, numeric summary, categorical summary, time coverage, correlations, relationships — and `validate_eda_plan()` checks the plan against configured budgets (tables, columns, rows, distinct values, correlation columns). The profiler then streams each table once, computing missingness patterns, quantiles, IQR-based tail flags (only with at least 30 values, and labelled as distribution tails rather than confirmed errors), Pearson correlations, top categories, time coverage, duplicate rows, and relationship cardinality between tables. Every report carries a `privacy_and_limitations` section that states its own statistical caveats. It runs in about 13 ms on 500 rows and is rate-limited to 3 per minute.

**Auto clean** (`data_cleaning_agent.py`) builds a preview of proposed actions — header normalization, whitespace and case normalization, null-marker unification, exact-duplicate removal — with a fingerprint of the source file. Applying requires the preview's approval token and re-checks the fingerprint, and it always writes a *new* table. Source data is never overwritten; that is one of the seven invariants in `ARCHITECTURE.md`.

## 6. Bounds, failure, and the guardrails added this week

Every loop is bounded: 8 model turns, 12 tool calls, 3 self-corrections, 45 s wall clock polled every 1,000 rows, 25,000 materialized rows, 500 groups, 25 rendered rows, 4,000-character questions. Cancellation is a registry keyed by request id (Redis-backed with an on-disk fallback) because the cancel request lands on a different worker than the run. Failures map to specific responses: quota exhaustion gets an actionable message naming the cause, transient model errors are retried with capped exponential backoff, budget exhaustion returns 429 with the partial plan and trace, internal exceptions return a generic message and log the detail.

The September 4 review found the places a stranger could still hurt a public deployment, and they are now closed: login bodies up to 50 MB used to become permanent rate-limiter keys (an OOM in about a minute — reproduced, then fixed by truncating the email and hashing long keys); rate limits were per-process and silently doubled with two workers (now a Redis sorted-set sliding window shared across workers); there was no ceiling on model spend beyond the per-minute limit (now per-user daily request and token budgets plus a global daily token ceiling that does not care how many accounts are registered); a CSV header could have thousands of columns, each of which went into every prompt (now 200 columns, 64-character names, 20 tables per database, 10 databases per user). All of it was verified against a real two-worker Gunicorn with Redis, over HTTP.

## 7. Where the state lives, and the deployment

Identity and active project live in a small signed cookie. Everything bulky — agent memory, pending approvals, cleaning previews, cancellation flags — lives in Redis, with a filesystem fallback for local development that production refuses to start with. Datasets go to S3 with server-side encryption under a per-project prefix (local filesystem in development); the app only ever gets `s3://bucket/datasets/<project>/<uuid>/<file>` references and validates them against the configured bucket and prefix. Users, projects, tables, and runs live in Postgres with Alembic-managed migrations run from the container entrypoint.

The AWS deployment that ran in August was ECS Fargate behind an ALB in private subnets, RDS Postgres in isolated subnets with forced SSL and a managed master password, S3 through a VPC gateway endpoint, Gemini egress through a NAT gateway, secrets injected from Secrets Manager into the task definition, a least-privilege task role scoped to one S3 prefix, and GitHub Actions deploying through OIDC with immutable image tags and a health-checked rolling deploy that scales the service from zero after the first image exists. The honest cost is roughly $85–90/month for that topology, which is why the current plan is to keep the Terraform as the reference design and run the public demo on one EC2 instance with Docker Compose, Caddy for TLS, the same S3 bucket, an instance role, and SSM Parameter Store — about $10–14/month.

## 8. Limitations worth saying out loud

The privacy guarantee is about *values*. Column names still reach the model unmodified in the default mode, so a header like `IGNORE PREVIOUS INSTRUCTIONS` is a (weak) prompt-injection path; the tools are constrained so the damage is a wrong or evasive answer, and the `aliases` mode removes even that. The `create_chart` tool sends aggregate labels and values to QuickChart, and group-by labels can be identifiers — that is the one deliberate exception, gated by routing, explicit approval, and a plain-language warning, and the right fix is rendering charts locally. The parser stops silently on a `csv.Error` (a field over 128 KB) and returns a 500 on Latin-1 files; `inf` in a float column breaks the JSON response; session IDs are not rotated on login despite a comment claiming otherwise; the frontend loads Tailwind's Play CDN and an unpinned lucide build. The verifier is keyword-based and can only catch the mismatches it was written to catch. None of these undermine the core claim; all of them are on the list.

## 9. How to talk about it

The sentence that captures it: *"It's a Flask service where a Gemini agent plans typed tool calls from a redacted schema, a hand-written streaming engine executes them locally, deterministic code verifies the result and sends it back for repair, and the rows go to the browser without ever touching the model."*

The questions a good interviewer will ask, and where the answers are: *Why not just use pandas?* — bounded, auditable operations and constant-memory streaming (§3). *How do you know no data leaks?* — `_privacy_metadata()` is the only thing that builds tool responses, the audit log strips values, and `test_applied_ai_upgrade.py` guards it (§2). *What stops the agent running forever or running up your bill?* — turn/tool/time/row budgets and, as of this week, daily token ceilings (§6). *Why Fargate and then why not Fargate?* — the design is right and the bill is wrong for a demo; §7 has the numbers. *What would you do next?* — local chart rendering, rotating session IDs, the parser's encoding and truncation bugs, and building Tailwind at deploy time (§8).
