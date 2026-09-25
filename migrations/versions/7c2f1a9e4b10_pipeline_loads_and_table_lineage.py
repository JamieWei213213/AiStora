"""pipeline: load ledger and table lineage columns

Revision ID: 7c2f1a9e4b10
Revises: 1d8ab2e053ae
Create Date: 2026-09-25 01:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c2f1a9e4b10'
down_revision = '1d8ab2e053ae'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('table', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dataset', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('source_format', sa.String(length=10), server_default='csv', nullable=False))
        batch_op.add_column(sa.Column('load_mode', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('key_columns', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('keep_history', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('last_load_id', sa.String(length=26), nullable=True))
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_table_dataset'), ['dataset'], unique=False)

    op.create_table(
        'load',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('load_id', sa.String(length=26), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('table_id', sa.Integer(), nullable=True),
        sa.Column('dataset', sa.String(length=64), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('source', sa.String(length=80), nullable=False),
        sa.Column('original_filename', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('stage', sa.String(length=20), nullable=True),
        sa.Column('raw_bytes', sa.BigInteger(), nullable=True),
        sa.Column('rows_in', sa.Integer(), nullable=True),
        sa.Column('rows_out', sa.Integer(), nullable=True),
        sa.Column('rows_rejected', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('quality_status', sa.String(length=10), nullable=True),
        sa.Column('error_type', sa.String(length=30), nullable=True),
        sa.Column('error_message', sa.String(length=1000), nullable=True),
        sa.Column('snapshot_id', sa.BigInteger(), nullable=True),
        sa.Column('parent_snapshot_id', sa.BigInteger(), nullable=True),
        sa.Column('manifest', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], ),
        sa.ForeignKeyConstraint(['table_id'], ['table.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('load', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_load_dataset'), ['dataset'], unique=False)
        batch_op.create_index(batch_op.f('ix_load_load_id'), ['load_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_load_project_id'), ['project_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_load_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_load_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('load', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_load_user_id'))
        batch_op.drop_index(batch_op.f('ix_load_status'))
        batch_op.drop_index(batch_op.f('ix_load_project_id'))
        batch_op.drop_index(batch_op.f('ix_load_load_id'))
        batch_op.drop_index(batch_op.f('ix_load_dataset'))
    op.drop_table('load')

    with op.batch_alter_table('table', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_table_dataset'))
        batch_op.drop_column('updated_at')
        batch_op.drop_column('last_load_id')
        batch_op.drop_column('keep_history')
        batch_op.drop_column('key_columns')
        batch_op.drop_column('load_mode')
        batch_op.drop_column('source_format')
        batch_op.drop_column('dataset')
