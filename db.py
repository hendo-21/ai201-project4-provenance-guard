"""SQLite persistence for submissions and appeals."""

import sqlite3

DB_PATH = "provenance_guard.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            content_id TEXT PRIMARY KEY,
            user_id TEXT,
            text TEXT,
            s1_score REAL,
            s2_score REAL,
            confidence REAL,
            label TEXT,
            status TEXT,
            timestamp TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS appeals (
            appeal_id TEXT PRIMARY KEY,
            content_id TEXT,
            user_id TEXT,
            appeal_text TEXT,
            appeal_status TEXT,
            appeal_timestamp TEXT,
            reviewer_notes TEXT,
            FOREIGN KEY (content_id) REFERENCES submissions (content_id)
        )
        """
    )
    conn.commit()
    conn.close()


def insert_submission(submission):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO submissions
            (content_id, user_id, text, s1_score, s2_score, confidence, label, status, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            submission["content_id"],
            submission["user_id"],
            submission["text"],
            submission["s1_score"],
            submission["s2_score"],
            submission["confidence"],
            submission["label"],
            submission["status"],
            submission["timestamp"],
        ),
    )
    conn.commit()
    conn.close()


def get_submission(content_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_submission_status(content_id, status):
    conn = get_connection()
    conn.execute(
        "UPDATE submissions SET status = ? WHERE content_id = ?", (status, content_id)
    )
    conn.commit()
    conn.close()


def insert_appeal(appeal):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO appeals
            (appeal_id, content_id, user_id, appeal_text, appeal_status, appeal_timestamp, reviewer_notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            appeal["appeal_id"],
            appeal["content_id"],
            appeal["user_id"],
            appeal["appeal_text"],
            appeal["appeal_status"],
            appeal["appeal_timestamp"],
            appeal.get("reviewer_notes"),
        ),
    )
    conn.commit()
    conn.close()


def get_log():
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT content_id, user_id, text, s1_score, s2_score, confidence, label, status, timestamp
        FROM submissions
        ORDER BY timestamp
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
