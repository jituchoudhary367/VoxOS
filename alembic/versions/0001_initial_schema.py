"""Create the initial VoxOS database schema.

Revision ID: 0001_initial_schema
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("preferences", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(50), server_default="active", nullable=False),
        sa.Column("session_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(10), server_default="en", nullable=False),
    )
    op.create_index("ix_conversations_session_id", "conversations", ["session_id"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input_type", sa.String(20), server_default="text", nullable=False),
        sa.Column("language", sa.String(10), server_default="en", nullable=False),
        sa.Column("message_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_request", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), server_default="CREATED", nullable=False),
        sa.Column("risk_level", sa.String(20), server_default="LOW", nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error_info", postgresql.JSONB(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=True),
    )
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "task_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), server_default="PENDING", nullable=False),
        sa.Column("arguments", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("depends_on", postgresql.ARRAY(sa.Text()), server_default=sa.text("ARRAY[]::text[]"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_steps_task_id", "task_steps", ["task_id"])

    op.create_table(
        "tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("task_step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("task_steps.id", ondelete="SET NULL")),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(50), server_default="PENDING", nullable=False),
        sa.Column("risk_level", sa.String(20), server_default="LOW", nullable=False),
        sa.Column("approval_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_tool_calls_task_step_id", "tool_calls", ["task_step_id"])
    op.create_index("ix_tool_calls_tool_name", "tool_calls", ["tool_name"])

    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), server_default="PENDING", nullable=False),
        sa.Column("requested_action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("consequences", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tool_call_id"),
    )

    op.create_table(
        "verification_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tool_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("checks", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("tool_call_id"),
    )

    op.create_table(
        "memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("confidence", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("privacy_level", sa.String(20), server_default="private", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("ix_memory_entries_user_id", "memory_entries", ["user_id"])
    op.create_index("ix_memory_entries_memory_type", "memory_entries", ["memory_type"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="SET NULL")),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("agent", sa.String(50), nullable=True),
        sa.Column("tool", sa.String(100), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(20), server_default="LOW", nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("log_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_session_id", "audit_logs", ["session_id"])
    op.create_index("ix_audit_logs_task_id", "audit_logs", ["task_id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("memory_entries")
    op.drop_table("verification_results")
    op.drop_table("approvals")
    op.drop_table("tool_calls")
    op.drop_table("task_steps")
    op.drop_table("tasks")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("sessions")
    op.drop_table("users")
