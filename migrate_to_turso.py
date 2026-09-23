"""One-time migration: copy your local library into Turso.

Run this once, after creating a Turso database, to carry over whatever
books are already in your local to_b.sqlite3 before you switch the app
over to the hosted database.

Usage:
    export TURSO_DATABASE_URL="libsql://your-db-name.turso.io"
    export TURSO_AUTH_TOKEN="your-token"
    python3 migrate_to_turso.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

import db

LOCAL_PATH = os.environ.get("TOB_DB_PATH", "to_b.sqlite3")


def main() -> None:
    turso_url = os.environ.get("TURSO_DATABASE_URL")
    turso_token = os.environ.get("TURSO_AUTH_TOKEN")
    if not turso_url or not turso_token:
        sys.exit(
            "Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN before running this "
            "(see the top of this file for how)."
        )

    if not os.path.exists(LOCAL_PATH):
        sys.exit(f"No local database found at {LOCAL_PATH} -- nothing to migrate.")

    local_conn = sqlite3.connect(LOCAL_PATH)
    local_conn.row_factory = sqlite3.Row
    books = [dict(row) for row in local_conn.execute("SELECT * FROM books")]
    local_conn.close()

    if not books:
        print("Local database has no books -- nothing to migrate.")
        return

    import libsql

    remote_conn = libsql.connect(database=turso_url, auth_token=turso_token)
    db.init_db(remote_conn)

    copied = 0
    for book in books:
        existing = db.find_existing(
            remote_conn, book.get("source_id"), book["title"], book.get("author")
        )
        if existing:
            continue
        db.add_book(
            remote_conn,
            title=book["title"],
            author=book.get("author"),
            cover_url=book.get("cover_url"),
            summary=book.get("summary"),
            source_id=book.get("source_id"),
            status=book.get("status", "tbr"),
            rating=book.get("rating"),
            date_finished=book.get("date_finished"),
            notes=book.get("notes"),
        )
        copied += 1

    print(f"Copied {copied} of {len(books)} book(s) to Turso "
          f"({len(books) - copied} already present, skipped).")


if __name__ == "__main__":
    main()
