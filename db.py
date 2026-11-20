
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobpilot.db"

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            source_id TEXT,
            company TEXT,
            title TEXT,
            location TEXT,
            url TEXT UNIQUE,
            description TEXT,
            posted_at TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
            score INTEGER DEFAULT 0,
            recommendation TEXT,
            status TEXT DEFAULT 'Interested',
            notes TEXT DEFAULT ''
        )
        """)
        con.commit()

def upsert_job(job):
    if not job.get("url"):
        return
    with connect() as con:
        con.execute("""
        INSERT INTO jobs (
            source, source_id, company, title, location, url,
            description, posted_at, score, recommendation
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            company=excluded.company,
            title=excluded.title,
            location=excluded.location,
            description=excluded.description,
            posted_at=excluded.posted_at,
            score=excluded.score,
            recommendation=excluded.recommendation,
            fetched_at=CURRENT_TIMESTAMP
        """, (
            job.get("source"), job.get("source_id"), job.get("company"),
            job.get("title"), job.get("location"), job.get("url"),
            job.get("description"), job.get("posted_at"), job.get("score", 0),
            job.get("recommendation", "")
        ))
        con.commit()

def list_jobs():
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM jobs ORDER BY score DESC, id DESC"
        ).fetchall()]

def update_status(job_id, status):
    with connect() as con:
        con.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        con.commit()

def update_notes(job_id, notes):
    with connect() as con:
        con.execute("UPDATE jobs SET notes=? WHERE id=?", (notes, job_id))
        con.commit()
