"""Add speech_blocks table

Revision ID: 004
Revises: 003
Create Date: 2025-01-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'speech_blocks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stream_id', sa.Integer(), nullable=False),
        sa.Column('start_ts', sa.DateTime(), nullable=False),
        sa.Column('end_ts', sa.DateTime(), nullable=False),
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('chunk_ids', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('text', sa.Text(), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['stream_id'], ['stream.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_speech_blocks_stream_id', 'speech_blocks', ['stream_id'], unique=False)
    op.create_index('ix_speech_blocks_start_ts', 'speech_blocks', ['start_ts'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_speech_blocks_start_ts', table_name='speech_blocks')
    op.drop_index('ix_speech_blocks_stream_id', table_name='speech_blocks')
    op.drop_table('speech_blocks')
