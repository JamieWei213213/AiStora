from flask import Flask

from extensions import db
from models import Project, User
from services.agent_history import (
    finish_run,
    goal_signature,
    project_metrics,
    record_feedback,
    start_run,
    successful_examples,
)


def build_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        user = User(email="history@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        project = Project(name="History", user_id=user.id)
        db.session.add(project)
        db.session.commit()
        return app, user.id, project.id


def test_history_redacts_literals_retrieves_success_and_scores_metrics():
    app, user_id, project_id = build_app()
    with app.app_context():
        signature = goal_signature("Show orders for 'Jamie' above 12345")
        assert "jamie" not in signature
        assert "12345" not in signature

        record = start_run(
            "9cd14132-d461-47d8-9bed-4c760b72952c",
            user_id,
            project_id,
            "Calculate average sales by region",
            "interactive",
            "gemini-test",
            "standard",
        )
        finish_run(
            record,
            status="finished",
            plan=["Aggregate sales by region"],
            trace=[
                {"tool": "aggregate_rows", "status": "ok"},
                {"tool": "finish", "status": "ok"},
                {"tool": "verify_result", "status": "ok"},
            ],
            result_kind="aggregate",
            result_name="sales_by_region",
            turns=2,
            tool_calls=2,
            duration_ms=10,
            verification={"passed": True},
        )
        record_feedback(record.request_id, user_id, "up")

        examples = successful_examples(
            user_id,
            project_id,
            "average revenue grouped by region",
        )
        assert examples[0]["tools"] == ["aggregate_rows", "finish"]
        metrics = project_metrics(user_id, project_id)
        assert metrics["success_rate"] == 1.0
        assert metrics["verified_rate"] == 1.0
        assert metrics["positive_feedback_rate"] == 1.0
