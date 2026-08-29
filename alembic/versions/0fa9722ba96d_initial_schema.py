"""initial_schema

Revision ID: 0fa9722ba96d
Revises: 
Create Date: 2026-08-29 14:23:52.620662

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0fa9722ba96d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - baseline from main.py schema.
    
    Note: Full schema with all 20+ tables is defined in main.py init_schema().
    This migration creates the core tables. Subsequent migrations should be
    added for schema changes.
    """
    # Core users table
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), unique=True, nullable=False),
        sa.Column('username', sa.String(64), nullable=True),
        sa.Column('full_name', sa.String(120), nullable=False),
        sa.Column('role', sa.String(30), nullable=False, server_default='student'),
        sa.Column('referral_code', sa.String(24), unique=True, nullable=True),
        sa.Column('referred_by', sa.String(24), nullable=True),
        sa.Column('access_code', sa.String(40), nullable=True),
        sa.Column('access_unlocked', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    
    # Profiles table
    op.create_table(
        'profiles',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), unique=True, nullable=False),
        sa.Column('role', sa.String(30), nullable=False, server_default='STUDENT'),
        sa.Column('school_id', sa.BigInteger(), nullable=True),
        sa.Column('class_code', sa.String(40), nullable=True),
        sa.Column('linking_code', sa.String(24), unique=True, nullable=True),
        sa.Column('parent_mode', sa.String(10), nullable=False, server_default='free'),
        sa.Column('premium_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('onboarded', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    
    # Question bank
    op.create_table(
        'question_bank',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('exam_type', sa.String(20), nullable=False),
        sa.Column('subject', sa.String(80), nullable=False),
        sa.Column('topic', sa.String(120), nullable=False),
        sa.Column('class_level', sa.String(40), nullable=False),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('correct_answer', sa.Text(), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('difficulty', sa.String(20), nullable=False),
        sa.UniqueConstraint('exam_type', 'subject', 'question_text', name='uq_question_bank_exam_subject_text'),
    )
    
    # Profiles index
    op.create_index('idx_profiles_telegram_id', 'profiles', ['telegram_id'], unique=True)
    
    # Other core tables (quiz_attempts, question_responses, etc.) are defined in main.py
    # and will be created by init_schema() at startup. This migration establishes
    # the baseline; subsequent schema changes should use new migrations.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_profiles_telegram_id', 'profiles')
    op.drop_table('question_bank')
    op.drop_table('profiles')
    op.drop_table('users')
