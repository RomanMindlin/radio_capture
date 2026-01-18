"""Initial schema

Revision ID: 000
Revises: 
Create Date: 2025-12-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '000'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create user table
    op.create_table('user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_username'), 'user', ['username'], unique=True)

    # Create stream table
    op.create_table('stream',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('mandatory_params', sa.JSON(), nullable=False),
        sa.Column('optional_params', sa.JSON(), nullable=False),
        sa.Column('last_up', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('current_status', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stream_name'), 'stream', ['name'], unique=True)

    # Create recording table
    op.create_table('recording',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stream_id', sa.Integer(), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Column('start_ts', sa.DateTime(), nullable=False),
        sa.Column('end_ts', sa.DateTime(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('duration_seconds', sa.Float(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['stream_id'], ['stream.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create event table
    op.create_table('event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('stream_id', sa.Integer(), nullable=True),
        sa.Column('level', sa.String(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['stream_id'], ['stream.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Create notification table
    op.create_table('notification',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bot_token', sa.String(), nullable=False),
        sa.Column('chat_id', sa.String(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('daily_report_time', sa.String(), nullable=True),
        sa.Column('thresholds', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('notification')
    op.drop_table('event')
    op.drop_table('recording')
    op.drop_index(op.f('ix_stream_name'), table_name='stream')
    op.drop_table('stream')
    op.drop_index(op.f('ix_user_username'), table_name='user')
    op.drop_table('user')
