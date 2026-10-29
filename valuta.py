"""Visa pengarna i kanadensiska dollar, och vad barnen kan handla för.

Allt BOKFÖRS fortfarande i svenska öre. Det här är bara ett visningslager:
saldot räknas om till CAD när sidan ritas. Byter vi tillbaka till kronor
är allt exakt som förut, oavsett hur kursen rört sig.

I Kanada står priset på prislappen UTAN moms – 13 % HST läggs på i kassan
(Ontario). Har Leon 13 CAD räcker det alltså bara till en prislapp på
11,50. Momsläget visar den siffran.

Kursen hämtas från Frankfurter (ECB:s dagliga referenskurser, ingen
API-nyckel) och sparas i databasen. Går det inte att nå används senast
hämtade kurs, och finns ingen alls används RESERVKURS. Sätts
VECKOPENG_CAD_KURS används den i stället och inget hämtas.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from clock import now_local

KURS_URL = "https://api.frankfurter.app/latest?from=SEK&to=CAD"
RESERVKURS = Decimal("0.14")  # 1 kr ≈ 0,14 CAD hösten 2026
MOMS = Decimal("0.13")  # HST i Ontario
FORSOK_IGEN_EFTER = timedelta(hours=6)


class Kurs:
    """Hur många CAD en krona är värd, och var siffran kommer ifrån."""

    def __init__(self, cad_per_kr: Decimal, datum: str | None = None):
        self.cad_per_kr = cad_per_kr
        self.datum = datum  # ECB:s datum för kursen, None för reservkursen

    def till_cent(self, ore: int) -> int:
        """Öre → cent. Decimal hela vägen, inga flyttal."""
        return int((Decimal(int(ore)) * self.cad_per_kr).quantize(0, ROUND_HALF_UP))

    def till_ore(self, cent: int) -> int:
        return int((Decimal(int(cent)) / self.cad_per_kr).quantize(0, ROUND_HALF_UP))


def fore_moms(cent: int) -> int:
    """Det högsta prislappspris som, med moms, ryms i beloppet."""
    # Avrunda NEDÅT: hellre en cent kvar än en cent för lite i kassan.
    return int((Decimal(int(cent)) / (1 + MOMS)).to_integral_value(rounding=ROUND_FLOOR))


def format_cent(cent: int) -> str:
    """Formatera cent som "13,50 CAD" – samma stil som kronorna."""
    tecken = "-" if cent < 0 else ""
    dollar, rest = divmod(abs(int(cent)), 100)
    heltal = f"{dollar:,}".replace(",", "\xa0")
    decimaler = f",{rest:02d}" if rest else ""
    return f"{tecken}{heltal}{decimaler} CAD"


def moms_procent() -> str:
    return f"{int(MOMS * 100)}\xa0%"


# --- kursen --------------------------------------------------------------


def _hamta() -> tuple[Decimal, str]:
    """Fråga Frankfurter. Kastar vid nätverksfel eller konstigt svar."""
    with urllib.request.urlopen(KURS_URL, timeout=3) as svar:
        data = json.load(svar)
    return Decimal(str(data["rates"]["CAD"])), str(data["date"])


def _las(conn: sqlite3.Connection, nyckel: str) -> str | None:
    rad = conn.execute("SELECT value FROM setting WHERE key = ?", (nyckel,)).fetchone()
    return rad["value"] if rad else None


def _skriv(conn: sqlite3.Connection, nyckel: str, varde: str) -> None:
    conn.execute(
        "INSERT INTO setting (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (nyckel, varde),
    )


def kurs(conn: sqlite3.Connection) -> Kurs:
    """Dagens kurs. Hämtar högst var sjätte timme, även när det misslyckas –
    annars får varje sidvisning vänta på en server som inte svarar."""
    if fast := os.environ.get("VECKOPENG_CAD_KURS", "").strip():
        try:
            return Kurs(Decimal(fast.replace(",", ".")), None)
        except InvalidOperation:
            pass

    forsok = _las(conn, "cad_forsok")
    if forsok is None or now_local() - datetime.fromisoformat(forsok) > FORSOK_IGEN_EFTER:
        _skriv(conn, "cad_forsok", now_local().isoformat(timespec="seconds"))
        try:
            varde, datum = _hamta()
            if varde > 0:
                _skriv(conn, "cad_kurs", str(varde))
                _skriv(conn, "cad_datum", datum)
        except Exception:  # nätverket, JSON, saknad nyckel – allt ger samma svar
            pass
        conn.commit()

    sparad = _las(conn, "cad_kurs")
    if sparad:
        return Kurs(Decimal(sparad), _las(conn, "cad_datum"))
    return Kurs(RESERVKURS, None)
