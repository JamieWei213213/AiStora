resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/states/${local.name}-ingest"
  retention_in_days = var.log_retention_days
}

# Standard workflow: executions can take minutes and the per-transition
# price is negligible at demo volume. Express would lose the execution
# history that the failure alarm and the app's load page rely on.
resource "aws_sfn_state_machine" "ingest" {
  name     = "${local.name}-ingest"
  type     = "STANDARD"
  role_arn = aws_iam_role.state_machine.arn

  definition = templatefile("${path.module}/ingest.asl.json.tftpl", {
    lambda_arn = aws_lambda_function.pipeline.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.ingest.arn}:*"
    level                  = "ERROR"
    include_execution_data = false
  }

  tracing_configuration {
    enabled = false
  }

  depends_on = [aws_iam_role_policy.state_machine]

  tags = { Name = "${local.name}-ingest" }
}
