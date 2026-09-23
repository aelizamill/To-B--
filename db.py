"""SQLite storage layer for To B--.

Everything that touches the database lives here. The UI never writes SQL
directly, which means you can swap SQLite for a hosted database later
(see README) by rewriting only this file.

Dates are stored as ISO strings ('YYYY-MM-DD') so they sort correctly,
and formatted as mm/dd/yyyy only when displayed.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from typing import Any, Iterable

DB_PATH = os.environ.get("TOB_DB_PATH", "to_b.sqlite3")

STATUSES = ("tbr", "reading", "read")

STATUS_LABELS = {
    "tbr": "To be read",
    "reading": "Currently reading",
    "read": "Read",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    author        TEXT,
    cover_url     TEXT,
    summary       TEXT,
    source_id     TEXT UNIQUE,
    status        TEXT NOT NULL DEFAULT 'tbr'
                  CHECK (status IN ('tbr', 'reading', 'read')),
    rating        INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    date_finished TEXT,
    notes         TEXT,
    date_added    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_status ON books (status);
"""


# --------------------------------------------------------------------------
# connection handling
# --------------------------------------------------------------------------

def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys on and dict-like rows."""
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the tables if they don't exist. Safe to call on every start."""
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------
# date helpers
# --------------------------------------------------------------------------

def to_iso(value: Any) -> str | None:
    """Accept a date, a datetime, 'mm/dd/yyyy', or ISO text -> ISO string."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {value!r}")


def to_display(iso_text: str | None) -> str:
    """ISO string -> 'mm/dd/yyyy' for showing on screen."""
    if not iso_text:
        return ""
    try:
        return datetime.strptime(iso_text, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return iso_text


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------

def list_books(
    conn: sqlite3.Connection,
    status: str | None = None,
    sort: str = "date_added",
    search: str = "",
) -> list[sqlite3.Row]:
    """Return books, optionally filtered by status and a title/author search."""
    order = {
        "date_added": "date_added DESC",
        "date_finished": "date_finished IS NULL, date_finished DESC",
        "rating": "rating IS NULL, rating DESC",
        "title": "title COLLATE NOCASE ASC",
        "author": "author COLLATE NOCASE ASC, title COLLATE NOCASE ASC",
    }.get(sort, "date_added DESC")

    clauses: list[str] = []
    params: list[Any] = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if search.strip():
        clauses.append("(title LIKE ? OR author LIKE ?)")
        needle = f"%{search.strip()}%"
        params.extend([needle, needle])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM books {where} ORDER BY {order}"
    return conn.execute(sql, params).fetchall()


def get_book(conn: sqlite3.Connection, book_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def find_existing(
    conn: sqlite3.Connection, source_id: str | None, title: str, author: str | None
) -> sqlite3.Row | None:
    """Catch duplicates before inserting.

    Matches on the provider's id first, then falls back to a
    case-insensitive title+author match for books added by hand.
    """
    if source_id:
        row = conn.execute(
            "SELECT * FROM books WHERE source_id = ?", (source_id,)
        ).fetchone()
        if row:
            return row
    return conn.execute(
        """SELECT * FROM books
           WHERE title = ? COLLATE NOCASE
             AND IFNULL(author, '') = ? COLLATE NOCASE""",
        (title, author or ""),
    ).fetchone()


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM books GROUP BY status"
    ).fetchall()
    counts = {s: 0 for s in STATUSES}
    counts.update({r["status"]: r["n"] for r in rows})
    return counts


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------

def add_book(
    conn: sqlite3.Connection,
    *,
    title: str,
    author: str | None = None,
    cover_url: str | None = None,
    summary: str | None = None,
    source_id: str | None = None,
    status: str = "tbr",
    rating: int | None = None,
    date_finished: Any = None,
    notes: str | None = None,
) -> int:
    """Insert a book and return its id."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status}")

    iso_finished = to_iso(date_finished)
    cur = conn.execute(
        """INSERT INTO books
               (title, author, cover_url, summary, source_id,
                status, rating, date_finished, notes, date_added)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            title.strip(),
            (author or "").strip() or None,
            cover_url,
            summary,
            source_id,
            status,
            rating,
            iso_finished,
            notes,
            date.today().isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def mark_read(
    conn: sqlite3.Connection,
    book_id: int,
    rating: int | None,
    date_finished: Any,
) -> None:
    """Move a book to the Read list, recording the rating and finish date.

    This is what the TBR -> Read button calls.
    """
    iso_finished = to_iso(date_finished) or date.today().isoformat()
    conn.execute(
        """UPDATE books
              SET status = 'read', rating = ?, date_finished = ?
            WHERE id = ?""",
        (rating, iso_finished, book_id),
    )
    conn.commit()


def update_book(conn: sqlite3.Connection, book_id: int, **fields: Any) -> None:
    """Update any editable column. Unknown columns are rejected."""
    allowed = {
        "title", "author", "cover_url", "summary",
        "status", "rating", "date_finished", "notes",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Cannot update: {', '.join(sorted(unknown))}")
    if not fields:
        return

    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"Unknown status: {fields['status']}")
    if "date_finished" in fields:
        fields["date_finished"] = to_iso(fields["date_finished"])

    assignments = ", ".join(f"{col} = ?" for col in fields)
    conn.execute(
        f"UPDATE books SET {assignments} WHERE id = ?",
        (*fields.values(), book_id),
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, book_id: int, status: str) -> None:
    update_book(conn, book_id, status=status)


def delete_book(conn: sqlite3.Connection, book_id: int) -> None:
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()


# --------------------------------------------------------------------------
# export / backup
# --------------------------------------------------------------------------

def export_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every book as plain dicts, for CSV or JSON backup."""
    return [dict(row) for row in conn.execute("SELECT * FROM books ORDER BY id")]


def export_csv(conn: sqlite3.Connection) -> str:
    import csv
    import io

    rows = export_rows(conn)
    buffer = io.StringIO()
    columns: Iterable[str] = rows[0].keys() if rows else [
        "id", "title", "author", "cover_url", "summary", "source_id",
        "status", "rating", "date_finished", "notes", "date_added",
    ]
    writer = csv.DictWriter(buffer, fieldnames=list(columns))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
