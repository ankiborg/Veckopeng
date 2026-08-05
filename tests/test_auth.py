"""Inloggningen som skyddar appen när den ligger publikt."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth  # noqa: E402


@pytest.fixture()
def klient(tmp_path, monkeypatch):
    """Bygg appen på nytt per test – lösenordet läses när den startar."""

    def bygg(losenord: str | None):
        monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "test.db"))
        if losenord is None:
            monkeypatch.delenv("VECKOPENG_LOSENORD", raising=False)
        else:
            monkeypatch.setenv("VECKOPENG_LOSENORD", losenord)
        import app as app_modul

        importlib.reload(app_modul)
        app_modul.app.config["TESTING"] = True
        return app_modul.app.test_client()

    return bygg


def test_utan_losenord_ar_appen_oppen(klient):
    """Hemma ska python app.py fungera utan inloggning."""
    c = klient(None)
    assert c.get("/").status_code == 200


def test_med_losenord_kravs_inloggning(klient):
    c = klient("hemligt")
    r = c.get("/")
    assert r.status_code == 302
    assert "/logga-in" in r.headers["Location"]


def test_ratt_losenord_slapper_in(klient):
    c = klient("hemligt")
    r = c.post("/logga-in", data={"losenord": "hemligt"}, follow_redirects=True)
    assert r.status_code == 200
    assert c.get("/").status_code == 200


def test_fel_losenord_slapper_inte_in(klient):
    c = klient("hemligt")
    r = c.post("/logga-in", data={"losenord": "gissning"})
    assert r.status_code == 401
    assert "Fel lösenord" in r.data.decode()
    assert c.get("/").status_code == 302


def test_utloggad_kan_inte_andra_pengar(klient):
    """Skyddet måste gälla POST också, inte bara sidorna man tittar på."""
    c = klient("hemligt")
    r = c.post("/barn/ny", data={"name": "Inkräktare", "weekly": "25"})
    assert r.status_code == 302
    assert "/logga-in" in r.headers["Location"]

    c.post("/logga-in", data={"losenord": "hemligt"})
    assert c.get("/").data.count(b"Inkr\xc3\xa4ktare") == 0


def test_logga_ut(klient):
    c = klient("hemligt")
    c.post("/logga-in", data={"losenord": "hemligt"})
    c.post("/logga-ut")
    assert c.get("/").status_code == 302


def test_inloggningen_skickar_bara_vidare_internt(klient):
    """?nasta= får inte gå att peka mot en annan webbplats."""
    c = klient("hemligt")
    r = c.post(
        "/logga-in?nasta=https://example.com/kapad",
        data={"losenord": "hemligt"},
    )
    assert r.headers["Location"] in ("/", "http://localhost/")

    c2 = klient("hemligt")
    r2 = c2.post("/logga-in?nasta=//example.com", data={"losenord": "hemligt"})
    assert r2.headers["Location"] in ("/", "http://localhost/")


def test_nasta_foljer_med_till_ratt_sida(klient):
    c = klient("hemligt")
    r = c.post("/logga-in?nasta=/barn/1", data={"losenord": "hemligt"})
    assert r.headers["Location"] == "/barn/1"


def test_nyckeln_ar_stabil_over_omstarter(monkeypatch):
    """Slumpas nyckeln loggas alla ut vid varje deploy."""
    monkeypatch.setenv("VECKOPENG_LOSENORD", "hemligt")
    monkeypatch.delenv("VECKOPENG_SECRET", raising=False)
    assert auth.secret_key() == auth.secret_key()


def test_olika_losenord_ger_olika_nyckel(monkeypatch):
    monkeypatch.delenv("VECKOPENG_SECRET", raising=False)
    monkeypatch.setenv("VECKOPENG_LOSENORD", "ett")
    a = auth.secret_key()
    monkeypatch.setenv("VECKOPENG_LOSENORD", "två")
    assert auth.secret_key() != a


def test_explicit_secret_vinner(monkeypatch):
    monkeypatch.setenv("VECKOPENG_LOSENORD", "hemligt")
    monkeypatch.setenv("VECKOPENG_SECRET", "min-egen-nyckel")
    assert auth.secret_key() == "min-egen-nyckel"
