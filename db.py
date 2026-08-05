"""SQLite-lagret. En enda fil på disk – lätt att säkerhetskopiera."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from clock import now_local

DEFAULT_PATH = Path(__file__).resolve().parent / "veckopeng.db"

# Varje barn har två konton. Fickpengarna är det som kommer och går;
# sparkontot är valfritt och sätts på per barn.
FICKPENGAR = "fickpengar"
SPARKONTO = "sparkonto"

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
"""

# Kolumner som tillkommit efter att appen börjat användas skarpt. De läggs
# på i efterhand med ALTER TABLE, aldrig genom att bygga om tabellen –
# databasen på Railway innehåller riktiga saldon som inte får gå förlorade.
MIGRATIONS: list[tuple[str, str, str]] = [
    # (tabell, kolumn, definition)
    ("txn", "account", f"TEXT NOT NULL DEFAULT '{FICKPENGAR}'"),
    # Binder ihop raderna i en överföring. Tas den ena bort måste den andra
    # följa med, annars uppstår pengar ur tomma intet på det ena kontot.
    ("txn", "group_id", "INTEGER"),
    ("child", "savings_on", "INTEGER NOT NULL DEFAULT 0"),
    ("child", "match_percent", "INTEGER NOT NULL DEFAULT 100"),
    ("child", "match_cap_ore", "INTEGER NOT NULL DEFAULT 10000"),
    ("child", "withdrawals_per_year", "INTEGER NOT NULL DEFAULT 2"),
]


def db_path() -> Path:
    """Var databasen ligger. VECKOPENG_DB kan peka om den (tester, backup)."""
    return Path(os.environ.get("VECKOPENG_DB") or DEFAULT_PATH)


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
    migrate(conn)
    conn.commit()


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Lägg till kolumner som saknas. Returnerar vad som gjordes."""
    gjort = []
    for tabell, kolumn, definition in MIGRATIONS:
        finns = {
            rad["name"] for rad in conn.execute(f"PRAGMA table_info({tabell})")
        }
        if kolumn not in finns:
            conn.execute(f"ALTER TABLE {tabell} ADD COLUMN {kolumn} {definition}")
            gjort.append(f"{tabell}.{kolumn}")
    if gjort:
        conn.commit()
    return gjort


# --- barn ---------------------------------------------------------------


# balance_ore = fickpengarna, savings_ore = sparkontot, total_ore = det vi
# faktiskt är skyldiga barnet. Sparade pengar är fortfarande barnets pengar.
_BARN_MED_SALDON = f"""
    SELECT c.*,
           COALESCE(SUM(CASE WHEN t.account = '{FICKPENGAR}'
                             THEN t.amount_ore END), 0) AS balance_ore,
           COALESCE(SUM(CASE WHEN t.account = '{SPARKONTO}'
                             THEN t.amount_ore END), 0) AS savings_ore,
           COALESCE(SUM(t.amount_ore), 0)               AS total_ore
      FROM child c
      LEFT JOIN txn t ON t.child_id = c.id
"""


def list_children(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        _BARN_MED_SALDON + " GROUP BY c.id ORDER BY c.id"
    ).fetchall()


def get_child(conn: sqlite3.Connection, child_id: int) -> sqlite3.Row | None:
    return conn.execute(
        _BARN_MED_SALDON + " WHERE c.id = ? GROUP BY c.id", (child_id,)
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


def update_savings_settings(
    conn: sqlite3.Connection,
    child_id: int,
    savings_on: bool,
    match_percent: int,
    match_cap_ore: int,
    withdrawals_per_year: int,
) -> None:
    conn.execute(
        "UPDATE child SET savings_on = ?, match_percent = ?, match_cap_ore = ?,"
        " withdrawals_per_year = ? WHERE id = ?",
        (
            1 if savings_on else 0,
            match_percent,
            match_cap_ore,
            withdrawals_per_year,
            child_id,
        ),
    )
    conn.commit()


def delete_child(conn: sqlite3.Connection, child_id: int) -> None:
    conn.execute("DELETE FROM txn WHERE child_id = ?", (child_id,))
    conn.execute("DELETE FROM child WHERE id = ?", (child_id,))
    conn.commit()


# --- transaktioner ------------------------------------------------------


def list_transactions(
    conn: sqlite3.Connection,
    child_id: int,
    account: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM txn WHERE child_id = ?"
    params: tuple = (child_id,)
    if account is not None:
        sql += " AND account = ?"
        params += (account,)
    sql += " ORDER BY date DESC, id DESC"
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
    account: str = FICKPENGAR,
    commit: bool = True,
) -> int:
    """commit=False när flera rader hör ihop – en överföring får aldrig
    lämna efter sig ett uttag utan motsvarande insättning."""
    cur = conn.execute(
        "INSERT INTO txn (child_id, date, amount_ore, kind, note, account,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            child_id,
            when,
            amount_ore,
            kind,
            note,
            account,
            now_local().isoformat(timespec="seconds"),
        ),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid)


def get_transaction(conn: sqlite3.Connection, txn_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM txn WHERE id = ?", (txn_id,)).fetchone()


def set_group(conn: sqlite3.Connection, txn_ids: list[int]) -> None:
    """Knyt ihop raderna i en överföring. Gruppens id är den första radens."""
    if not txn_ids:
        return
    grupp = txn_ids[0]
    conn.executemany(
        "UPDATE txn SET group_id = ? WHERE id = ?",
        [(grupp, t) for t in txn_ids],
    )


def delete_transaction(conn: sqlite3.Connection, txn_id: int) -> int:
    """Ta bort posten – och resten av dess överföring. Returnerar antalet."""
    rad = get_transaction(conn, txn_id)
    if rad is None:
        return 0
    if rad["group_id"]:
        cur = conn.execute("DELETE FROM txn WHERE group_id = ?", (rad["group_id"],))
    else:
        cur = conn.execute("DELETE FROM txn WHERE id = ?", (txn_id,))
    conn.commit()
    return int(cur.rowcount)


def total_owed(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount_ore), 0) AS s FROM txn").fetchone()
    return int(row["s"])
