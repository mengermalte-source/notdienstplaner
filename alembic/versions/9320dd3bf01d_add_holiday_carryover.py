"""add_holiday_carryover

Revision ID: 9320dd3bf01d
Revises: 21ef37339335
Create Date: 2026-08-15 08:45:51.887577

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9320dd3bf01d'
down_revision: Union[str, None] = '21ef37339335'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'holidaydutycarryover',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('planning_period_id', sa.Integer(), nullable=False),
        sa.Column('holiday_key', sa.String(), nullable=False),
        sa.Column('worked', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['planning_period_id'], ['planningperiod.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_holidaydutycarryover_holiday_key', 'holidaydutycarryover', ['holiday_key'])
    op.create_index('ix_holidaydutycarryover_planning_period_id', 'holidaydutycarryover', ['planning_period_id'])
    op.create_index('ix_holidaydutycarryover_user_id', 'holidaydutycarryover', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_holidaydutycarryover_user_id', table_name='holidaydutycarryover')
    op.drop_index('ix_holidaydutycarryover_planning_period_id', table_name='holidaydutycarryover')
    op.drop_index('ix_holidaydutycarryover_holiday_key', table_name='holidaydutycarryover')
    op.drop_table('holidaydutycarryover')
