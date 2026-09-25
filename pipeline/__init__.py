"""AIStora data platform.

Everything under this package is shared by three runtimes:

* the AWS Lambda image (``pipeline.lambda_handler``), orchestrated by Step
  Functions;
* the local runner used by docker-compose and the test-suite
  (``pipeline.local_runner``), which executes the same stages in-process;
* the Flask app, which only *reads* the artifacts the pipeline writes
  (manifests, curated Parquet, gold marts) and never runs a stage itself in
  production.

The stages are pure functions of ``(settings, store, manifest)`` so that an
orchestrator adds retries, dependency tracking and visibility but no logic.
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
