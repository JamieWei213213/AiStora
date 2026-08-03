# AIStora AWS Deployment and IAM Guide

This deployment is designed to be something you can explain in a data
engineering interview. It does not use the AWS root user, long-lived access
keys in GitHub, a public database, or a public dataset bucket.

## Architecture

```text
Browser
  |
  v
Public Application Load Balancer
  |
  v
ECS/Fargate task in private application subnets
  |                    |                         |
  | PostgreSQL         | S3 API                  | HTTPS
  v                    v                         v
Private RDS         Private S3 bucket     Gemini API through NAT
subnets             through VPC endpoint

GitHub Actions --OIDC--> scoped deploy role --> ECR + ECS only
ECS execution role ---------------------------> ECR, logs, runtime secrets
ECS task role -------------------------------> datasets/* in one S3 bucket
```

Durable state lives in RDS and S3. A Fargate task downloads an S3 object into
ephemeral storage while it analyzes the CSV. Cleaned datasets are uploaded as
new S3 objects; the source object remains unchanged.

## What Terraform provisions

- A VPC across two Availability Zones.
- Public subnets for the load balancer and one cost-aware NAT gateway.
- Private application subnets for Fargate.
- Isolated private database subnets for RDS.
- A private, encrypted, versioned S3 dataset bucket with all public access
  blocked and a policy denying non-TLS requests.
- An S3 gateway VPC endpoint.
- A private PostgreSQL 16 RDS instance with credentials generated and managed
  by RDS through Secrets Manager.
- An encrypted ECR repository with image scanning and immutable tags.
- An ECS cluster, Fargate task definition, service, health checks, deployment
  rollback, Application Load Balancer, and CloudWatch logs.
- Separate task, task-execution, and GitHub OIDC deployment roles.

The development defaults use one NAT gateway, one Fargate task after the first
deployment, a single-AZ `db.t4g.micro` RDS instance, and HTTP unless an ACM
certificate is provided. Those are honest cost-aware development choices, not
high-availability production settings.

## IAM design you should be able to explain

### GitHub deployment role

GitHub requests a short-lived web identity token. AWS verifies the token
through its GitHub OIDC provider. The trust policy requires all of the
following:

- The audience is `sts.amazonaws.com`.
- The repository exactly matches the configured owner/repository.
- The branch exactly matches `main`.

The role can push only to the AIStora ECR repository, register ECS task
definitions, update only the AIStora ECS service, and pass only AIStora's two
task roles to ECS. No AWS access key is stored in GitHub.

### ECS task execution role

This identity belongs to the ECS/Fargate platform, not to the Flask
application. It pulls images from ECR, writes container logs to CloudWatch,
and reads only three runtime secrets: the Flask session key, Gemini API key,
and RDS-managed database password.

### ECS task role

This is the identity available to `boto3` inside the application container.
It can get, put, or delete objects only under `datasets/*` in the one AIStora
bucket. It cannot list the bucket, change bucket policy, read secrets, deploy
services, or administer RDS.

### Network authorization

- The internet can reach ports 80/443 on the load balancer only.
- The Fargate task accepts port 5000 only from the load balancer security
  group.
- RDS accepts port 5432 only from the Fargate task security group.
- RDS has no public IP and its subnet route table has no internet route.

Security groups authorize connections between roles in the architecture,
instead of relying on broad IP allowlists.

## Prerequisites

Install:

- AWS CLI v2
- Terraform 1.8 or newer
- Docker Desktop
- Git

Use an IAM Identity Center or other non-root administrator session with MFA
for the one-time infrastructure bootstrap. Do not create root access keys.

Confirm the identity and region before creating anything:

```powershell
aws sts get-caller-identity
aws configure get region
```

## Provisioning

From `infra/terraform`:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
terraform fmt -recursive
terraform init
terraform validate
terraform plan -out aistora.tfplan
terraform apply aistora.tfplan
```

For team use, first create a dedicated Terraform-state S3 bucket with
versioning and encryption, copy `backend.hcl.example` to `backend.hcl`, and
initialize with:

```powershell
terraform init -backend-config=backend.hcl
```

The backend uses S3 lockfiles. DynamoDB locking is not needed for modern
Terraform.

## Configure the Gemini secret

Terraform intentionally creates only a placeholder Gemini secret so the real
API key is not written into Terraform variables or state.

1. Read the `gemini_secret_arn` Terraform output.
2. Open AWS Secrets Manager in the selected region.
3. Select that secret and choose **Retrieve secret value** / **Set secret
   value**.
4. Replace the placeholder with the Gemini API key.

The ECS execution role retrieves it at task startup. The application task role
cannot read Secrets Manager directly.

## Configure GitHub Actions

Run:

```powershell
terraform output github_actions_variables
```

Create repository-level GitHub Actions variables with the exact names and
values shown:

- `AWS_REGION`
- `AWS_DEPLOY_ROLE_ARN`
- `ECR_REPOSITORY`
- `ECS_CLUSTER`
- `ECS_SERVICE`
- `ECS_TASK_DEFINITION_FAMILY`
- `APP_URL`

No AWS access-key or secret-key GitHub secret is needed.

Pushing to `main` runs:

1. Python tests, compilation, and dependency checks.
2. Terraform formatting and validation.
3. GitHub-to-AWS OIDC authentication.
4. An immutable Docker image build tagged with the commit and run attempt.
5. ECR push.
6. ECS task-definition registration and rolling deployment.
7. ECS stability wait and a live `/health` smoke test.

## S3 behavior in the application

Production environment variables are supplied by the task definition:

```text
DATASET_STORAGE_BACKEND=s3
S3_DATASET_BUCKET=<private bucket>
S3_DATASET_PREFIX=datasets
```

Database `Table.filepath` values contain `s3://bucket/key` references. The
storage layer:

- validates that every object belongs to the configured bucket and prefix;
- uploads with server-side encryption;
- uses the ECS task role credential provider automatically;
- checks the object's ETag/version metadata;
- caches a content-specific copy on Fargate ephemeral disk;
- removes S3 objects when their tables are deleted.

There are no AWS credentials in `.env` inside ECS.

## RDS behavior

RDS generates its master password and stores it in Secrets Manager. ECS injects
the password into the container at startup. The app builds a PostgreSQL
SQLAlchemy URL from:

```text
DB_HOST
DB_PORT
DB_NAME
DB_USER
DB_PASSWORD
DB_SSLMODE=require
```

Connections use pool pre-ping and periodic recycling so replaced or stale RDS
connections are detected. The RDS PostgreSQL parameter group enforces SSL and
the application requires it.

## Cost and teardown warning

This architecture creates billable resources even when nobody is using the
site, especially the NAT gateway, Application Load Balancer, RDS, Fargate,
Secrets Manager, and CloudWatch logs. Review the AWS Billing dashboard and set
a budget alert before deployment.

Before destroying the stack, export any datasets or database state you want to
keep. The S3 bucket refuses deletion while it contains data by default.
Production should also enable RDS deletion protection, final snapshots,
Multi-AZ, and HTTPS with ACM.

## Honest interview limitations

Be ready to say:

- The development deployment is single-AZ RDS to control cost; production
  would enable Multi-AZ and deletion protection.
- One NAT gateway reduces cost but is an Availability Zone dependency.
- HTTPS requires an ACM certificate and domain; the initial ALB URL can use
  HTTP for a private demo.
- `db.create_all()` initializes the current schema; a team production system
  should use versioned Alembic migrations.
- Dataset files are durable in S3, but active analysis uses bounded Fargate
  ephemeral storage.
- Cross-task cancellation would need a shared coordination service if the
  service scaled beyond one active task.

Only claim the deployment on your resume after Terraform has applied, the
workflow has deployed an image, an upload is visible in S3, the metadata is
visible in RDS, and a live analysis has completed.
