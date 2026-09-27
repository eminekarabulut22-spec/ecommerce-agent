"""create order_items table and add order ownership

Revision ID: e43d93da8a40
Revises: efccd4814102
Create Date: 2026-09-24 15:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e43d93da8a40'
down_revision: Union[str, Sequence[str], None] = 'efccd4814102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('order_items',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_amount', sa.Integer(), nullable=False),
    sa.Column('amount_subtotal', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_items_order_id'), 'order_items', ['order_id'], unique=False)
    op.create_index(op.f('ix_order_items_product_id'), 'order_items', ['product_id'], unique=False)

    # Orders move from "exactly one product" (product_id/unit_amount/quantity on the row) to
    # "N order_items", and gain a required owner. Existing rows are prior test-mode payments
    # made before accounts existed, so there's no user to attribute them to - this app has no
    # production data yet, so they're dropped rather than backfilled.
    op.execute('DELETE FROM orders')

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index('ix_orders_product_id')
        batch_op.drop_column('product_id')
        batch_op.drop_column('quantity')
        batch_op.drop_column('unit_amount')
        batch_op.add_column(sa.Column('user_id', sa.Uuid(), nullable=False))
        batch_op.create_index(batch_op.f('ix_orders_user_id'), ['user_id'], unique=False)
        batch_op.create_foreign_key('fk_orders_user_id_users', 'users', ['user_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.execute('DELETE FROM orders')

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_constraint('fk_orders_user_id_users', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_orders_user_id'))
        batch_op.drop_column('user_id')
        batch_op.add_column(sa.Column('unit_amount', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('quantity', sa.Integer(), nullable=False, server_default='1'))
        batch_op.add_column(sa.Column('product_id', sa.Uuid(), nullable=True))
        batch_op.create_index('ix_orders_product_id', ['product_id'], unique=False)

    op.drop_index(op.f('ix_order_items_product_id'), table_name='order_items')
    op.drop_index(op.f('ix_order_items_order_id'), table_name='order_items')
    op.drop_table('order_items')
