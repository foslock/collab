"""add canvases table and lines canvas_id column

Revision ID: 554e8cec926c
Revises:
Create Date: 2026-03-11 22:05:37.787260

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '554e8cec926c'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create canvases table
    op.create_table(
        'canvases',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('hash_id', sa.String(8), nullable=False),
        sa.Column('owner_session_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('hash_id'),
    )
    op.create_index('ix_canvases_owner_session_id', 'canvases', ['owner_session_id'])

    # Add canvas_id column to lines (nullable initially so existing rows aren't rejected)
    op.add_column('lines', sa.Column('canvas_id', sa.Uuid(), nullable=True))
    op.create_index('ix_lines_canvas_id', 'lines', ['canvas_id'])


def downgrade() -> None:
    op.drop_index('ix_lines_canvas_id', table_name='lines')
    op.drop_column('lines', 'canvas_id')
    op.drop_index('ix_canvases_owner_session_id', table_name='canvases')
    op.drop_table('canvases')
