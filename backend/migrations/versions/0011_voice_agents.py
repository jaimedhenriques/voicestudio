"""Voice agents, conversations, and the immutable telephony call log.

Revision ID: 0011_voice_agents
Revises: 0010_remote_worker_schema

Purely additive — no existing table is touched and no backfill runs, so an
existing ``omnivoice_data/`` upgrades in place with nothing to migrate.

Mirrors ``core.db::_BASE_SCHEMA`` while that startup schema remains the fallback
for bundled installs where Alembic is unavailable.

On ``telephony_calls``: the row is written for every attempt that reaches the
placement endpoint, INCLUDING refusals, and is never deleted. That is §R1
guardrail 5 in ``docs/competitive-analysis.md`` — the log is what makes the
other guardrails auditable after the fact, so "no row" and "we chose not to
record that one" must not be distinguishable states. Nothing in the app offers
a delete path for it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011_voice_agents"
down_revision: Union[str, None] = "0010_remote_worker_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            system_prompt TEXT NOT NULL DEFAULT '',
            first_message TEXT NOT NULL DEFAULT '',
            voice_profile TEXT,
            language TEXT NOT NULL DEFAULT 'en',
            llm_model TEXT,
            temperature REAL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
            title TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL DEFAULT 'browser',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL
                REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            interrupted INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_turns_conversation "
        "ON conversation_turns(conversation_id, id)"
    )

    # Destination allowlist — §R1 guardrail 4. A call may only be placed to a
    # number that is already on this list, which is what makes bulk dialling
    # structurally impossible rather than merely rate-limited.
    op.execute("""
        CREATE TABLE IF NOT EXISTS telephony_allowlist (
            e164 TEXT PRIMARY KEY,
            label TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )
    """)

    # Immutable attempt log — §R1 guardrail 5. `refused_reason` is populated
    # when a guardrail rejected the attempt; those rows are the audit trail and
    # are written BEFORE any carrier is contacted.
    op.execute("""
        CREATE TABLE IF NOT EXISTS telephony_calls (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            destination TEXT NOT NULL,
            status TEXT NOT NULL,
            refused_reason TEXT,
            disclosure_text TEXT NOT NULL DEFAULT '',
            recorded INTEGER NOT NULL DEFAULT 0,
            duration_s REAL,
            created_at REAL NOT NULL,
            ended_at REAL
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_telephony_calls_created "
        "ON telephony_calls(created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_telephony_calls_status "
        "ON telephony_calls(status)"
    )


def downgrade() -> None:
    # `telephony_calls` is deliberately NOT dropped. The log is an audit record
    # of calls actually attempted from this install; a schema rollback is not a
    # reason to destroy it, and re-running upgrade() is a no-op against an
    # existing table. The other four tables hold only configuration and
    # transcripts and are safe to drop.
    for table in ("conversation_turns", "conversations", "telephony_allowlist", "agents"):
        if _has_table(table):
            op.execute(f"DROP TABLE {table}")
