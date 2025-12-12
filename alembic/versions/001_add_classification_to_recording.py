"""Add classification to recording

Revision ID: 001
Revises: 
Create Date: 2025-12-12 20:15:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add classification column to recording table
    op.add_column('recording', sa.Column('classification', sa.String(length=20), nullable=True))


def downgrade() -> None:
    # Remove classification column from recording table
    op.drop_column('recording', 'classification')
