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
    # Pipeline-managed tables. ``dataset`` is the lake slug the table is
    # bound to; ``source_format`` is "csv" for the legacy upload path and
    # "parquet" for curated tables. ``last_load_id`` points at the load that
    # produced the current rows so the UI can show "as of".
    dataset = db.Column(db.String(64), nullable=True, index=True)
    source_format = db.Column(db.String(10), nullable=False, default="csv", server_default="csv")
    load_mode = db.Column(db.String(10), nullable=True)
    key_columns = db.Column(db.JSON, nullable=True)
    keep_history = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    last_load_id = db.Column(db.String(26), nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    @property
    def is_pipeline_managed(self):
        return self.source_format == "parquet" and bool(self.dataset)


class Load(db.Model):
    """The app's view of a pipeline load, synced from the lake's manifests.

    The manifest in the lake is the source of truth; this row exists so the
    UI can list loads with one query and so a load is tied to the user who
    started it. Nothing here is written by the pipeline itself.
    """

    __tablename__ = "load"

    id = db.Column(db.Integer, primary_key=True)
    load_id = db.Column(db.String(26), unique=True, nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    table_id = db.Column(db.Integer, db.ForeignKey('table.id', ondelete="SET NULL"), nullable=True)
    dataset = db.Column(db.String(64), nullable=False, index=True)
    mode = db.Column(db.String(10), nullable=False, default="replace")
    source = db.Column(db.String(80), nullable=False, default="upload")
    original_filename = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="received", index=True)
    stage = db.Column(db.String(20), nullable=True)
    raw_bytes = db.Column(db.BigInteger, nullable=True)
    rows_in = db.Column(db.Integer, nullable=True)
    rows_out = db.Column(db.Integer, nullable=True)
    rows_rejected = db.Column(db.Integer, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    quality_status = db.Column(db.String(10), nullable=True)
    error_type = db.Column(db.String(30), nullable=True)
    error_message = db.Column(db.String(1000), nullable=True)
    snapshot_id = db.Column(db.BigInteger, nullable=True)
    parent_snapshot_id = db.Column(db.BigInteger, nullable=True)
    manifest = db.Column(db.JSON, nullable=True)
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
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    project = db.relationship('Project', backref=db.backref('loads', lazy=True, cascade="all, delete-orphan"))
    table = db.relationship('Table', backref=db.backref('loads', lazy=True))

    @property
    def is_terminal(self):
        return self.status in {"succeeded", "quarantined", "failed", "rolled_back"}


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
