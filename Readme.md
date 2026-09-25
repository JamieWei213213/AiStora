# AIStora

A CSV analysis workspace for sales, operations, research, and everyday data questions. Upload related files, inspect data quality, and ask questions in plain English. Analysis runs on the application server; the AI planner receives your question and permitted schema metadata.

---

## Demo

<p>
  <img src="./demo/demo.gif" width="800" height="800"/>
</p>

---

## Want to use it?

AIStora is not hosted publicly — the demo above is a recording of the app
running locally. If you'd like to try it with your own data, get a walkthrough,
or use it for your team, email me at **[jamiejwei@gmail.com](mailto:jamiejwei@gmail.com)**
and I'll set you up.

---

## The problem

CSV files are easy to collect and harder to understand together. AIStora brings
files into a workspace, profiles their quality, and helps answer questions
without requiring SQL. Start with small datasets and inspect results before
using them to make decisions.

## Small public beta

The optional [budget deployment](docs/deployment/BUDGET_LAUNCH.md) prepares a
single-server setup for small CSVs with public registration, persistent sessions,
password recovery through configured SMTP, and shared AI usage limits. It has
not been deployed or load-tested on a cloud server. The full data-pipeline setup
remains available separately.

## What makes it different from ChatGPT

Your data is stored on your own server. When you ask a question, the LLM only sees the schema — column names and types — never your actual rows. The query plan is generated from the schema alone and executed locally by our in-memory engine. We also built prompt guardrails to keep query generation predictable and safe against adversarial inputs.

---

## Under the hood

- Custom DataFrame engine — no Pandas, built from scratch; ~250K rows/second
  including CSV parse ([benchmarks](./benchmarks/README.md))
- Streaming CSV parser: counts, minimums, maximums and top-K run in constant
  memory; filters and joins bound their materialised output
- PostgreSQL for persistence and auth, Redis for server-side session state,
  engine handles compute
- Gemini plans and selects native structured tools
- Named intermediate results support multi-step analysis
- Schema exploration suggests and automatically runs analyses from column types
- One-click deterministic EDA reports profile quality, distributions, time coverage, correlations, privacy, and limits locally
- Approval-gated Auto clean creates a new normalized table without overwriting source data
- Local execution with limits, cancellation, audit logs, and approval gates
- Deterministic result verification can return failed results to the agent for repair
- Cost-aware model routing sends simple work to a standard model and complex work to an advanced model
- Privacy-safe run evaluation, user feedback, and successful-plan retrieval improve later requests
- Per-project success, verification, feedback, latency, and tool-usage metrics
- **Data platform** (September 2026): every upload runs through a six-stage
  pipeline — validate, profile, quality gate, typed Parquet, Apache Iceberg
  merge, register — with per-dataset schema contracts, replace / append /
  merge loads, snapshot rollback, optional type-2 history, scheduled Postgres
  and Google Sheets connectors, nightly dbt marts and a pipeline-health view.
  Serverless on AWS (S3 → EventBridge → Step Functions → Lambda/DuckDB → Glue
  catalog) for about $1–2/month idle; in-process locally with no AWS at all.

See [ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the component map, request
lifecycle, the two privacy boundaries, where state lives, and the invariants
the system depends on.

See [PROJECT_GUIDE.md](./docs/project/PROJECT_GUIDE.md) for the long-form
guide: design rationale, technology choices, threat model, testing strategy
and honest limitations.

See [CHANGELOG.md](./docs/CHANGELOG.md) for the September 2026 review
remediation — what was wrong, why it mattered, and what replaced it.

See [AGENT_GUIDE.md](./docs/agent/AGENT_GUIDE.md) for the tool and API
reference, configuration, operating guide, troubleshooting and file
inventory.

See [APPLIED_AI_UPGRADE.md](./docs/agent/APPLIED_AI_UPGRADE.md) for the routed,
schema-constrained planning flow, privacy policies, evaluation metrics, tracing,
and current limitations.

See [EDA_REPORT.md](./docs/analysis/EDA_REPORT.md) for the report contents,
privacy behavior, limits, testing steps, and honest statistical limitations.

See [DATA_PLATFORM.md](./docs/DATA_PLATFORM.md) for the pipeline: the lake
layout, the stages, load modes and rollback, schema contracts, the quality
gate, orchestration, telemetry marts, connectors, cost, and the decisions
behind each piece.

See [AWS_DEPLOYMENT_GUIDE.md](./docs/deployment/AWS_DEPLOYMENT_GUIDE.md) for the S3-backed
dataset layer, private RDS PostgreSQL, ECS/Fargate deployment, Terraform,
GitHub OIDC CI/CD, and least-privilege IAM design.

All project guides are indexed in [docs/README.md](./docs/README.md).

---

## Tech stack

| Layer | Tools |
|---|---|
| Backend | Flask, SQLAlchemy, Alembic, Gunicorn |
| Engine | Custom DataFrame + streaming CSV parser + Parquet source |
| Data platform | DuckDB, Apache Iceberg (PyIceberg), Parquet, dbt, Step Functions, Lambda, EventBridge, Glue Data Catalog |
| Database | PostgreSQL |
| Sessions | Redis (filesystem fallback for local development) |
| AI / LLM | Google Gemini |
| Frontend | Vanilla JS, Tailwind CSS |
| Infra | AWS S3, RDS, ElastiCache, ECS/Fargate, ECR, Lambda, Step Functions, EventBridge, Glue, IAM, Terraform, Docker, GitHub Actions |

---

## Run it locally

```bash
git clone https://github.com/JamieWei213213/AiStora.git
cd AIStora
cp .env.example .env   # add your Gemini API key
docker compose up --build
# open http://localhost:5001
```

Compose starts the application, PostgreSQL and Redis. Database migrations run
automatically on container start. The data pipeline runs in-process against a
local lake volume; nothing in AWS is needed. Load a file from the command line
with `python -m pipeline.local_runner data.csv --project 1 --dataset invoices`.

### Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # add your Gemini API key
flask --app app db upgrade    # apply migrations
flask --app app run --port 5000
```

Without `REDIS_URL` the session store falls back to the local filesystem,
which is fine for a single process.

### Tests

```bash
pip install -r requirements-dev.txt
pytest -q                                    # 230+ tests, including tests/pipeline
pytest --cov=. --cov-report=term
```

The suite runs with no external services and no network access.

### Frontend

Tailwind is compiled to `static/css/tailwind.css` and committed; the icon
library is vendored. Node is only needed when you change templates or scripts
— see [docs/FRONTEND_BUILD.md](./docs/FRONTEND_BUILD.md).

---

MIT License · DSCI 551 · Fall 2025 · USC
