# models.py
from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    projects = db.relationship('Project', backref='owner', lazy=True)
    agent_runs = db.relationship(
        'AgentRun',
        backref='user',
        lazy=True,
        cascade="all, delete-orphan",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tables = db.relationship('Table', backref='project', lazy=True, cascade="all, delete-orphan")
    agent_runs = db.relationship(
        'AgentRun',
        backref='project',
        lazy=True,
        cascade="all, delete-orphan",
    )

class Table(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    columns_schema = db.Column(db.JSON, nullable=True) 
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    row_count = db.Column(db.Integer)


class AgentRun(db.Model):
    """Privacy-limited metadata used for evaluation and project-local learning."""

    __tablename__ = "agent_run"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.String(36), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    project_id = db.Column(
        db.Integer,
        db.ForeignKey('project.id'),
        nullable=False,
        index=True,
    )
    goal_signature = db.Column(db.String(800), nullable=False)
    mode = db.Column(db.String(20), nullable=False, default="interactive")
    model_name = db.Column(db.String(100), nullable=True)
    routing_tier = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="running", index=True)
    plan = db.Column(db.JSON, nullable=True)
    trace = db.Column(db.JSON, nullable=True)
    result_kind = db.Column(db.String(30), nullable=True)
    result_name = db.Column(db.String(100), nullable=True)
    turns = db.Column(db.Integer, nullable=False, default=0)
    tool_calls = db.Column(db.Integer, nullable=False, default=0)
    duration_ms = db.Column(db.Integer, nullable=False, default=0)
    verification = db.Column(db.JSON, nullable=True)
    error_type = db.Column(db.String(50), nullable=True)
    feedback_rating = db.Column(db.String(10), nullable=True)
    feedback_comment = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
