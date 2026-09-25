# AIStora documentation

Start here.

## Understanding the system

- [Architecture](./ARCHITECTURE.md) — component map, request lifecycle, the two
  privacy boundaries, where state lives, engine semantics, bounds, deployment
  topology, and the invariants that must survive future changes.
- [Data platform](./DATA_PLATFORM.md) — the ingestion pipeline: lake layout,
  stages, load modes and rollback, schema contracts, the quality gate,
  orchestration, telemetry marts, connectors, cost, decisions and limits.
- [Project guide](./project/PROJECT_GUIDE.md) — the long-form guide: what the
  system does, why each technology was chosen, design rationale, the threat
  model, testing strategy, tradeoffs and known limitations.

## What changed and when

- [Changelog](./CHANGELOG.md) — the September 2026 code-review remediation:
  what was wrong, why it mattered, and what replaced it.
- [Agentic change log](./agent/AGENTIC_CHANGES.md) — the agent features added
  to the original application.

## Reference

- [Agent guide](./agent/AGENT_GUIDE.md) — tool and API reference, privacy
  model, configuration, operating guide, troubleshooting, file inventory.
- [Applied AI upgrade](./agent/APPLIED_AI_UPGRADE.md) — routed,
  schema-constrained planning, evaluation metrics and tracing.
- [EDA report](./analysis/EDA_REPORT.md) — report contents, privacy behaviour,
  limits and statistical caveats.

## Deployment

- [Budget public-beta launch](./deployment/BUDGET_LAUNCH.md) — single-server
  configuration, $20 target, local verification, and remaining release gates.

- [AWS deployment guide](./deployment/AWS_DEPLOYMENT_GUIDE.md) — S3, RDS,
  ECS/Fargate, IAM, Terraform and CI/CD design.
- [AWS deployment record](./deployment/AWS_DEPLOYMENT_RECORD.md) — the
  resources actually deployed, fixes made, and verification evidence.

## Quick links

- [Main README](../Readme.md)
- [Terraform infrastructure](../infra/terraform/) and the
  [pipeline module](../infra/terraform/pipeline/)
- [Pipeline module map](../pipeline/README.md)
- [Database migrations](../migrations/)
- [Automated tests](../tests/)
- [Resume source](./resume/Jamie_Wei_Resume_Agentic.tex)
