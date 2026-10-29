"""Kanadensiska dollar och moms – bara visning, bokföringen är i öre."""

from __future__ import annotations

import importlib
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import valuta  # noqa: E402
from clock import now_local  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("VECKOPENG_CAD_KURS", raising=False)
    c = db.connect()
    db.init(c)
    yield c
    c.close()


@pytest.fixture()
def klient(tmp_path, monkeypatch):
    monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("VECKOPENG_LOSENORD", raising=False)
    monkeypatch.setenv("VECKOPENG_CAD_KURS", "0.14")  # aldrig nätverk i tester
    import app as app_modul

    importlib.reload(app_modul)
    app_modul.app.config["TESTING"] = True
    c = app_modul.app.test_client()
    c.post("/barn/ny", data={"name": "Leon", "weekly": "100", "start_date": "2099-01-01"})
    return c


# --- räkning -------------------------------------------------------------


def test_kronor_blir_dollar_utan_flyttal():
    k = valuta.Kurs(Decimal("0.14"))
    assert k.till_cent(10000) == 1400  # 100 kr → 14 CAD
    assert k.till_ore(1400) == 10000


def test_fore_moms_avrundar_nedat():
    # 13 CAD räcker till en prislapp på 11,50 – 11,51 + 13 % blir 13,0063.
    assert valuta.fore_moms(1300) == 1150
    assert valuta.fore_moms(1130) == 1000


@pytest.mark.parametrize(
    "cent,text", [(1400, "14 CAD"), (1350, "13,50 CAD"), (-500, "-5 CAD"), (150000, "1\xa0500 CAD")]
)
def test_format_cent(cent, text):
    assert valuta.format_cent(cent) == text


# --- kursen --------------------------------------------------------------


def test_kursen_hamtas_och_sparas(conn, monkeypatch):
    monkeypatch.setattr(valuta, "_hamta", lambda: (Decimal("0.1432"), "2026-10-28"))
    k = valuta.kurs(conn)
    assert k.cad_per_kr == Decimal("0.1432")
    assert k.datum == "2026-10-28"

    # Nästa gång inom sex timmar frågar den inte igen.
    monkeypatch.setattr(valuta, "_hamta", lambda: pytest.fail("hämtade igen"))
    assert valuta.kurs(conn).cad_per_kr == Decimal("0.1432")


def test_nere_server_ger_senaste_kursen(conn, monkeypatch):
    monkeypatch.setattr(valuta, "_hamta", lambda: (Decimal("0.15"), "2026-10-01"))
    valuta.kurs(conn)
    gammalt = (now_local() - timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute("UPDATE setting SET value = ? WHERE key = 'cad_forsok'", (gammalt,))

    def nere():
        raise OSError("ingen kontakt")

    monkeypatch.setattr(valuta, "_hamta", nere)
    assert valuta.kurs(conn).cad_per_kr == Decimal("0.15")


def test_reservkurs_nar_inget_finns(conn, monkeypatch):
    def nere():
        raise OSError("ingen kontakt")

    monkeypatch.setattr(valuta, "_hamta", nere)
    k = valuta.kurs(conn)
    assert k.cad_per_kr == valuta.RESERVKURS
    assert k.datum is None


def test_fast_kurs_fran_miljon(conn, monkeypatch):
    monkeypatch.setenv("VECKOPENG_CAD_KURS", "0,2")
    monkeypatch.setattr(valuta, "_hamta", lambda: pytest.fail("ska inte hämta"))
    assert valuta.kurs(conn).cad_per_kr == Decimal("0.2")


# --- sidorna -------------------------------------------------------------


def test_kronor_ar_standard(klient):
    html = klient.get("/barn/1").get_data(as_text=True)
    assert "100 kr varje lördag" in html
    assert "moms" not in html.lower()  # momsknappen syns bara i CAD


def test_cad_visar_dollar(klient):
    klient.post("/barn/1/transaktion", data={"amount": "100", "direction": "in"})
    klient.post("/visning", data={"valuta": "CAD"})
    html = klient.get("/barn/1").get_data(as_text=True)
    assert "14 CAD" in html
    assert "1 kr = 0,1400 CAD" in html


def test_momslaget_visar_prislappspris(klient):
    klient.post("/barn/1/transaktion", data={"amount": "100", "direction": "in"})
    klient.post("/visning", data={"valuta": "CAD", "moms": "1"})
    html = klient.get("/barn/1").get_data(as_text=True)
    assert "Leon kan handla för" in html
    assert "12,38 CAD" in html  # 14 / 1,13 = 12,389… → nedåt
    assert "Saldot är 14 CAD" in html
    startsida = klient.get("/").get_data(as_text=True)
    assert "12,38 CAD" in startsida


def test_moms_galler_inte_i_kronor(klient):
    klient.post("/barn/1/transaktion", data={"amount": "100", "direction": "in"})
    klient.post("/visning", data={"valuta": "CAD", "moms": "1"})
    klient.post("/visning", data={"valuta": "SEK"})
    html = klient.get("/barn/1").get_data(as_text=True)
    assert "Leon har tjänat in" in html


def test_belopp_skrivs_i_cad_i_cad_laget(klient):
    klient.post("/barn/1/transaktion", data={"amount": "100", "direction": "in"})
    klient.post("/visning", data={"valuta": "CAD"})
    klient.post(
        "/barn/1/transaktion", data={"amount": "7", "direction": "ut", "note": "Glass"}
    )
    klient.post("/visning", data={"valuta": "SEK"})
    html = klient.get("/barn/1").get_data(as_text=True)
    assert "Glass (7 CAD)" in html
    assert "-50 kr" in html  # 7 CAD / 0,14 = 50 kr


def test_visning_skickar_bara_internt(klient):
    r = klient.post("/visning", data={"valuta": "CAD", "tillbaka": "//ond.example"})
    assert r.headers["Location"].endswith("/")
    r = klient.post("/visning", data={"valuta": "CAD", "tillbaka": "/barn/1"})
    assert r.headers["Location"].endswith("/barn/1")
