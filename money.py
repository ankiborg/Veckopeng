"""Pengar räknas i ÖRE, aldrig i float.

25,50 kr blir 2550. Ett saldo som räknas i kronor med decimaler driver
i väg efter tillräckligt många insättningar, och det här är pengar vi
faktiskt är skyldiga barnen.
"""

from __future__ import annotations


class AmountError(ValueError):
    """Beloppet gick inte att tolka."""


def parse_amount(text: str) -> int:
    """Tolka ett belopp från formuläret och returnera öre.

    Accepterar både "25", "25,50" och "25.50" – Annika skriver komma,
    ett tangentbord med numeriskt block skriver punkt.
    """
    if text is None:
        raise AmountError("Skriv ett belopp.")

    cleaned = text.strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    cleaned = cleaned.removesuffix("kr").strip()
    if not cleaned:
        raise AmountError("Skriv ett belopp.")
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]

    try:
        kronor, _, decimaler = cleaned.partition(".")
        if len(decimaler) > 2:
            raise AmountError("Högst två decimaler.")
        negativ = kronor.startswith("-")
        kronor = kronor.lstrip("-") or "0"
        if not kronor.isdigit() or (decimaler and not decimaler.isdigit()):
            raise AmountError(f"'{text.strip()}' är inget belopp.")
        ore = int(kronor) * 100 + int(decimaler.ljust(2, "0") or 0)
    except AmountError:
        raise
    except ValueError as exc:  # pragma: no cover - defensivt
        raise AmountError(f"'{text.strip()}' är inget belopp.") from exc

    if ore == 0:
        raise AmountError("Beloppet måste vara mer än noll.")
    return -ore if negativ else ore


def ore_to_input(ore: int) -> str:
    """Öre → strängen som ska ligga i ett textfält: "25" eller "25,50"."""
    kronor, rest = divmod(abs(int(ore)), 100)
    tecken = "-" if ore < 0 else ""
    return f"{tecken}{kronor},{rest:02d}" if rest else f"{tecken}{kronor}"


def format_ore(ore: int) -> str:
    """Formatera öre som "25 kr" eller "25,50 kr" – jämna kronor slipper ",00"."""
    tecken = "-" if ore < 0 else ""
    kronor, rest = divmod(abs(int(ore)), 100)
    # Hårt mellanslag både som tusentalsavgränsare (svensk standard) och
    # framför "kr" – annars bryter webbläsaren raden mitt i beloppet och
    # lämnar ett ensamt "kr" på nästa rad.
    heltal = f"{kronor:,}".replace(",", "\xa0")
    decimaler = f",{rest:02d}" if rest else ""
    return f"{tecken}{heltal}{decimaler}\xa0kr"
