"""Add language to stream

Revision ID: 003
Revises: 002
Create Date: 2025-12-12 22:50:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add language column to stream table with default 'he' for Hebrew
    op.add_column('stream', sa.Column('language', sa.String(length=10), nullable=False, server_default='he'))


def downgrade() -> None:
    # Remove language column from stream table
    op.drop_column('stream', 'language')
