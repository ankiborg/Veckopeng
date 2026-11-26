"""SQLite-lagret. En enda fil på disk – lätt att säkerhetskopiera."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from clock import now_local

DEFAULT_PATH = Path(__file__).resolve().parent / "veckopeng.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS child (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    weekly_ore  INTEGER NOT NULL DEFAULT 2500,
    start_date  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS txn (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_id    INTEGER NOT NULL REFERENCES child(id) ON DELETE CASCADE,
    date        TEXT    NOT NULL,
    amount_ore  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_txn_child ON txn(child_id, date);

-- Gör lördagspåfyllningen idempotent: kör vi accrue() tio gånger samma dag
-- blir det ändå bara en veckopeng per lördag och barn.
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_veckopeng
    ON txn(child_id, date) WHERE kind = 'veckopeng';

-- Småsaker som ska överleva omstarter, t.ex. senast hämtade CAD-kurs.
CREATE TABLE IF NOT EXISTS setting (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""


def db_path() -> Path:
    """Var databasen ligger.

    1. VECKOPENG_DB om den är satt (tester, eget val).
    2. Railways volym om en sådan är monterad – Railway sätter då
       RAILWAY_VOLUME_MOUNT_PATH själv. Då räcker det att lägga till
       volymen; ingen variabel att stava rätt.
    3. Annars bredvid koden, som hemma.
    """
    if explicit := os.environ.get("VECKOPENG_DB", "").strip():
        return Path(explicit)
    if volym := os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip():
        return Path(volym) / "veckopeng.db"
    return DEFAULT_PATH


def pa_railway() -> bool:
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT_NAME")
        or os.environ.get("RAILWAY_PROJECT_ID")
    )


def sparas_sakert() -> bool:
    """Överlever databasen nästa deploy?

    Railways filsystem nollställs vid varje deploy. Bara det som ligger på
    en volym finns kvar – annars försvinner barnen och alla saldon nästa
    gång någon pushar. Hemma är disken beständig och svaret alltid ja.
    """
    if not pa_railway():
        return True
    volym = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if not volym:
        return False
    return db_path().resolve().is_relative_to(Path(volym).resolve())


TABELLER_SOM_MASTE_FINNAS = {"child", "txn"}


def ar_veckopengsfil(path: str | Path) -> bool:
    """Är filen en riktig Veckopeng-databas? Kollas innan den får ersätta
    den levande – en felvald fil ska inte kunna radera barnen."""
    try:
        c = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        try:
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            namn = {
                r[0]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            return TABELLER_SOM_MASTE_FINNAS <= namn
        finally:
            c.close()
    except sqlite3.DatabaseError:
        return False


def kopiera(fran: sqlite3.Connection, till: sqlite3.Connection) -> None:
    """Kopiera hela databasen med SQLites backup-API. Tar med det som
    ligger i WAL-filen, och fungerar medan den andra gunicorn-arbetaren
    läser – en vanlig filkopia gör inget av det."""
    fran.backup(till)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    fil = Path(path or db_path())
    fil.parent.mkdir(parents=True, exist_ok=True)  # Railways volym kan vara tom
    conn = sqlite3.connect(str(fil))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL låter en läsare och en skrivare jobba samtidigt. Utan den låser
    # gunicorns arbetare ut varandra och sidan svarar "database is locked"
    # när två telefoner laddar samma sekund.
    conn.execute("PRAGMA journal_mode = WAL")
    # Vänta hellre en stund än att kasta fel direkt vid en krock.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --- barn ---------------------------------------------------------------


def list_children(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.*, COALESCE(SUM(t.amount_ore), 0) AS balance_ore
          FROM child c
          LEFT JOIN txn t ON t.child_id = c.id
         GROUP BY c.id
         ORDER BY c.id
        """
    ).fetchall()


def get_child(conn: sqlite3.Connection, child_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT c.*, COALESCE(SUM(t.amount_ore), 0) AS balance_ore
          FROM child c
          LEFT JOIN txn t ON t.child_id = c.id
         WHERE c.id = ?
         GROUP BY c.id
        """,
        (child_id,),
    ).fetchone()


def add_child(
    conn: sqlite3.Connection, name: str, weekly_ore: int, start_date: str
) -> int:
    cur = conn.execute(
        "INSERT INTO child (name, weekly_ore, start_date, created_at)"
        " VALUES (?, ?, ?, ?)",
        (name, weekly_ore, start_date, now_local().isoformat(timespec="seconds")),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_child(
    conn: sqlite3.Connection, child_id: int, name: str, weekly_ore: int
) -> None:
    conn.execute(
        "UPDATE child SET name = ?, weekly_ore = ? WHERE id = ?",
        (name, weekly_ore, child_id),
    )
    conn.commit()


def delete_child(conn: sqlite3.Connection, child_id: int) -> None:
    conn.execute("DELETE FROM txn WHERE child_id = ?", (child_id,))
    conn.execute("DELETE FROM child WHERE id = ?", (child_id,))
    conn.commit()


# --- transaktioner ------------------------------------------------------


def list_transactions(
    conn: sqlite3.Connection, child_id: int, limit: int | None = None
) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM txn WHERE child_id = ?"
        " ORDER BY date DESC, id DESC"
    )
    params: tuple = (child_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return conn.execute(sql, params).fetchall()


def add_transaction(
    conn: sqlite3.Connection,
    child_id: int,
    when: str,
    amount_ore: int,
    kind: str,
    note: str = "",
) -> int:
    cur = conn.execute(
        "INSERT INTO txn (child_id, date, amount_ore, kind, note, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            child_id,
            when,
            amount_ore,
            kind,
            note,
            now_local().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_transaction(conn: sqlite3.Connection, txn_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM txn WHERE id = ?", (txn_id,)).fetchone()


def delete_transaction(conn: sqlite3.Connection, txn_id: int) -> None:
    conn.execute("DELETE FROM txn WHERE id = ?", (txn_id,))
    conn.commit()


def total_owed(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount_ore), 0) AS s FROM txn").fetchone()
    return int(row["s"])
