"""To B-- : a personal book catalogue.

Run it with:  streamlit run app.py

The layout follows the sketch: cover on the left, then title, your rating,
the author, and a summary in an expander that starts collapsed.
"""

from __future__ import annotations

import json
from datetime import date

import streamlit as st

import db
import metadata

PLACEHOLDER_COVER = "https://placehold.co/128x193?text=No+cover"


# --------------------------------------------------------------------------
# setup
# --------------------------------------------------------------------------

st.set_page_config(page_title="To B--", page_icon="📚", layout="centered")


@st.cache_resource
def get_conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


conn = get_conn()

if "candidates" not in st.session_state:
    st.session_state.candidates = []
if "chosen" not in st.session_state:
    st.session_state.chosen = None
if "editing" not in st.session_state:
    st.session_state.editing = None


def stars(rating: int | None) -> str:
    if not rating:
        return "not rated"
    return "★" * rating + "☆" * (5 - rating)


def reset_add_flow() -> None:
    st.session_state.candidates = []
    st.session_state.chosen = None


# --------------------------------------------------------------------------
# sidebar: add a book
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Add a book")

    query = st.text_input("Title", placeholder="e.g. Piranesi", key="query")
    if st.button("Search", use_container_width=True) and query.strip():
        with st.spinner("Searching…"):
            st.session_state.candidates = metadata.search(query, limit=6)
            st.session_state.chosen = None
        if not st.session_state.candidates:
            st.warning("No matches. Try adding the author's name.")

    # Step 2: pick the right edition out of the results.
    if st.session_state.candidates and st.session_state.chosen is None:
        st.caption("Which one?")
        for i, cand in enumerate(st.session_state.candidates):
            cols = st.columns([1, 3])
            cols[0].image(cand.cover_url or PLACEHOLDER_COVER, width=50)
            with cols[1]:
                st.write(f"**{cand.title}**")
                st.caption(f"{cand.author or 'Unknown author'} · {cand.year}")
                if st.button("Choose", key=f"pick_{i}", use_container_width=True):
                    st.session_state.chosen = metadata.enrich(cand)
                    st.rerun()
        if st.button("Cancel", use_container_width=True):
            reset_add_flow()
            st.rerun()

    # Step 3: TBR or read?
    chosen = st.session_state.chosen
    if chosen is not None:
        existing = db.find_existing(conn, chosen.source_id, chosen.title, chosen.author)
        if existing:
            st.warning(
                f"Already on your shelves as “{existing['title']}” "
                f"({db.STATUS_LABELS[existing['status']]})."
            )

        st.success(chosen.label)
        status = st.radio(
            "Have you read it?",
            options=["tbr", "reading", "read"],
            format_func=lambda s: db.STATUS_LABELS[s],
            horizontal=False,
        )

        rating = None
        finished = None
        if status == "read":
            rating = st.slider("Rating out of 5", 1, 5, 4)
            finished = st.date_input("Date finished", value=date.today())

        if st.button("Add to shelves", type="primary", use_container_width=True):
            db.add_book(
                conn,
                title=chosen.title,
                author=chosen.author,
                cover_url=chosen.cover_url,
                summary=chosen.summary,
                source_id=chosen.source_id,
                status=status,
                rating=rating,
                date_finished=finished,
            )
            reset_add_flow()
            st.rerun()

        if st.button("Back", use_container_width=True):
            st.session_state.chosen = None
            st.rerun()

    st.divider()
    with st.expander("Add manually"):
        m_title = st.text_input("Title", key="m_title")
        m_author = st.text_input("Author", key="m_author")
        m_status = st.selectbox(
            "Status", db.STATUSES, format_func=lambda s: db.STATUS_LABELS[s]
        )
        if st.button("Add", key="manual_add") and m_title.strip():
            db.add_book(conn, title=m_title, author=m_author, status=m_status)
            st.rerun()

    st.divider()
    st.download_button(
        "Export CSV backup",
        data=db.export_csv(conn),
        file_name=f"to-b-backup-{date.today().isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.download_button(
        "Export JSON backup",
        data=json.dumps(db.export_rows(conn), indent=2),
        file_name=f"to-b-backup-{date.today().isoformat()}.json",
        mime="application/json",
        use_container_width=True,
    )


# --------------------------------------------------------------------------
# main: the shelves
# --------------------------------------------------------------------------

st.title("To B--")

counts = db.status_counts(conn)
st.caption(
    f"{counts['tbr']} to read · {counts['reading']} reading · {counts['read']} read"
)

filters = st.columns([2, 2, 3])
status_filter = filters[0].selectbox(
    "Shelf",
    options=[None, *db.STATUSES],
    format_func=lambda s: "All" if s is None else db.STATUS_LABELS[s],
)
sort = filters[1].selectbox(
    "Sort by",
    options=["date_added", "date_finished", "rating", "title", "author"],
    format_func=lambda s: {
        "date_added": "Recently added",
        "date_finished": "Recently finished",
        "rating": "Rating",
        "title": "Title",
        "author": "Author",
    }[s],
)
search_text = filters[2].text_input("Filter", placeholder="title or author")

books = db.list_books(conn, status=status_filter, sort=sort, search=search_text)

if not books:
    st.info("Nothing here yet. Add a book from the sidebar.")

for book in books:
    with st.container(border=True):
        left, right = st.columns([1, 4])

        left.image(book["cover_url"] or PLACEHOLDER_COVER, use_container_width=True)

        with right:
            st.subheader(book["title"])
            st.write(f"{stars(book['rating'])}  ·  {book['author'] or 'Unknown author'}")

            line = db.STATUS_LABELS[book["status"]]
            if book["date_finished"]:
                line += f" · finished {db.to_display(book['date_finished'])}"
            st.caption(line)

            # Collapsed by default, as in the sketch.
            if book["summary"]:
                with st.expander("Summary"):
                    st.write(book["summary"])

            actions = st.columns(3)

            if book["status"] in ("tbr", "reading"):
                if actions[0].button("Mark as read", key=f"read_{book['id']}"):
                    st.session_state.editing = f"finish_{book['id']}"
                    st.rerun()
            if book["status"] == "tbr":
                if actions[1].button("Start reading", key=f"start_{book['id']}"):
                    db.set_status(conn, book["id"], "reading")
                    st.rerun()
            if actions[2].button("Edit", key=f"edit_{book['id']}"):
                st.session_state.editing = f"edit_{book['id']}"
                st.rerun()

            # The TBR -> Read move collects the same data as the Read path.
            if st.session_state.editing == f"finish_{book['id']}":
                with st.form(f"finish_form_{book['id']}"):
                    st.write("**Finishing this one**")
                    rating = st.slider("Rating out of 5", 1, 5, 4)
                    finished = st.date_input("Date finished", value=date.today())
                    if st.form_submit_button("Save"):
                        db.mark_read(conn, book["id"], rating, finished)
                        st.session_state.editing = None
                        st.rerun()

            if st.session_state.editing == f"edit_{book['id']}":
                with st.form(f"edit_form_{book['id']}"):
                    new_title = st.text_input("Title", value=book["title"])
                    new_author = st.text_input("Author", value=book["author"] or "")
                    new_status = st.selectbox(
                        "Status",
                        db.STATUSES,
                        index=db.STATUSES.index(book["status"]),
                        format_func=lambda s: db.STATUS_LABELS[s],
                    )
                    new_rating = st.slider("Rating", 0, 5, book["rating"] or 0)
                    new_finished = st.text_input(
                        "Date finished (mm/dd/yyyy)",
                        value=db.to_display(book["date_finished"]),
                    )
                    new_cover = st.text_input("Cover URL", value=book["cover_url"] or "")
                    new_summary = st.text_area("Summary", value=book["summary"] or "")

                    saved, removed = st.columns(2)
                    if saved.form_submit_button("Save changes"):
                        db.update_book(
                            conn,
                            book["id"],
                            title=new_title,
                            author=new_author,
                            status=new_status,
                            rating=new_rating or None,
                            date_finished=new_finished or None,
                            cover_url=new_cover or None,
                            summary=new_summary or None,
                        )
                        st.session_state.editing = None
                        st.rerun()
                    if removed.form_submit_button("Delete book"):
                        db.delete_book(conn, book["id"])
                        st.session_state.editing = None
                        st.rerun()
