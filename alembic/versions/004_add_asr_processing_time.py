"""Add ASR processing time to recording

Revision ID: 004
Revises: 003
Create Date: 2025-01-10 19:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('recording', sa.Column('asr_processing_seconds', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('recording', 'asr_processing_seconds')
