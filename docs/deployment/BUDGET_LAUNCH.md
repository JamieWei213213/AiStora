# Small public beta: target at most $20/month

Prepared September 25, 2026. This is a launch candidate, not a deployed or
load-tested service. Existing uploads, credentials, and databases were not
migrated. Use the latest working copy in Desktop/aistora holder/AiStora-main.

## Product and scope

A general CSV analysis product with open registration and small files. Start
with upload, data preview, deterministic profiling, cleaning, and bounded AI
questions. Keep the existing advanced pipeline available for local use; the
budget profile disables connectors, the lakehouse pipeline, and nightly jobs.
The interface hides those controls in this profile.

Do not add a warehouse, vector database, Kubernetes, or more managed services
just to launch. Saved analyses, reusable reports, and guided sample-data
onboarding are the next useful product features after observing real users.

## Cost envelope, not a guaranteed bill

AWS lists a Linux Lightsail instance with public IPv4 and 2 GB RAM at $12/month:
https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html

An initial planning allowance is $12 compute + $2 backups + $3 AI + $3 reserve.
Domain registration, taxes, email delivery, and transfer overages may consume
that reserve or push the total over $20. The $2 backup and $3 AI allocations are
budgets, not verified prices for this workload. Lightsail snapshots are charged
at $0.05/GB-month; measure retained data and retention:
https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-faq-snapshots.html

Gemini's current billing documentation describes project spending controls and
possible enforcement delay. Confirm controls available to the actual account:
https://ai.google.dev/gemini-api/docs/billing

Use a dedicated project/key and set its lowest suitable spend cap with headroom.
Application quotas are request/token limits, not dollar guarantees. Concurrent
runs can exceed a token threshold; retries use more tokens, and relationship
inference does not yet report token usage to the shared ledger. The new global
request counter includes relationship requests. Verify actual provider usage.
If $20 is an absolute all-in ceiling, do not enable paid AI until the complete
cost envelope and provider controls have been checked.

The existing Terraform stack includes separately billed NAT, ALB, RDS, and
Fargate resources. Do not apply it for this budget profile.

## Included safeguards and limits

- 5 MiB per upload request; two workspaces and five tables per account.
- 10 AI requests per account per UTC day; 50 shared across all accounts.
- Standard model only; four turns, six tool calls, one retry, bounded output.
- Shared Redis counters persist across container restarts. With the configured
  Redis unavailable, AI budget operations fail closed instead of falling back
  to a fresh per-worker counter. Redis is not exposed publicly.
- A conservative 2 GiB free-disk floor stops uploads before writes. This is a
  best-effort guard, not a per-user storage quota. Other writes and concurrent
  jobs can still use disk: monitor and establish retention before launch.
- Persistent login errors, field validation, password visibility, recovery
  links expiring in 30 minutes, and session revocation after password changes.
- SMTP uses STARTTLS or TLS on 465. Reset links use a configured HTTPS origin,
  never a request Host header. Tokens are password-bound and single-use.
- Public registration is enabled with existing per-IP throttling. Email
  verification and bot challenges are not implemented; public abuse remains a
  launch risk. Provider-level spend limits are necessary, not optional.

Password recovery is unavailable until SMTP is configured. Delivery errors
are logged generically to avoid revealing account existence. Verify delivery
with a real mailbox before opening registration. No real email was sent by the
automated tests. Existing sessions must sign in again after this upgrade.

## Prepared single-server deployment

Shortcut: `infra/budget/server-setup.sh` performs steps 1-6 below on a fresh Ubuntu
server (swap, Docker, clone, `.env.budget` prompts, build, health wait). It is
idempotent; re-run it to deploy a newer `main`.

The standalone docker-compose.budget.yml runs Caddy, the app, PostgreSQL, and
Redis. Only Caddy publishes ports 80/443. It obtains HTTPS certificates for the
hostname you control. App and database volumes persist through ordinary
container replacement. Never run `docker compose down -v` on a real deployment.
Redis persists quota counters; logs rotate. No background scheduler runs.

1. Confirm the source changes and run tests. Build the image on a suitable
   machine; a 2 GB machine may struggle during image build or migrations.
2. Provision only after reviewing the full monthly allowance. Point a domain
   at the server and open 80/443, restricting SSH to the administrator.
3. Copy infra/budget/environment.example to .env.budget in the repository root.
   Set the domain, independently generated signing key and database password,
   a supported Gemini model/key, and a verified SMTP sender. Keep secrets out
   of Git; .env.* is also excluded from the Docker build context.
4. Validate without printing secrets:
   `docker compose --env-file .env.budget -f docker-compose.budget.yml config --quiet`
5. Build/start:
   `docker compose --env-file .env.budget -f docker-compose.budget.yml up -d --build`
6. Verify HTTPS, health, registration, recovery delivery, session expiry, upload,
   deterministic EDA, and one bounded live AI question. Test concurrent small
   CSV workloads and inspect memory, disk, and provider usage before publishing.
7. Configure off-server encrypted backups of PostgreSQL and uploaded files.
   Stop writes while taking a consistent paired backup. Test restoration to a
   separate environment. Server-local Docker volumes are not backups.

Container memory ceilings total 1280 MiB, leaving nominal OS headroom on a
2 GiB server. These are configured limits, not measured workload requirements.
An out-of-memory kill can still interrupt a request. This is a single-server
beta with maintenance downtime, not a highly available deployment.

## Remaining release gates

- Choose/control a domain and configure real mail delivery.
- Verify the supported model and account-level spend controls.
- Implement or choose email verification/bot protection for public signup.
- Decide data retention, account deletion, and clear privacy/support information.
- Measure the target machine under realistic concurrent workloads.
- Verify deployed backup restoration, monitoring, TLS, and error behaviour.

Do not describe the project as live, fully production-ready, or guaranteed
under $20 until these gates and the actual costs are verified.

## Verification completed locally

- Full automated suite: 261 passed (55.86 seconds), with an isolated test
  database, temporary storage, dotenv disabled, and no live model calls.
- Desktop/mobile Chrome smoke test passed: registration by Enter, mode labels,
  persistent incorrect-password feedback, password visibility, sign-in,
  workspace creation, CSV upload, and a deterministic EDA report.
- Simulated HTML server failures, rate limits, and network failures displayed
  distinct feedback. Mobile overflow and landing-page scrolling checked.
- Zero browser JavaScript exceptions in the smoke test. Screenshots inspected.
- JavaScript syntax and Git diff whitespace checks passed.
- Compose configuration validation passed using placeholder variables and
  --no-env-resolution. No real production environment file exists yet.
- Docker engine was not running. Image build, container startup, cloud capacity,
  SMTP delivery, paid AI, and backup restoration remain unverified.

### Domain choice (checked September 25, 2026)

The .com registry returned a record for aistora.com, and no record (404) for
tryaistora.com or useaistora.com. These are candidates, not guaranteed available
purchases; confirm exact registrar availability and price immediately before
buying. Preferred candidate: tryaistora.com.

Porkbun currently advertises ordinary .com domains at $11.08 per year:
https://porkbun.com/tld/com

That is about $0.93 per month when amortized, but paid annually upfront. A
planning allocation of $12 hosting + $0.93 domain + $2 backups + $3 AI leaves
about $2.07/month for taxes, email, and other costs. This is still a target,
not a verified all-inclusive bill. No domain or hosting has been purchased.

## Selected domain

The user selected tryaistora.com. The deployment environment example now uses
that hostname. Registration is not complete: Porkbun blocked the automated
search, so exact availability, checkout price, and renewal terms require
manual verification in the registrar account. No purchase or DNS change was made.

## Launch progress reported by the user

The final purchased domain is ai-stora.com (Porkbun), replacing tryaistora.com.
The user created aistora-beta in Oregon with 2 GB RAM and attached static IPv4
52.33.212.227. Screenshots show an apex A record for this IP and HTTP/HTTPS
firewall rules. Docker server 29.1.3 and Compose 2.40.3 are confirmed by the
server terminal screenshot. Application deployment remains pending.
