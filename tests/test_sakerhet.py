"""Att barnen och saldona inte försvinner: volymen, larmet och kopian."""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402

RAILWAY = ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_VOLUME_MOUNT_PATH")


@pytest.fixture(autouse=True)
def ren_miljo(monkeypatch):
    for namn in RAILWAY + ("VECKOPENG_DB", "VECKOPENG_LOSENORD"):
        monkeypatch.delenv(namn, raising=False)


@pytest.fixture()
def klient(tmp_path, monkeypatch):
    monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "app" / "veckopeng.db"))
    import app as app_modul

    importlib.reload(app_modul)
    app_modul.app.config["TESTING"] = True
    c = app_modul.app.test_client()
    c.post("/barn/ny", data={"name": "Leon", "weekly": "25", "start_date": "2099-01-01"})
    c.post("/barn/1/transaktion", data={"amount": "135", "direction": "in"})
    return c


# --- var databasen hamnar ------------------------------------------------


def test_volymen_anvands_utan_variabel(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    assert db.db_path() == tmp_path / "veckopeng.db"
    assert db.sparas_sakert()


def test_utan_volym_pa_railway_ar_osakert(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    assert not db.sparas_sakert()


def test_fil_utanfor_volymen_ar_osakert(monkeypatch, tmp_path):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "app" / "veckopeng.db"))
    assert not db.sparas_sakert()


def test_hemma_ar_alltid_sakert():
    assert db.sparas_sakert()


def test_larmet_syns_utan_volym(klient, monkeypatch):
    assert "sparas inte" not in klient.get("/").get_data(as_text=True)
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    assert "sparas inte" in klient.get("/").get_data(as_text=True)


# --- säkerhetskopian -----------------------------------------------------


def test_kopian_gar_runt(klient):
    kopia = klient.get("/sakerhetskopia")
    assert kopia.status_code == 200
    assert "attachment" in kopia.headers["Content-Disposition"]
    data = kopia.get_data()

    # Allt försvinner …
    klient.post("/barn/1/ta-bort", data={"confirm": "Leon"})
    assert "Inga barn upplagda" in klient.get("/").get_data(as_text=True)

    # … och kommer tillbaka.
    r = klient.post(
        "/sakerhetskopia/aterstall",
        data={"fil": (io.BytesIO(data), "veckopeng.db")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "Säkerhetskopian är inläst" in html
    assert "Leon" in html
    assert "135 kr" in html


def test_fel_fil_andrar_ingenting(klient):
    r = klient.post(
        "/sakerhetskopia/aterstall",
        data={"fil": (io.BytesIO(b"inte en databas"), "semester.jpg")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    html = r.get_data(as_text=True)
    assert "ingen säkerhetskopia" in html
    assert "135 kr" in html


def test_det_gamla_sparas_undan_fore_aterstallning(klient):
    data = klient.get("/sakerhetskopia").get_data()
    klient.post(
        "/sakerhetskopia/aterstall",
        data={"fil": (io.BytesIO(data), "veckopeng.db")},
        content_type="multipart/form-data",
    )
    mapp = db.db_path().parent
    assert list(mapp.glob("fore-aterstallning-*.db"))
