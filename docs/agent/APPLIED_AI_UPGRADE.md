# Applied AI architecture

AIStora is a bounded tabular analytics agent. It does not use vector RAG: the
product currently analyzes structured CSV tables, so schema-aware local tools
are the appropriate retrieval and execution layer.

## Request path

1. `routes/chat.py` authenticates the request, restores project-scoped follow-up
   memory, applies limits, and returns a privacy-filtered response.
   API responses use no-store caching and the application sets baseline browser
   security headers plus explicit session-cookie policy.
2. `services/request_router.py` classifies the request as conversation, lookup,
   aggregation, comparison, relationship, or visualization. The decision selects
   both the model tier and the smallest allowed tool set.
3. `services/privacy.py` classifies sensitive column names. Credential-like
   columns are removed from the default LLM schema view; remaining columns carry
   classifications. Raw rows never enter the model prompt or tool observations.
4. `services/agent_service.py` exposes native Gemini function declarations for
   only the routed tools. `record_plan` is a schema-constrained plan object and
   the local runtime validates its length, uniqueness, and content.
5. `services/agent_tools.py` executes against private local DataFrames. Safe
   metadata or categorized abbreviated errors are returned to Gemini. Invalid
   attempts can be corrected up to `AGENT_MAX_CORRECTIONS`; turns, calls, time,
   rows, materialization, and groups are also bounded.
   The Gemini transport has its own deadline and output-token ceiling, and
   validated function-calling mode constrains calls to the routed declarations.
6. `services/agent_verifier.py` checks the candidate result and can return a
   failed candidate for repair before the answer is released.
7. `services/agent_history.py` stores a privacy-limited request summary. The
   trace includes latency, retries, self-corrections, token usage, estimated
   token cost, status, and failure category. No raw row values or filter values
   are stored in the audit log.

The private/public boundary is therefore:

```text
private CSV rows -> local DataFrame tools -> named local results
                         |
                         +-> safe schema, counts, categories, errors -> Gemini
                         |
                         +-> configured result privacy policy -> authenticated user
```

## Privacy policies

- `AGENT_SCHEMA_PRIVACY=classified` (default): hides credential-like columns and
  adds classifications for visible columns.
- `AGENT_SCHEMA_PRIVACY=full`: exposes all column names and types, but never rows.
- `AGENT_SCHEMA_PRIVACY=aliases`: hashes table and column names. This maximizes
  metadata privacy but is intended for metadata-only/conversational use because
  aliased names cannot currently be mapped back into executable tool arguments.
- `AGENT_RESULT_PRIVACY=masked` (default): masks identifier, financial, and
  credential-like fields in row/table responses.
- `AGENT_RESULT_PRIVACY=full`: returns authorized result rows unchanged.
- `AGENT_RESULT_PRIVACY=aggregate_only`: suppresses row-level table responses;
  aggregate results remain available.

Set the two per-million token prices in `.env` to enable cost estimates. Zero
means token counts are still measured but cost is intentionally reported as 0.

## Evaluation

`services/agent_evaluation.py` reports plan validity, execution success, answer
accuracy against exact or callable oracles, self-correction success, retries,
latency, tokens, and estimated cost. `tests/test_applied_ai_upgrade.py` contains
deterministic cases for routing, privacy, validation, correction budgets, and
the evaluation report. The full test command is:

```powershell
.\.venv-win\Scripts\python.exe -m pytest -q
```

## Honest limitations

- Column sensitivity classification is name-based, not a content DLP scanner.
- In-process cancellation and conversation memory do not coordinate across
  multiple web workers; a shared store is needed at larger scale.
- Per-user AI rate limiting is also in-process. Use a shared Redis-backed
  limiter before scaling to multiple workers or hosts.
- Cost accuracy depends on configured current provider rates.
- Model behavior still requires live evaluation with a Gemini key; deterministic
  tests use scripted model turns and local data.
- Relationship inference remains probabilistic and should be confirmed for
  ambiguous schemas.
- This system does not execute arbitrary Python or SQL and does not support
  unstructured-document RAG.
