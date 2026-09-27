"""create orders and stripe webhook events tables

Revision ID: 7b3e1c9a2f40
Revises: 40d2acfc94c3
Create Date: 2026-09-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b3e1c9a2f40'
down_revision: Union[str, Sequence[str], None] = '40d2acfc94c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('orders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_amount', sa.Integer(), nullable=False),
    sa.Column('amount_total', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('stripe_checkout_session_id', sa.String(), nullable=True),
    sa.Column('stripe_payment_intent_id', sa.String(), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_orders_product_id'), 'orders', ['product_id'], unique=False)
    op.create_index(op.f('ix_orders_stripe_checkout_session_id'), 'orders', ['stripe_checkout_session_id'], unique=True)
    op.create_table('stripe_webhook_events',
    sa.Column('event_id', sa.String(), nullable=False),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('stripe_webhook_events')
    op.drop_index(op.f('ix_orders_stripe_checkout_session_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_product_id'), table_name='orders')
    op.drop_table('orders')
