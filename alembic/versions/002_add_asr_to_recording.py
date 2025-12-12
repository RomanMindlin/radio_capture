"""Add ASR fields to recording

Revision ID: 002
Revises: 001
Create Date: 2025-12-12 21:52:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add ASR columns to recording table
    op.add_column('recording', sa.Column('transcript', sa.Text(), nullable=True))
    op.add_column('recording', sa.Column('transcript_json', sa.JSON(), nullable=True))
    op.add_column('recording', sa.Column('asr_model', sa.String(length=50), nullable=True))
    op.add_column('recording', sa.Column('asr_confidence', sa.Float(), nullable=True))
    op.add_column('recording', sa.Column('asr_ts', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove ASR columns from recording table
    op.drop_column('recording', 'asr_ts')
    op.drop_column('recording', 'asr_confidence')
    op.drop_column('recording', 'asr_model')
    op.drop_column('recording', 'transcript_json')
    op.drop_column('recording', 'transcript')
