"""Sparkontot: överföringar, matchning och uttagsgräns.

Varje barn kan ha ett sparkonto vid sidan av fickpengarna. Poängen är att
göra det lönsamt att inte spendera:

* **Matchning.** Vi lägger på en andel av varje överföring. 100 % betyder
  att vi dubblar.
* **Tak per månad.** Utan tak går matchningen att mjölka – för pengar man
  tagit ut kan föras in igen och matchas en gång till. Taket sätter en
  övre gräns för vad det kan kosta oss, oavsett hur många gånger samma
  hundralapp åker fram och tillbaka.
* **Uttagsgräns.** Ett visst antal uttag per kalenderår. Det är gränsen
  som gör sparandet till ett verkligt beslut och inte en mellanlandning.

Pengar på sparkontot är fortfarande barnets pengar och räknas med i det
vi är skyldiga.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import db
from clock import today

# Transaktionstyper
OVERFORING = "overforing"  # flytt mellan barnets två konton
BONUS = "sparbonus"  # vår matchning
UTTAG = "sparuttag"  # uttag från sparkontot, räknas mot årsgränsen


class SavingsError(Exception):
    """Överföringen eller uttaget gick inte att genomföra."""


# --- matchning -----------------------------------------------------------


def matched_this_month(
    conn: sqlite3.Connection, child_id: int, when: date | None = None
) -> int:
    """Hur mycket bonus barnet redan fått den månad `when` ligger i."""
    when = when or today()
    rad = conn.execute(
        "SELECT COALESCE(SUM(amount_ore), 0) AS s FROM txn"
        " WHERE child_id = ? AND kind = ? AND date LIKE ?",
        (child_id, BONUS, f"{when.year:04d}-{when.month:02d}-%"),
    ).fetchone()
    return int(rad["s"])


def match_for(
    conn: sqlite3.Connection,
    child: sqlite3.Row,
    amount_ore: int,
    when: date | None = None,
) -> int:
    """Vad vi lägger på en överföring, efter att taket dragits av."""
    procent = int(child["match_percent"] or 0)
    if procent <= 0:
        return 0

    bonus = amount_ore * procent // 100

    tak = int(child["match_cap_ore"] or 0)
    if tak > 0:
        kvar = tak - matched_this_month(conn, child["id"], when)
        bonus = max(0, min(bonus, kvar))
    return bonus


# --- uttagsgräns ---------------------------------------------------------


def withdrawals_this_year(
    conn: sqlite3.Connection, child_id: int, when: date | None = None
) -> int:
    when = when or today()
    rad = conn.execute(
        "SELECT COUNT(*) AS n FROM txn"
        " WHERE child_id = ? AND kind = ? AND date LIKE ?",
        (child_id, UTTAG, f"{when.year:04d}-%"),
    ).fetchone()
    return int(rad["n"])


def withdrawals_left(
    conn: sqlite3.Connection, child: sqlite3.Row, when: date | None = None
) -> int:
    """Antal uttag kvar i år. Noll betyder att gränsen är nådd."""
    tillatna = int(child["withdrawals_per_year"] or 0)
    if tillatna <= 0:  # 0 = ingen gräns
        return -1
    return max(0, tillatna - withdrawals_this_year(conn, child["id"], when))


# --- överföringar --------------------------------------------------------


def deposit(
    conn: sqlite3.Connection,
    child: sqlite3.Row,
    amount_ore: int,
    when: str | None = None,
) -> int:
    """Flytta pengar från fickpengarna till sparkontot. Returnerar bonusen.

    Alla tre raderna skrivs i en och samma transaktion. Ett avbrott mitt i
    får inte kunna lämna kvar ett uttag från fickpengarna utan motsvarande
    insättning på sparkontot.
    """
    if not child["savings_on"]:
        raise SavingsError("Sparkontot är inte påslaget för det här barnet.")
    if amount_ore <= 0:
        raise SavingsError("Beloppet måste vara mer än noll.")
    if amount_ore > int(child["balance_ore"]):
        raise SavingsError(
            "Det finns inte så mycket i fickpengarna. "
            "Sparkontot fylls på från fickpengarna, inte från luften."
        )

    datum = when or today().isoformat()
    bonus = match_for(conn, child, amount_ore, date.fromisoformat(datum))

    try:
        rader = [
            db.add_transaction(
                conn, child["id"], datum, -amount_ore, OVERFORING,
                "Till sparkontot", db.FICKPENGAR, commit=False,
            ),
            db.add_transaction(
                conn, child["id"], datum, amount_ore, OVERFORING,
                "Från fickpengarna", db.SPARKONTO, commit=False,
            ),
        ]
        if bonus:
            rader.append(
                db.add_transaction(
                    conn, child["id"], datum, bonus, BONUS,
                    _bonustext(child), db.SPARKONTO, commit=False,
                )
            )
        db.set_group(conn, rader)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return bonus


def _bonustext(child: sqlite3.Row) -> str:
    procent = int(child["match_percent"] or 0)
    if procent == 100:
        return "Vi dubblar!"
    return f"Vi lägger på {procent} %"


def withdraw(
    conn: sqlite3.Connection,
    child: sqlite3.Row,
    amount_ore: int,
    when: str | None = None,
    override: bool = False,
) -> None:
    """Flytta tillbaka pengar från sparkontot till fickpengarna."""
    if not child["savings_on"]:
        raise SavingsError("Sparkontot är inte påslaget för det här barnet.")
    if amount_ore <= 0:
        raise SavingsError("Beloppet måste vara mer än noll.")
    if amount_ore > int(child["savings_ore"]):
        raise SavingsError("Det finns inte så mycket på sparkontot.")

    datum = when or today().isoformat()
    if not override and withdrawals_left(conn, child, date.fromisoformat(datum)) == 0:
        tillatna = int(child["withdrawals_per_year"])
        raise SavingsError(
            f"{child['name']} har redan gjort {tillatna} uttag i år. "
            "Du kan göra ett uttag ändå, men det syns i historiken."
        )

    text = "Uttag från sparkontot"
    if override:
        # Ett undantag som inte syns är ingen gräns. Det ska gå att se
        # i efterhand att vi gick förbi den vi själva satt upp.
        text += " (utöver årsgränsen)"

    try:
        db.set_group(conn, [
            db.add_transaction(
                conn, child["id"], datum, -amount_ore, UTTAG, text,
                db.SPARKONTO, commit=False,
            ),
            db.add_transaction(
                conn, child["id"], datum, amount_ore, OVERFORING,
                "Från sparkontot", db.FICKPENGAR, commit=False,
            ),
        ])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
