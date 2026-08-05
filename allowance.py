"""Lördagspåfyllningen.

Ingen schemalagd jobb-körning. Appen räknar i stället EFTERSKOTT: varje
gång någon öppnar en sida bokförs alla lördagar som passerat sedan sist.
Det är hela poängen – en cron som inte kördes för att datorn var avstängd
i helgen tappar en veckopeng, och då stämmer inte saldot längre. Det här
ger samma svar oavsett hur länge appen legat nere.

Dubbelbokning stoppas av det partiella unique-indexet i db.SCHEMA, inte
av kontroller här.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from clock import SATURDAY, saturdays, today

KIND = "veckopeng"


def accrue_child(conn: sqlite3.Connection, child: sqlite3.Row, until: date | None = None) -> int:
    """Bokför saknade veckopengar för ett barn. Returnerar antalet nya rader."""
    until = until or today()
    start = date.fromisoformat(child["start_date"])
    weekly = int(child["weekly_ore"])
    if weekly <= 0:
        return 0

    redan = {
        row["date"]
        for row in conn.execute(
            "SELECT date FROM txn WHERE child_id = ? AND kind = ?",
            (child["id"], KIND),
        )
    }

    nya = [
        (child["id"], d.isoformat(), weekly, KIND, "Veckopeng")
        for d in saturdays(start, until)
        if d.isoformat() not in redan
    ]
    if not nya:
        return 0

    conn.executemany(
        "INSERT INTO txn (child_id, date, amount_ore, kind, note, created_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))",
        nya,
    )
    conn.commit()
    return len(nya)


def accrue_all(conn: sqlite3.Connection, until: date | None = None) -> int:
    """Kör påfyllningen för alla barn. Anropas vid varje sidvisning."""
    children = conn.execute("SELECT * FROM child").fetchall()
    return sum(accrue_child(conn, child, until) for child in children)


def next_saturday(from_day: date | None = None) -> date:
    """Nästa påfyllning – lördagen EFTER dagens datum.

    Är det redan lördag är veckans påfyllning bokförd, så nästa är om en
    vecka. Därför strikt efter, inte "från och med".
    """
    day = (from_day or today()) + timedelta(days=1)
    return day + timedelta(days=(SATURDAY - day.weekday()) % 7)
