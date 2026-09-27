"""add google_sub to users, make password_hash nullable

Revision ID: a91f2c7d3e58
Revises: e43d93da8a40
Create Date: 2026-09-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a91f2c7d3e58'
down_revision: Union[str, Sequence[str], None] = 'e43d93da8a40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch mode: SQLite can't ALTER COLUMN, so Alembic rebuilds the table.
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('google_sub', sa.String(), nullable=True))
        batch_op.alter_column('password_hash', existing_type=sa.String(), nullable=True)
        batch_op.create_index(op.f('ix_users_google_sub'), ['google_sub'], unique=True)


def downgrade() -> None:
    """Downgrade schema. Fails if Google-only users (password_hash NULL) exist - delete or
    migrate them first."""
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_index(op.f('ix_users_google_sub'))
        batch_op.alter_column('password_hash', existing_type=sa.String(), nullable=False)
        batch_op.drop_column('google_sub')
