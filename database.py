"""
Lightweight SQLite persistence layer.

NOTE ON RAILWAY: Railway's filesystem is ephemeral by default -- the bot.db
file will be WIPED on every redeploy/restart unless you attach a Railway
Volume and point DB_PATH (see .env.example) at a path inside that volume.
See the README for setup steps.
"""
import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "bot.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    # Make sure the parent directory exists (e.g. /data for a mounted
    # Railway Volume) -- sqlite3 fails with "unable to open database file"
    # if it doesn't. On some hosts the volume mount can also briefly lag
    # behind the container starting up, so retry a few times before giving up.
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)

    last_error = None
    for attempt in range(5):
        try:
            with get_conn() as conn:
                _create_tables(conn)
            return
        except sqlite3.OperationalError as e:
            last_error = e
            time.sleep(1)
    raise last_error


def _create_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            prize TEXT NOT NULL,
            winners INTEGER NOT NULL,
            host_id INTEGER NOT NULL,
            ends_at TEXT NOT NULL,
            ended INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (message_id, user_id)
        )
    """)



# ---------- Leveling ----------

def get_user_xp(guild_id: int, user_id: int) -> tuple[int, int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT xp, level FROM levels WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            return 0, 0
        return row["xp"], row["level"]


def set_user_xp(guild_id: int, user_id: int, xp: int, level: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO levels (guild_id, user_id, xp, level) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = excluded.xp, level = excluded.level
        """, (guild_id, user_id, xp, level))


def get_leaderboard(guild_id: int, limit: int = 10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT user_id, xp, level FROM levels WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
            (guild_id, limit),
        ).fetchall()


def get_rank(guild_id: int, user_id: int) -> int:
    """1-indexed position on the leaderboard, or 0 if the user has no XP row."""
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) + 1 AS rank FROM levels
            WHERE guild_id = ? AND xp > (
                SELECT xp FROM levels WHERE guild_id = ? AND user_id = ?
            )
        """, (guild_id, guild_id, user_id)).fetchone()
        exists = conn.execute(
            "SELECT 1 FROM levels WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        ).fetchone()
        return row["rank"] if exists else 0


def reset_server_xp(guild_id: int) -> int:
    """Wipes every member's XP/level in a guild. Returns the number of rows removed."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM levels WHERE guild_id = ?", (guild_id,))
        return cur.rowcount


# ---------- Warnings ----------

def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, reason),
        )
        return cur.lastrowid


def get_warnings(guild_id: int, user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, moderator_id, reason, created_at FROM warnings "
            "WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC",
            (guild_id, user_id),
        ).fetchall()


def clear_warnings(guild_id: int, user_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return cur.rowcount


def remove_warning(guild_id: int, warning_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND id = ?", (guild_id, warning_id)
        )
        return cur.rowcount


# ---------- Giveaways ----------

def create_giveaway(message_id: int, guild_id: int, channel_id: int, prize: str, winners: int, host_id: int, ends_at_iso: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO giveaways (message_id, guild_id, channel_id, prize, winners, host_id, ends_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, guild_id, channel_id, prize, winners, host_id, ends_at_iso),
        )


def get_giveaway(message_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM giveaways WHERE message_id = ?", (message_id,)).fetchone()


def get_active_giveaways():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM giveaways WHERE ended = 0").fetchall()


def mark_giveaway_ended(message_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE giveaways SET ended = 1 WHERE message_id = ?", (message_id,))


def add_giveaway_entry(message_id: int, user_id: int) -> bool:
    """Returns True if this was a new entry, False if the user already entered."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO giveaway_entries (message_id, user_id) VALUES (?, ?)",
                (message_id, user_id),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_giveaway_entries(message_id: int) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM giveaway_entries WHERE message_id = ?", (message_id,)
        ).fetchall()
        return [r["user_id"] for r in rows]
