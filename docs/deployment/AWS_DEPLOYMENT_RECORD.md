# AIStora AWS Deployment Record

Deployment completed and verified on **August 3, 2026**.

This file records what was actually done. The companion
`AWS_DEPLOYMENT_GUIDE.md` explains the design and interview concepts in more
depth.

## Final result

| Item | Deployed value |
|---|---|
| AWS account | `243714546940` |
| Region | `us-west-2` |
| Application | <http://aistora-dev-632736863.us-west-2.elb.amazonaws.com> |
| Health check | <http://aistora-dev-632736863.us-west-2.elb.amazonaws.com/health> |
| ECS cluster/service | `aistora-dev` / `aistora-dev` |
| Current tested task revision | `aistora-dev:5` |
| Dataset bucket | `aistora-dev-243714546940-us-west-2-datasets` |
| Terraform state bucket | `aistora-terraform-state-243714546940-us-west-2` |
| RDS | Private PostgreSQL 16, `db.t4g.micro` |
| ECR repository | `aistora-dev` |
| CloudWatch log group | `/ecs/aistora-dev` |
| Standard AI model | `gemini-3.1-flash-lite` |
| Advanced routed model | `gemini-3.6-flash` |
| Monthly AWS budget alert | `$50`, email notifications at 50%, 80%, and 100% |

The final health response was:

```json
{"database":"connected","status":"ok"}
```

## Architecture that was deployed

```text
Browser
  |
  v
Public Application Load Balancer (HTTP development endpoint)
  |
  v
ECS/Fargate task in private application subnets
  |                         |                         |
  | PostgreSQL over TLS     | S3 API                  | Gemini HTTPS API
  v                         v                         v
Private encrypted RDS   Private encrypted S3     NAT gateway

GitHub Actions --OIDC--> scoped deploy role --> ECR and ECS
ECS execution role ---------------------------> ECR, logs, runtime secrets
ECS task role -------------------------------> one bucket's datasets/* prefix
Terraform -----------------------------------> encrypted versioned S3 state
```

## Every deployment step

### 1. Protected the AWS account and verified the identity

- Stopped using the AWS root session for deployment work.
- Signed in as the non-root IAM user `jamie-bootstrap-admin`.
- Used the AWS CLI profile `aistora-bootstrap`.
- Set the profile region to `us-west-2`.
- Verified the identity immediately before planning and applying:

```text
Account: 243714546940
ARN: arn:aws:iam::243714546940:user/jamie-bootstrap-admin
```

No root access key and no long-lived AWS key for GitHub were created.

### 2. Installed and used the deployment tools

- AWS CLI v2 for identity checks, budgets, secret updates, and verification.
- Terraform for repeatable AWS infrastructure.
- Git and GitHub pull requests for reviewed changes.
- GitHub Actions and Docker for clean Linux builds and container health tests.

Terraform was run from `infra/terraform`. Local deployment settings are kept
in ignored `terraform.tfvars` and `backend.hcl` files.

### 3. Created cost alerts before the application stack

Created AWS Budget `aistora-monthly-50` with a `$50` monthly limit.

The following actual-spend percentage alerts were verified for
`jamiejwei@gmail.com`:

- 50% = `$25`
- 80% = `$40`
- 100% = `$50`

An AWS Budget is an alert, not a hard spending limit. NAT Gateway, ALB, RDS,
Fargate, Secrets Manager, logs, and data transfer may continue accumulating
charges after an alert is sent.

### 4. Created and hardened remote Terraform state

Created:

```text
aistora-terraform-state-243714546940-us-west-2
```

Configured:

- S3 versioning;
- SSE-S3 encryption;
- all four public-access blocks;
- bucket-owner-enforced object ownership;
- a policy denying non-TLS access;
- native S3 state locking with `use_lockfile = true`.

During the safety check, Terraform warned that `backend.hcl` was being passed
without an S3 backend declaration. Deployment was stopped before applying the
application stack. I added this required declaration:

```hcl
terraform {
  backend "s3" {}
}
```

Terraform was then reinitialized with:

```powershell
terraform init -reconfigure -backend-config=backend.hcl
```

The remote state object was verified at:

```text
s3://aistora-terraform-state-243714546940-us-west-2/aistora/dev/terraform.tfstate
```

### 5. Reviewed the first Terraform plan

The first approved plan was exactly:

```text
Plan: 64 to add, 0 to change, 0 to destroy.
```

It was verified against account `243714546940`, region `us-west-2`, with no
pre-existing GitHub OIDC provider collision.

### 6. Applied the infrastructure and handled the RDS restriction

Terraform successfully created the network, ALB, S3, IAM, ECR, ECS cluster,
Secrets Manager entries, and CloudWatch resources. AWS then rejected the RDS
request because the new/free-tier account does not permit the planned seven-day
backup retention period.

I made backup retention configurable and set this development account to one
day:

```hcl
database_backup_retention_days = 1
```

I also made the RDS SSL parameter behavior explicit:

```hcl
apply_method = "pending-reboot"
```

That removed provider drift. The safe resume plan was:

```text
Plan: 5 to add, 0 to change, 0 to destroy.
```

The resume apply created RDS, the remaining IAM policies, ECS task definition,
and ECS service. The service began at zero tasks until a real image existed.

### 7. Provisioned the data and runtime layers

Terraform created:

- a two-Availability-Zone VPC layout;
- public ALB subnets;
- private ECS application subnets;
- isolated private RDS subnets;
- one cost-aware NAT gateway;
- an S3 gateway endpoint;
- an encrypted, versioned, non-public dataset bucket;
- private encrypted RDS PostgreSQL;
- immutable, scan-on-push ECR storage;
- ECS/Fargate, target group, listener, health checks, and rollback;
- CloudWatch application logs;
- separate execution, task, and GitHub deployment roles.

RDS was verified as `available`, encrypted, not publicly accessible, and
reachable by the application through TLS. The dataset bucket was verified with
AES-256 server-side encryption, versioning, all public-access blocks, and a
non-public bucket policy.

### 8. Moved runtime secrets into AWS Secrets Manager

Terraform created placeholders without putting the Gemini key into Terraform
variables or state. The existing local Gemini key was copied into Secrets
Manager through a temporary file that was overwritten and deleted immediately.
The key was never displayed in the terminal or chat.

ECS revision 4 injects these secret names at startup:

```text
DB_PASSWORD
GEMINI_API_KEY
SECRET_KEY
```

- RDS creates and manages its database password.
- Terraform generated the Flask session secret.
- The Gemini key lives in `aistora-dev/gemini-api-key`.

The application task role cannot retrieve Secrets Manager values. Secret
retrieval belongs to the ECS execution role.

### 9. Configured least-privilege runtime and deployment identities

Created these inline policy boundaries:

```text
aistora-dev-task             -> dataset-storage
aistora-dev-execution        -> runtime-secrets
aistora-dev-github-deploy    -> deploy-aistora-only
```

The GitHub role trust condition was verified as:

```json
{
  "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
  "token.actions.githubusercontent.com:sub":
    "repo:JamieWei213213/AiStora:ref:refs/heads/main"
}
```

This means only this repository's `main` branch can exchange a GitHub OIDC
token for short-lived AWS credentials. GitHub stores no AWS access key.

The application task role is limited to dataset object operations under the
one bucket's `datasets/*` prefix. The execution role pulls images, writes logs,
and retrieves only required runtime secrets.

### 10. Configured GitHub Actions

Created and verified these non-secret repository variables:

```text
APP_URL
AWS_DEPLOY_ROLE_ARN
AWS_REGION
ECR_REPOSITORY
ECS_CLUSTER
ECS_SERVICE
ECS_TASK_DEFINITION_FAMILY
```

The workflow gates deployment on:

1. Python tests and dependency checks;
2. Terraform formatting and validation;
3. a production Docker build and local container health check;
4. GitHub OIDC authentication;
5. immutable ECR image push;
6. ECS task-definition registration and rolling deployment;
7. ECS stability wait;
8. public `/health` smoke test.

### 11. Deployed the AWS implementation through pull request #1

- Infrastructure fix commit: `d2b96a9`
- Pull request: <https://github.com/JamieWei213213/AiStora/pull/1>
- Squash merge: `c4a1d93`
- Successful deployment workflow:
  <https://github.com/JamieWei213213/AiStora/actions/runs/30851456516>

The workflow successfully used OIDC, built and pushed the image, registered
the task definition, scaled ECS from zero to one, and passed the live health
check.

### 12. Found and fixed the retired Gemini model during a real test

The first authenticated live test proved that registration, login, RDS, and S3
worked, but Google returned `404 NOT_FOUND` for
`gemini-2.5-flash-lite` for this new user.

I queried Google's live model-list endpoint using the configured key without
printing it. I then checked Google's current model documentation and pricing.
The final routing is:

```text
Standard requests: gemini-3.1-flash-lite
Advanced requests: gemini-3.6-flash
```

At deployment time, Google listed Gemini 3.1 Flash-Lite at `$0.25` per million
text input tokens and `$1.50` per million output tokens. Model pricing and
availability can change, so check the official pricing and deprecation pages
before future model changes:

- <https://ai.google.dev/gemini-api/docs/pricing>
- <https://ai.google.dev/gemini-api/docs/deprecations>

Terraform replaced only its unused bootstrap task-definition revision. The
running ECS service stayed healthy on revision 2 during that change.

### 13. Deployed the supported model through pull request #2

- Model update commit: `210ac35`
- Pull request: <https://github.com/JamieWei213213/AiStora/pull/2>
- Squash merge: `b93fcb1`
- Successful deployment workflow:
  <https://github.com/JamieWei213213/AiStora/actions/runs/30852529973>

GitHub deployed ECS task revision 4 while revision 2 continued serving. The ALB
drained the old target before ECS removed it. Revision 4 then passed the public
database-backed health test.

### 14. Fixed Auto analyze UUID generation on the HTTP endpoint

The HTTP development endpoint does not expose `crypto.randomUUID()` in the
browser. The previous fallback used a timestamp/random string that was not a
UUID, so the backend correctly rejected Auto analyze before the agent started.

The frontend now generates an RFC 4122 UUID v4 using
`crypto.getRandomValues()` when available and a valid UUID-formatted
last-resort fallback otherwise. Strict backend UUID validation remains in
place. A regression test prevents the invalid timestamp fallback from being
reintroduced.

- Fix commit: `4a19be7`
- Pull request: <https://github.com/JamieWei213213/AiStora/pull/3>
- Squash merge: `451fed9`
- Successful deployment workflow:
  <https://github.com/JamieWei213213/AiStora/actions/runs/30875140807>

The live browser test confirmed that both browser crypto methods were absent,
then successfully ran Auto analyze through the fallback. ECS task revision 5
returned verified category totals `A=30`, `B=20`, and `C=7` with two plan
steps, three tool calls, and a 100% success metric. The synthetic project and
all of its S3 object versions were removed afterward.

## Final end-to-end test evidence

A temporary account and project were created through the public application.
A five-row CSV was uploaded and analyzed:

```text
category,amount,region
A,10,west
A,20,east
B,5,west
B,15,east
C,7,west
```

The test request was:

```text
Calculate the total amount for each category and show the result.
```

Verified results:

| Check | Result |
|---|---|
| Registration | HTTP 200 |
| Login | HTTP 200 |
| RDS connection | `connected` |
| Upload | HTTP 200 |
| Uploaded rows | 5 |
| Durable object | 1 S3 object under the project prefix |
| Object encryption | AES-256 |
| Agent response | HTTP 200, `table` |
| Routed model | `gemini-3.1-flash-lite` |
| Routing tier | `standard` |
| Plan | 2 steps |
| Tool trace | 4 events |
| Result | 3 grouped rows |
| Deterministic verification | passed |

The temporary project was deleted through the application. Every temporary S3
object version and delete marker was then removed and verified at zero. The
current revision's CloudWatch stream contained 81 inspected events and zero
error-like messages at final audit time.

The application does not currently expose account deletion, so the three smoke
tests left three inert `@example.invalid` user rows (password hashes only) in RDS.
They have no projects, datasets, or agent-run records. Add an account-deletion
workflow or remove them during a controlled database-maintenance session if a
completely empty demo database is required.

## How to operate it

### Open the application

Visit:

<http://aistora-dev-632736863.us-west-2.elb.amazonaws.com>

Create an account, create/select a database, upload a CSV, and ask a bounded
question such as:

```text
Calculate total sales by region and sort highest to lowest.
```

### Check health

```powershell
Invoke-RestMethod `
  http://aistora-dev-632736863.us-west-2.elb.amazonaws.com/health
```

### Check ECS

```powershell
aws ecs describe-services `
  --cluster aistora-dev `
  --services aistora-dev `
  --profile aistora-bootstrap `
  --region us-west-2
```

### Read current logs

```powershell
aws logs tail /ecs/aistora-dev `
  --follow `
  --profile aistora-bootstrap `
  --region us-west-2
```

### Deploy a future code change

1. Create a branch.
2. Commit the change.
3. Open a pull request.
4. Wait for test, Terraform, and container jobs to pass.
5. Merge to `main`.
6. Watch the `Test and deploy AIStora to AWS` workflow.
7. Confirm `/health` and one real application operation.

Do not manually push mutable `latest` images. The workflow uses an immutable
commit/run tag and records it in the ECS task definition.

### Rotate the Gemini key

Use the AWS Secrets Manager console in `us-west-2`:

1. Open `aistora-dev/gemini-api-key`.
2. Create a new secret value.
3. Force a new ECS deployment so new tasks receive it.
4. Test one agent request.
5. Revoke the old key in Google AI Studio.

Do not put the key in Terraform variables, GitHub variables, Docker images, or
source control.

### Watch cost

- AWS Console -> Billing and Cost Management -> Budgets.
- Open `aistora-monthly-50`.
- Investigate the service breakdown after any alert.

Scaling ECS to zero stops Fargate task compute, but it does **not** stop NAT,
ALB, RDS, Secrets Manager, S3, or log charges.

## Honest interview explanation

Use this summary:

> I deployed AIStora on ECS/Fargate behind an Application Load Balancer. CSV
> objects are encrypted and versioned in S3, while users, schemas, and agent
> run metadata are stored in private RDS PostgreSQL. ECS receives three runtime
> secrets from Secrets Manager, and the app uses a separate least-privilege
> task role limited to one S3 prefix. GitHub Actions authenticates with OIDC,
> builds immutable images, pushes to ECR, performs a rolling ECS deployment,
> and smoke-tests a database-backed health endpoint. Terraform state is
> encrypted, versioned, and locked in a separate S3 bucket.

Be ready to explain these tradeoffs honestly:

- This is a cost-aware portfolio/development deployment, not full production
  high availability.
- RDS is single-AZ, has one-day backup retention, deletion protection off, and
  skips a final snapshot on destroy.
- There is one NAT gateway, which is both a fixed cost and an AZ dependency.
- The current public ALB endpoint is HTTP. A production deployment needs a
  domain, ACM certificate, and HTTPS redirect.
- The app uses `db.create_all()`; production schema changes should use Alembic
  migrations.
- One ECS task is appropriate for the demo. Scaling agent cancellation and
  shared session coordination needs a shared service such as Redis.
- S3 versioning protects data, but a lifecycle policy should be added if old
  object versions must expire automatically.
- The bootstrap IAM user still has broad administrator access for Terraform.
  Runtime and CI roles are least-privilege, but the next security improvement
  is a scoped infrastructure-provisioning role and then removal of temporary
  administrator access.

## Resume bullet now supported by evidence

> Deployed a containerized agentic analytics service on AWS ECS/Fargate with
> encrypted S3-backed datasets and private RDS PostgreSQL, provisioned via
> Terraform; implemented least-privilege ECS roles and GitHub OIDC CI/CD with
> immutable ECR images, rolling health-checked deployments, and live agent
> verification.

## Files that implement the deployment

- `infra/terraform/` - VPC, S3, RDS, ECS, ECR, IAM, ALB, and secrets.
- `.github/workflows/deploy-aws.yml` - tested OIDC/ECR/ECS deployment.
- `Dockerfile` and `gunicorn.conf.py` - production container runtime.
- `services/storage_service.py` - local/S3 dataset abstraction.
- `routes/data.py` - validated upload and durable-object persistence.
- `routes/databases.py` - project and dataset deletion behavior.
- `config.py` - RDS, S3, Gemini, and agent runtime configuration.
- `services/llm_service.py` - standard/advanced Gemini model routing.
- `AWS_DEPLOYMENT_GUIDE.md` - architecture, IAM, deployment, and interview guide.
