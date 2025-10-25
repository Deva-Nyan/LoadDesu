import sqlite3, logging
from typing import Optional
from config import DB_PATH


_conn: Optional[sqlite3.Connection] = None


def db_init():
    global _conn
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    cur = _conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cache (
            content_key TEXT NOT NULL,
            variant_key TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_unique_id TEXT,
            width INTEGER,
            height INTEGER,
            duration INTEGER,
            size INTEGER,
            fmt_used TEXT,
            title TEXT,
            source_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (content_key, variant_key)
        );
        """
    )
    _conn.commit()
    logging.info(f"[DB] cache at {DB_PATH}")

def cache_get(content_key: str, variant_key: str):
    cur = _conn.cursor()
    cur.execute("SELECT * FROM cache WHERE content_key=? AND variant_key=?", (content_key, variant_key))
    return cur.fetchone()

def cache_put(content_key: str, variant_key: str, *, kind: str, file_id: str, file_unique_id: Optional[str],
        width: Optional[int], height: Optional[int], duration: Optional[int], size: Optional[int],
        fmt_used: str, title: Optional[str], source_url: str):
    cur = _conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO cache(content_key, variant_key, kind, file_id, file_unique_id,
            width, height, duration, size, fmt_used, title, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (content_key, variant_key, kind, file_id, file_unique_id, width, height, duration, size, fmt_used, title, source_url),
    )
    _conn.commit()
    logging.info(f"[DB] saved {content_key} [{variant_key}] → {file_id}")