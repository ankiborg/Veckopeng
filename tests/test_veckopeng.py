from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import allowance  # noqa: E402
import db  # noqa: E402
from clock import format_date, saturdays  # noqa: E402
from money import AmountError, format_ore, ore_to_input, parse_amount  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "test.db"))
    c = db.connect()
    db.init(c)
    yield c
    c.close()


# --- pengar --------------------------------------------------------------


@pytest.mark.parametrize(
    "text,ore",
    [
        ("25", 2500),
        ("25,50", 2550),
        ("25.50", 2550),
        (" 25 kr ", 2500),
        ("+10", 1000),
        ("-10", -1000),
        ("0,50", 50),
        ("1 000", 100000),
    ],
)
def test_parse_amount(text, ore):
    assert parse_amount(text) == ore


@pytest.mark.parametrize("text", ["", "   ", "abc", "25,505", "12kr34"])
def test_parse_amount_avvisar_skrap(text):
    with pytest.raises(AmountError):
        parse_amount(text)


def test_noll_ar_inte_ett_belopp():
    with pytest.raises(AmountError):
        parse_amount("0")


@pytest.mark.parametrize(
    "ore,text",
    [(2500, "25 kr"), (2550, "25,50 kr"), (-2000, "-20 kr"), (0, "0 kr")],
)
def test_format_ore(ore, text):
    assert format_ore(ore) == text


def test_tusental_far_hart_mellanslag():
    assert format_ore(150000) == "1\xa0500 kr"


def test_falt_och_parse_gar_runt():
    for ore in (2500, 2550, 5, 100000):
        assert parse_amount(ore_to_input(ore)) == ore


# --- lördagar ------------------------------------------------------------


def test_saturdays_inkluderar_bada_andarna():

    # 2026-08-01 är en lördag, 2026-08-22 likaså.
    funna = list(saturdays(date(2026, 8, 1), date(2026, 8, 22)))
    assert funna == [
        date(2026, 8, 1),
        date(2026, 8, 8),
        date(2026, 8, 15),
        date(2026, 8, 22),
    ]


def test_saturdays_tomt_nar_slutet_ligger_fore_starten():

    assert list(saturdays(date(2026, 8, 10), date(2026, 8, 1))) == []


def test_nasta_lordag_ar_strikt_efter_idag():
    # Är det redan lördag är veckans peng bokförd – nästa är om sju dagar.
    assert allowance.next_saturday(date(2026, 8, 1)) == date(2026, 8, 8)
    assert allowance.next_saturday(date(2026, 8, 3)) == date(2026, 8, 8)


# --- påfyllning ----------------------------------------------------------


def test_accrue_bokfor_en_peng_per_lordag(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    barn = db.get_child(conn, cid)

    antal = allowance.accrue_child(conn, barn, until=date(2026, 8, 22))

    assert antal == 4
    assert db.get_child(conn, cid)["balance_ore"] == 4 * 2500


def test_accrue_ar_idempotent(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 22))

    # Tio sidvisningar till får inte ge tio veckopengar till.
    for _ in range(10):
        allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 22))

    assert db.get_child(conn, cid)["balance_ore"] == 4 * 2500


def test_accrue_tar_igen_efter_avbrott(conn):
    """Datorn avstängd i en månad ska inte tappa några veckopengar."""
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 1))
    assert db.get_child(conn, cid)["balance_ore"] == 2500

    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 29))
    assert db.get_child(conn, cid)["balance_ore"] == 5 * 2500


def test_accrue_bokfor_inget_fore_startdatumet(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-10")
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 20))

    datum = [r["date"] for r in db.list_transactions(conn, cid)]
    assert datum == ["2026-08-15"]


def test_andrad_veckopeng_galler_bara_framat(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 8))

    db.update_child(conn, cid, "Elsa", 5000)
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 22))

    # 2 gamla lördagar à 25 kr + 2 nya à 50 kr
    assert db.get_child(conn, cid)["balance_ore"] == 2 * 2500 + 2 * 5000


def test_noll_i_veckopeng_bokfor_ingenting(conn):
    cid = db.add_child(conn, "Elsa", 0, "2026-08-01")
    assert allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 22)) == 0


def test_accrue_all_tar_alla_barn(conn):
    db.add_child(conn, "Elsa", 2500, "2026-08-01")
    db.add_child(conn, "Ivar", 1000, "2026-08-01")

    assert allowance.accrue_all(conn, until=date(2026, 8, 8)) == 4
    assert db.total_owed(conn) == 2 * 2500 + 2 * 1000


# --- transaktioner -------------------------------------------------------


def test_uttag_minskar_saldot(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 8))

    db.add_transaction(conn, cid, "2026-08-09", -2000, "uttag", "Glass")

    assert db.get_child(conn, cid)["balance_ore"] == 5000 - 2000


def test_saldot_kan_bli_minus(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    db.add_transaction(conn, cid, "2026-08-02", -10000, "uttag", "Förskott")
    assert db.get_child(conn, cid)["balance_ore"] == -10000


def test_borttaget_barn_tar_med_sig_historiken(conn):
    cid = db.add_child(conn, "Elsa", 2500, "2026-08-01")
    allowance.accrue_child(conn, db.get_child(conn, cid), until=date(2026, 8, 22))

    db.delete_child(conn, cid)

    assert db.list_children(conn) == []
    assert db.total_owed(conn) == 0


# --- datum ---------------------------------------------------------------


def test_format_date_ar_svenskt():
    assert format_date("2026-08-05") == "5 aug"
    assert format_date("2025-03-01") == "1 mars 2025"
