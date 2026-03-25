"""
memory.py — Nova's persistent memory layer
===========================================
Uses SQLite to store three types of memory:

1. CONVERSATION HISTORY  — every message exchanged, used to give Claude
                           context in follow-up messages

2. FACTS                 — things Nova learns about you over time.
                           e.g. "User prefers concise reports"
                           "User's risk tolerance is moderate-aggressive"
                           Nova extracts these automatically from conversations

3. REPORTS               — past research briefs, stored so Nova can say
                           "EQB is down 8% since last week's note"

Lesson on SQLite: it's a single file database — no server, no setup.
Python ships with it built-in (import sqlite3). Perfect for a Pi.
The database file lives at ~/nova/nova.db
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("nova.memory")

DB_PATH = Path.home() / "nova" / "nova.db"


class Memory:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # lets us access columns by name
        self._init_schema()
        log.info(f"Memory initialised at {DB_PATH}")

    def _init_schema(self):
        """
        Create tables if they don't exist yet.
        IF NOT EXISTS means this is safe to run every startup.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL,        -- 'user' or 'assistant'
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS facts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,        -- e.g. 'preference', 'portfolio', 'personal'
                key         TEXT NOT NULL,        -- e.g. 'report_style'
                value       TEXT NOT NULL,        -- e.g. 'concise'
                confidence  REAL DEFAULT 1.0,     -- 0.0 to 1.0
                source      TEXT,                 -- which conversation this came from
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE(category, key)             -- one value per key, upsert on conflict
            );

            CREATE TABLE IF NOT EXISTS reports (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,        -- 'weekly', 'monthly', 'quarterly', 'single'
                ticker      TEXT,                 -- populated for single-ticker reports
                content     TEXT NOT NULL,        -- full report text
                data_json   TEXT,                 -- raw market data snapshot as JSON
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conversations_created
                ON conversations(created_at);

            CREATE INDEX IF NOT EXISTS idx_facts_category
                ON facts(category);

            CREATE INDEX IF NOT EXISTS idx_reports_type
                ON reports(report_type, created_at);
        """)
        self.conn.commit()

    # ── Conversation History ───────────────────────────────────────────────────

    def add_message(self, role: str, content: str):
        """Save a message to conversation history."""
        self.conn.execute(
            "INSERT INTO conversations (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, datetime.utcnow().isoformat())
        )
        self.conn.commit()

    def get_recent_history(self, limit: int = 20) -> list[dict]:
        """
        Get the last N messages for Claude's context window.
        We pass these to Claude as the 'messages' array so it remembers
        what was discussed earlier in the session.

        Lesson: Claude has no built-in memory between API calls. Every call
        is stateless. So we manually load history and pass it in each time.
        This is how ALL LLM memory works — it's just context management.
        """
        rows = self.conn.execute(
            """SELECT role, content FROM conversations
               ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()

        # Reverse so oldest is first (Claude expects chronological order)
        return [{"role": r["role"], "content": r["content"]}
                for r in reversed(rows)]

    def get_history_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]

    # ── Facts (long-term memory) ───────────────────────────────────────────────

    def upsert_fact(self, category: str, key: str, value: str,
                    confidence: float = 1.0, source: str = None):
        """
        Store or update a fact about the user.
        Uses INSERT OR REPLACE so updating an existing fact just overwrites it.
        """
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO facts (category, key, value, confidence, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(category, key) DO UPDATE SET
                   value = excluded.value,
                   confidence = excluded.confidence,
                   source = excluded.source,
                   updated_at = excluded.updated_at""",
            (category, key, value, confidence, source, now, now)
        )
        self.conn.commit()
        log.debug(f"Fact saved: [{category}] {key} = {value}")

    def get_facts(self, category: str = None) -> list[dict]:
        """Get all facts, optionally filtered by category."""
        if category:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE category = ? ORDER BY category, key",
                (category,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts ORDER BY category, key"
            ).fetchall()
        return [dict(r) for r in rows]

    def format_facts_for_prompt(self) -> str:
        """
        Render all stored facts as a readable block to inject into
        Claude's system prompt. This is how Nova 'remembers' across sessions.

        Example output:
            [personal] name = Robert
            [portfolio] holdings_file = ~/portfolio/holdings.csv
            [preference] report_style = concise
        """
        facts = self.get_facts()
        if not facts:
            return "No facts stored yet."
        lines = [f"[{f['category']}] {f['key']} = {f['value']}" for f in facts]
        return "\n".join(lines)

    # ── Reports ───────────────────────────────────────────────────────────────

    def save_report(self, report_type: str, content: str,
                    data: dict = None, ticker: str = None):
        """Save a completed research report."""
        self.conn.execute(
            """INSERT INTO reports (report_type, ticker, content, data_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (report_type, ticker, content,
             json.dumps(data) if data else None,
             datetime.utcnow().isoformat())
        )
        self.conn.commit()

    def get_last_report(self, report_type: str = "weekly") -> dict | None:
        """Get the most recent report of a given type."""
        row = self.conn.execute(
            """SELECT * FROM reports WHERE report_type = ?
               ORDER BY created_at DESC LIMIT 1""",
            (report_type,)
        ).fetchone()
        return dict(row) if row else None

    def get_report_summary(self, report_type: str = "weekly") -> str:
        """
        Returns a short summary of the last report for injection into
        Nova's system prompt — so she can compare current vs previous.
        """
        report = self.get_last_report(report_type)
        if not report:
            return f"No previous {report_type} report on file."

        # Just the first 500 chars — enough context without blowing token budget
        snippet = report["content"][:500].strip()
        date = report["created_at"][:10]
        return f"Last {report_type} report ({date}):\n{snippet}..."

    def close(self):
        self.conn.close()
