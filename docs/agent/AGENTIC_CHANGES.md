# Complete Agentic Upgrade Change List

## Added

- `services/agent_tools.py`
  - Eleven structured analytics tools.
  - Named intermediate results.
  - Safe filtering operators.
  - Streaming grouped aggregation.
  - Bounded joins, projections, and top-row selection.
  - Resource, row, group, time, and tool-call limits.
  - Clarification, approval, planning, and finish actions.
- `services/schema_agent.py`
  - Classifies numeric, categorical, temporal, and identifier-like columns.
  - Generates deterministic question suggestions.
  - Builds autonomous schema-exploration goals.
- `services/data_cleaning_agent.py`
  - Profiles CSV quality locally.
  - Builds conservative cleaning plans.
  - Writes approved cleaned copies without overwriting originals.
- `services/schema_service.py`
  - Refreshes project schema metadata after derived tables are created.
- `services/agent_service.py`
  - Native Gemini function-calling planner/executor loop.
  - Multi-turn tool observations and error recovery.
  - Final-result selection.
- `services/agent_memory.py`
  - Database-scoped, bounded conversation memory.
  - Memory clearing.
- `services/agent_control.py`
  - Cooperative cancellation.
  - Shared cancellation markers for multiple Gunicorn workers.
- `services/agent_audit.py`
  - Append-only JSONL tool audit trail.
  - Redaction of data rows and filter values.
- `services/agent_history.py`
  - Persists privacy-limited run evaluations and explicit feedback.
  - Retrieves similar verified successful plans without storing result rows.
  - Calculates project-level success, verification, latency, model, and tool metrics.
- `services/agent_verifier.py`
  - Applies deterministic checks to final results without another LLM call.
- `services/agent_evaluation.py`
  - Defines reusable evaluation cases for tool sequences, result kinds, and efficiency.
- `services/model_router.py`
  - Routes simple requests to the standard model and complex requests to the advanced model.
- `services/storage_service.py`
  - Provides local and S3 dataset backends behind one validated interface.
  - Keeps S3 as the durable source of truth and uses content-specific ephemeral
    cache files while the local analytics engine reads a dataset.
  - Uses the standard AWS credential provider chain so ECS task-role
    credentials replace static access keys.
- `infra/terraform/`
  - Provisions a two-AZ VPC, private Fargate and database subnets, S3, RDS,
    ECR, ECS, ALB, CloudWatch, Secrets Manager, security groups, and scoped IAM
    roles.
- `.github/workflows/deploy-aws.yml`
  - Tests the application and Terraform, obtains short-lived AWS credentials
    through GitHub OIDC, pushes immutable images, deploys ECS, and smoke-tests
    the live health endpoint.
- `AWS_DEPLOYMENT_GUIDE.md`
  - Documents provisioning, costs, CI/CD, S3/RDS behavior, and the exact IAM
    boundaries.
- `.env.example`
  - Gemini, secret-key, and agent-budget configuration template.
- `AGENT_GUIDE.md`
  - Architecture, tools, privacy, setup, operation, and testing guide.
- Agent tests covering tools, privacy, memory, cancellation, routes, UI, and
  native Gemini SDK configuration.

## Replaced

- Replaced model-generated Python expressions with native Gemini function calls.
- Replaced `secure_eval` execution with server-owned structured operations.
- Replaced the retired `google-generativeai` SDK with `google-genai`.
- Replaced one-shot query generation with an eight-turn planner/executor loop.

## Changed

- `routes/chat.py`
  - Runs the structured agent.
  - Supports named results, clarification continuation, chart approval,
    cancellation, memory clearing, trace output, deterministic verification,
    persistent run evaluation, feedback, metrics, and configurable budgets.
- `routes/tables.py`
  - Adds cleaning preview and approved apply endpoints.
  - Registers cleaned copies as new project tables.
- `routes/data.py`
  - Adds project schema refresh support.
  - Validates CSV uploads, writes durable objects through the configured
    storage backend, and rolls back objects if database persistence fails.
- `routes/databases.py`
  - Removes project dataset objects when their database metadata is deleted.
- `services/llm_service.py`
  - Uses the supported Gemini SDK.
  - Uses configurable `GEMINI_MODEL` with `gemini-3.6-flash` as the default.
  - Configures manual native function calling so raw tool results cannot be
    automatically returned to the model.
  - Recognizes Gemini quota failures without exposing raw SDK errors.
  - Retries transient server/network failures with bounded exponential backoff.
  - Maintains separately configurable standard and advanced model instances.
- `static/js/scripts.js`
  - Shows plans and tool traces.
  - Adds approval and clarification interactions.
  - Adds request cancellation and memory clearing.
  - Adds Auto analyze and clickable column-based suggestions.
  - Adds per-table Auto clean previews and approval handling.
  - Shows a clear Gemini quota card with links to AI Studio and rate-limit help.
  - Displays routing, verification, project metrics, and feedback controls.
  - Escapes rendered user and data content.
- `templates/components/app/chat_screen.html`
  - Adds agent status, activity panel, cancel control, and memory control.
- `templates/components/app/upload_screen.html`
  - Adds the Auto-clean preview and approval modal.
- `routes/tables.py`
  - Profiles S3-backed datasets through ephemeral materialization and writes
    approved cleaned copies back to S3 without changing the source object.
- `services/state_manager.py`
  - Resolves local paths or S3 URIs through the storage service before
    constructing a DataFrame.
- `Dockerfile`
  - Runs as a non-root user, adds a container health check, and uses an
    explicit production entrypoint.
- `engine/parser.py`
  - Uses standards-compliant streaming CSV parsing with quoted-field support.
- `config.py`
  - Adds environment-configurable agent budgets, routing, retry, and history settings.
- `docker-compose.yml`
  - Removes the hard-coded application secret.
  - Passes agent budget settings.
- `services/logger.py`
  - Uses timezone-aware UTC timestamps.
- `requirements.txt`
  - Uses `google-genai`.
- `.gitignore`
  - Ignores the Windows environment, audit log, and cancellation markers.
- `Readme.md`
  - Documents structured tools and links to the agent guide.

## Removed

- `services/security.py`
  - The generated-code evaluator is no longer needed because the model cannot
    submit Python code.

## Verification

- 47 automated tests pass.
- Python compilation passes.
- Frontend JavaScript syntax validation passes.
- Dependency integrity validation passes.
- Flask startup and both main pages pass.
- All 27 routes register.
- Terraform formatting and validation pass with Terraform 1.15.8 and AWS
  provider 6.57.1.
- All 11 Gemini native tool declarations validate with the installed SDK.

Live Gemini connectivity and access to `gemini-3.6-flash` were verified with
the configured API key. Continued requests require available project quota.

Not run:

- Docker runtime validation requires Docker/WSL, which is not installed
  locally. The GitHub workflow performs the Linux image build before ECS
  deployment.
