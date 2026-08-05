"""All tid är svensk tid.

Servern kan stå i UTC. Kör vi på UTC blir lördagens påfyllning liggande
fel kring midnatt, och "idag" i formulären pekar på fel dygn två timmar
om året. Datum i appen är alltid Europe/Stockholm.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")

SATURDAY = 5  # date.weekday(): måndag = 0


def now_local() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now_local().date()


def parse_date(text: str) -> date:
    """Läs ett ISO-datum (YYYY-MM-DD) från ett formulär."""
    return date.fromisoformat(text.strip())


def saturdays(start: date, end: date):
    """Alla lördagar i intervallet, båda ändar inklusive."""
    if end < start:
        return
    # Hoppa fram till första lördagen från och med startdatumet.
    current = start + timedelta(days=(SATURDAY - start.weekday()) % 7)
    while current <= end:
        yield current
        current += timedelta(days=7)


def weekday_name(d: date) -> str:
    return ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"][
        d.weekday()
    ]


MONTHS = [
    "jan", "feb", "mars", "april", "maj", "juni",
    "juli", "aug", "sep", "okt", "nov", "dec",
]


def format_date(d: date | str) -> str:
    """"2026-08-05" → "5 aug". Månadsnamnen kommer härifrån, inte från
    systemets locale – en server i USA skulle annars skriva "Aug"."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    stamp = f"{d.day} {MONTHS[d.month - 1]}"
    return stamp if d.year == today().year else f"{stamp} {d.year}"
