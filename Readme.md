# AIStora

A self-hosted, privacy-first analytics platform for small business accountants and bookkeepers — query your QuickBooks, Xero, and Shopify exports in plain English. No SQL. No data team. No data ever sent to an AI.

---

## Demo

<p>
  <img src="./demo/demo.gif" width="800" height="800"/>
</p>

---

## The problem

Small accountants and bookkeepers can't upload sensitive client data to tools like ChatGPT, and can't afford a data team. They're stuck manually digging through CSV exports to answer routine financial questions.

AIStora fixes this — upload your CSV to your own server, ask in plain English, get your answer. The AI never sees your actual data.

---

## What makes it different from ChatGPT

Your data is stored on your own server. When you ask a question, the LLM only sees the schema — column names and types — never your actual rows. The query plan is generated from the schema alone and executed locally by our in-memory engine. We also built prompt guardrails to keep query generation predictable and safe against adversarial inputs.

---

## Under the hood

- Custom DataFrame engine — no Pandas, built from scratch
- Streaming CSV parser for large exports
- PostgreSQL for persistence and auth, engine handles compute
- Gemini plans and selects native structured tools
- Named intermediate results support multi-step analysis
- Schema exploration suggests and automatically runs analyses from column types
- Approval-gated Auto clean creates a new normalized table without overwriting source data
- Local execution with limits, cancellation, audit logs, and approval gates
- Deterministic result verification can return failed results to the agent for repair
- Cost-aware model routing sends simple work to a standard model and complex work to an advanced model
- Privacy-safe run evaluation, user feedback, and successful-plan retrieval improve later requests
- Per-project success, verification, feedback, latency, and tool-usage metrics

See [AGENT_GUIDE.md](./docs/agent/AGENT_GUIDE.md) for the complete architecture, tool and
API reference, privacy model, configuration, operating guide, troubleshooting,
testing instructions, and file inventory.

See [AWS_DEPLOYMENT_GUIDE.md](./docs/deployment/AWS_DEPLOYMENT_GUIDE.md) for the S3-backed
dataset layer, private RDS PostgreSQL, ECS/Fargate deployment, Terraform,
GitHub OIDC CI/CD, and least-privilege IAM design.

See [AIStora_INTERVIEW_GUIDE.md](./docs/interview/AIStora_INTERVIEW_GUIDE.md) for the complete
project explanation, technology choices, agent and AWS walkthroughs, honest
limitations, interview questions, STAR story, demo script, and resume wording.

All project guides are indexed in [docs/README.md](./docs/README.md).

---

## Tech stack

| Layer | Tools |
|---|---|
| Backend | Flask, SQLAlchemy, Gunicorn |
| Engine | Custom DataFrame + streaming CSV parser |
| Database | PostgreSQL |
| AI / LLM | Google Gemini |
| Frontend | Vanilla JS, Tailwind CSS |
| Infra | AWS S3, RDS, ECS/Fargate, ECR, IAM, Terraform, Docker, GitHub Actions |

---

## Run it locally
```bash
git clone https://github.com/JamieWei213213/AiStora.git
cd AIStora
cp .env.example .env   # add your Gemini API key
docker-compose up --build
# open http://localhost:5001
```

---

MIT License · DSCI 551 · Fall 2025 · USC
