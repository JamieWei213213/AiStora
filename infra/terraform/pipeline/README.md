# infra/terraform/pipeline

Terraform root module for the serverless data pipeline in `pipeline/`. It is
deliberately separate from the application stack in `infra/terraform/`: it
has its own state key and no dependency on the VPC, ECS or RDS, so the lake
and the catalog keep running while the app stack is destroyed and rebuilt.

What it creates:

| Resource | Purpose |
|---|---|
| S3 bucket `<project>-<env>-<account>-<region>-lake` | The lake. Prefix layout in `pipeline/keys.py`; lifecycle rules match it. EventBridge notifications on. Versioning off (Iceberg keeps its own snapshots). |
| ECR repository `<project>-<env>-pipeline` | The Lambda image built from `pipeline/Dockerfile`. |
| Lambda `<project>-<env>-pipeline` | One function for every stage, the nightly jobs and the connectors (`pipeline/lambda_handler.py`). 1769 MiB, 900 s, 10 GiB `/tmp`, no VPC. |
| Glue database | The Iceberg namespace (`<project>_<env>` by default). |
| Step Functions `<project>-<env>-ingest` | `ingest.asl.json.tftpl`: ParseEvent, Validate, Profile, Gate, Transform, Curate, Register, with `on_failure` cleanup. A quarantined file is a *Succeed* outcome. |
| EventBridge rule + Scheduler group | `Object Created` under `raw/` starts an execution; `nightly-telemetry` (03:00 UTC), `nightly-compaction` (03:30 UTC) and one schedule per `connector_schedules` entry invoke the Lambda. |
| SQS dead-letter queue, SNS topic, 5 alarms, 1 dashboard | Failed executions, Lambda errors/throttles/slow p95 and DLQ depth notify the topic. |

## Apply

```bash
cd infra/terraform/pipeline
cp backend.hcl.example backend.hcl        # same state bucket as the app stack, different key
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=backend.hcl
terraform apply -var image_tag=bootstrap   # fails at the Lambda: no image yet (expected)
```

The Lambda cannot be created until an image with `image_tag` exists in the
repository, and the repository is created by this module. The first apply
therefore runs in two steps:

1. `terraform apply -target=aws_ecr_repository.pipeline` creates the repository.
2. Run the **Pipeline** workflow from the Actions tab (*Run workflow*). It
   builds `pipeline/Dockerfile`, pushes `<sha12>-<attempt>` and prints the tag.
   The `update-function-code` step fails on this first run because the
   function does not exist yet; that is fine.
3. `terraform apply -var image_tag=<tag from step 2>`.

Later image rollouts go through the workflow (`aws lambda update-function-code`);
the function's `image_uri` is in `ignore_changes`, so a later apply does not
roll the code back to `image_tag`.

If the app stack's GitHub role should deploy the pipeline too, set
`github_deploy_role_name = "<project>-<env>-github-deploy"` (the name the app
stack gives it). This module then attaches a policy scoped to the pipeline
repository, the function, the state machine and the smoke-test prefixes.

Then add the repository variables from `terraform output github_actions_variables`
to GitHub (`AWS_DEPLOY_ROLE_ARN` comes from the app stack's outputs).

## Plug the app stack in

The app stack reads the lake when it knows the bucket. In
`infra/terraform/terraform.tfvars`:

```hcl
lake_bucket_name       = "<terraform output lake_bucket>"
pipeline_function_name = "<terraform output pipeline_function_name>"
glue_database_name     = "<terraform output glue_database>"
```

With these set, the task role gains scoped S3 and Glue access and the
container gets `PIPELINE_ENABLED=true`, `PIPELINE_BACKEND=aws`,
`LAKE_BUCKET`, `ICEBERG_CATALOG=glue`, `ICEBERG_NAMESPACE` and
`PIPELINE_FUNCTION_NAME`. With `lake_bucket_name = null` the app runs the
legacy synchronous upload path (`PIPELINE_ENABLED=false`).

## Connectors

A connector is configured by `connectors/<id>/config.json` in the lake and
scheduled with `connector_schedules = { "<id>" = "rate(6 hours)" }`. Its
`secret_ref` must name an SSM `SecureString` parameter under
`/<project>-<env>/connectors/` (see `terraform output connector_parameter_path`),
encrypted with the default `aws/ssm` key so the Lambda needs no KMS grant:

```bash
aws ssm put-parameter --type SecureString \
  --name /aistora-dev/connectors/crm_postgres \
  --value 'postgresql://user:pass@host/db'
```

## Smoke test

The deploy job drops `raw/project=0/dataset=ci_smoke/load_id=<ULID>/smoke.csv`
and polls `manifests/project=0/dataset=ci_smoke/<ULID>.json` until its
`status` is `succeeded`. Project 0 is reserved for this; the workflow's role
can write only under `raw/project=0/` and read only `manifests/project=0/`.

## Cost (idle demo, us-west-2)

| Item | Monthly |
|---|---|
| S3 lake (a few GB, lifecycle to Glacier IR after 90 days) | pennies |
| ECR (10 images x ~600 MB, first 500 MB free) | ~$0.50 |
| Lambda (1769 MiB; a 30-second load is ~$0.001; 400k GB-s free) | $0 at demo volume |
| Step Functions Standard (4,000 free transitions; ~14 per load) | $0 |
| EventBridge rule and Scheduler (14M free invocations) | $0 |
| Glue Data Catalog (first 1M objects and requests free) | $0 |
| SQS + SNS (1M requests free; email delivery free) | $0 |
| CloudWatch alarms (5 x $0.10) | $0.50 |
| CloudWatch dashboard (first 3 free) | $0 |
| CloudWatch Logs (5 GB ingest free, 30-day retention) | $0 |
| **Total** | **about $1-2** |

Nothing here runs continuously: no NAT gateway, no VPC endpoints, no
provisioned concurrency. X-Ray tracing is off and execution data is not
logged for the same reason.
