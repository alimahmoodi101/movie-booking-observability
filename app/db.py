"""SQLite database setup and models for the movie booking app."""
import sqlite3
import time
import os
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "/data/booking.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor(commit=False):
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db_cursor(commit=True) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS showtimes (
                id INTEGER PRIMARY KEY,
                movie_id INTEGER NOT NULL,
                starts_at TEXT NOT NULL,
                total_seats INTEGER NOT NULL,
                FOREIGN KEY (movie_id) REFERENCES movies(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seats (
                id INTEGER PRIMARY KEY,
                showtime_id INTEGER NOT NULL,
                seat_label TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'free',  -- free | held | booked
                held_at REAL,
                held_by TEXT,
                FOREIGN KEY (showtime_id) REFERENCES showtimes(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY,
                showtime_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                seat_ids TEXT NOT NULL,   -- comma-separated
                status TEXT NOT NULL,     -- confirmed | cancelled
                created_at REAL NOT NULL,
                FOREIGN KEY (showtime_id) REFERENCES showtimes(id)
            )
        """)

        # Seed a couple of movies/showtimes if empty
        cur.execute("SELECT COUNT(*) c FROM movies")
        if cur.fetchone()["c"] == 0:
            cur.execute("INSERT INTO movies (title) VALUES ('Dune: Part Three')")
            cur.execute("INSERT INTO movies (title) VALUES ('The Long Reel')")
            movie_ids = [row["id"] for row in cur.execute("SELECT id FROM movies")]
            now = time.time()
            for mid in movie_ids:
                cur.execute(
                    "INSERT INTO showtimes (movie_id, starts_at, total_seats) VALUES (?, ?, ?)",
                    (mid, "2026-09-22T19:00:00", 20),
                )
                showtime_id = cur.lastrowid
                for i in range(1, 21):
                    cur.execute(
                        "INSERT INTO seats (showtime_id, seat_label, status) VALUES (?, ?, 'free')",
                        (showtime_id, f"S{i:02d}"),
                    )
