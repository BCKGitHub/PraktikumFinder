"""dead-link check fields on opportunities

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-17 21:00:00
"""
import sqlalchemy as sa

from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('opportunities') as batch:
        batch.add_column(sa.Column('link_status', sa.String(length=20), nullable=True))
        batch.add_column(sa.Column('link_checked_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('link_check_note', sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table('opportunities') as batch:
        batch.drop_column('link_check_note')
        batch.drop_column('link_checked_at')
        batch.drop_column('link_status')
