"""Sparkontot: matchning, tak, uttagsgräns och att pengar inte uppstår."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db  # noqa: E402
import savings  # noqa: E402


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("VECKOPENG_DB", str(tmp_path / "test.db"))
    c = db.connect()
    db.init(c)
    yield c
    c.close()


def barn(conn, fickpengar_ore=0, **installningar):
    """Ett barn med sparkonto på och pengar i fickan."""
    cid = db.add_child(conn, "Elsa", 2500, "2026-01-03")
    val = {
        "savings_on": True,
        "match_percent": 100,
        "match_cap_ore": 10000,
        "withdrawals_per_year": 2,
    }
    val.update(installningar)
    db.update_savings_settings(conn, cid, **val)
    if fickpengar_ore:
        db.add_transaction(conn, cid, "2026-08-01", fickpengar_ore, "manuell", "Start")
    return db.get_child(conn, cid)


# --- matchning -----------------------------------------------------------


def test_dubbling_lagger_pa_lika_mycket(conn):
    b = barn(conn, 10000)
    bonus = savings.deposit(conn, b, 5000, "2026-08-05")

    assert bonus == 5000
    efter = db.get_child(conn, b["id"])
    assert efter["balance_ore"] == 5000       # 100 - 50 flyttat
    assert efter["savings_ore"] == 10000      # 50 + 50 i bonus
    assert efter["total_ore"] == 15000        # vi är skyldiga 50 kr mer än förut


def test_halv_matchning(conn):
    b = barn(conn, 10000, match_percent=50)
    assert savings.deposit(conn, b, 4000, "2026-08-05") == 2000


def test_ingen_matchning_nar_procenten_ar_noll(conn):
    b = barn(conn, 10000, match_percent=0)
    assert savings.deposit(conn, b, 4000, "2026-08-05") == 0
    assert db.get_child(conn, b["id"])["savings_ore"] == 4000


def test_taket_haller_kostnaden_nere(conn):
    """Taket är det som gör matchningen omöjlig att mjölka."""
    b = barn(conn, 100000, match_cap_ore=10000)  # tak 100 kr/månad

    assert savings.deposit(conn, db.get_child(conn, b["id"]), 6000, "2026-08-05") == 6000
    # 40 kr kvar av taket, trots att 60 kr skulle ge 60 kr i bonus
    assert savings.deposit(conn, db.get_child(conn, b["id"]), 6000, "2026-08-06") == 4000
    assert savings.deposit(conn, db.get_child(conn, b["id"]), 6000, "2026-08-07") == 0

    assert savings.matched_this_month(conn, b["id"], date(2026, 8, 5)) == 10000


def test_taket_nollstalls_till_nasta_manad(conn):
    b = barn(conn, 100000, match_cap_ore=10000)
    savings.deposit(conn, db.get_child(conn, b["id"]), 10000, "2026-08-05")
    assert savings.matched_this_month(conn, b["id"], date(2026, 8, 5)) == 10000

    assert savings.deposit(conn, db.get_child(conn, b["id"]), 5000, "2026-09-01") == 5000


def test_utan_tak_matchas_allt(conn):
    b = barn(conn, 100000, match_cap_ore=0)
    assert savings.deposit(conn, b, 80000, "2026-08-05") == 80000


def test_aterinsattning_ger_inte_gratis_pengar_i_all_oandlighet(conn):
    """Mjölkningen: ta ut, sätt in igen, få bonus igen. Taket stoppar den."""
    b = barn(conn, 20000, match_cap_ore=10000)

    savings.deposit(conn, db.get_child(conn, b["id"]), 20000, "2026-08-05")
    savings.withdraw(conn, db.get_child(conn, b["id"]), 30000, "2026-08-06")
    savings.deposit(conn, db.get_child(conn, b["id"]), 30000, "2026-08-07")

    # Vår kostnad är taket, inte 20 + 30 kr
    bonusar = [
        t["amount_ore"]
        for t in db.list_transactions(conn, b["id"])
        if t["kind"] == savings.BONUS
    ]
    assert sum(bonusar) == 10000


# --- pengarna måste komma någonstans ifrån -------------------------------


def test_kan_inte_spara_mer_an_som_finns_i_fickan(conn):
    b = barn(conn, 5000)
    with pytest.raises(savings.SavingsError, match="inte så mycket"):
        savings.deposit(conn, b, 6000, "2026-08-05")

    assert db.get_child(conn, b["id"])["savings_ore"] == 0


def test_kan_inte_ta_ut_mer_an_som_finns_pa_sparkontot(conn):
    b = barn(conn, 10000)
    savings.deposit(conn, b, 2000, "2026-08-05")
    with pytest.raises(savings.SavingsError, match="inte så mycket"):
        savings.withdraw(conn, db.get_child(conn, b["id"]), 99999, "2026-08-06")


def test_sparkonto_maste_vara_pasatt(conn):
    b = barn(conn, 10000, savings_on=False)
    with pytest.raises(savings.SavingsError, match="inte påslaget"):
        savings.deposit(conn, b, 1000, "2026-08-05")


def test_overforing_flyttar_pengar_inte_skapar_dem(conn):
    """Bortsett från bonusen ska en flytt lämna totalen orörd."""
    b = barn(conn, 10000, match_percent=0)
    fore = db.get_child(conn, b["id"])["total_ore"]

    savings.deposit(conn, b, 4000, "2026-08-05")
    savings.withdraw(conn, db.get_child(conn, b["id"]), 1500, "2026-08-06")

    assert db.get_child(conn, b["id"])["total_ore"] == fore


# --- uttagsgränsen -------------------------------------------------------


def test_tva_uttag_gar_bra_det_tredje_stoppas(conn):
    b = barn(conn, 50000, withdrawals_per_year=2)
    savings.deposit(conn, db.get_child(conn, b["id"]), 50000, "2026-08-05")

    savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2026-08-06")
    savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2026-08-07")
    with pytest.raises(savings.SavingsError, match="redan gjort 2 uttag"):
        savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2026-08-08")


def test_granser_gar_att_passera_med_flit(conn):
    b = barn(conn, 50000, withdrawals_per_year=1)
    savings.deposit(conn, db.get_child(conn, b["id"]), 50000, "2026-08-05")
    savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2026-08-06")

    savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2026-08-07", override=True)

    # ... men undantaget ska synas i historiken
    texter = [t["note"] for t in db.list_transactions(conn, b["id"], db.SPARKONTO)]
    assert any("utöver årsgränsen" in t for t in texter)


def test_uttagsgransen_nollstalls_vid_arsskiftet(conn):
    b = barn(conn, 50000, withdrawals_per_year=1)
    savings.deposit(conn, db.get_child(conn, b["id"]), 50000, "2026-08-05")
    savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2026-12-30")

    savings.withdraw(conn, db.get_child(conn, b["id"]), 1000, "2027-01-02")


def test_noll_uttag_betyder_ingen_grans(conn):
    b = barn(conn, 50000, withdrawals_per_year=0)
    savings.deposit(conn, db.get_child(conn, b["id"]), 50000, "2026-08-05")
    for dag in range(1, 10):
        savings.withdraw(conn, db.get_child(conn, b["id"]), 100, f"2026-09-0{dag}")
    assert savings.withdrawals_left(conn, db.get_child(conn, b["id"])) == -1


# --- borttagning ---------------------------------------------------------


def test_borttagen_overforing_tar_med_sig_bada_raderna(conn):
    """Tas bara ena benet bort uppstår pengar ur tomma intet."""
    b = barn(conn, 10000)
    savings.deposit(conn, b, 4000, "2026-08-05")
    fore = db.get_child(conn, b["id"])["total_ore"]

    ett_ben = db.list_transactions(conn, b["id"], db.SPARKONTO)[0]
    assert db.delete_transaction(conn, ett_ben["id"]) == 3  # uttag + insättning + bonus

    efter = db.get_child(conn, b["id"])
    assert efter["balance_ore"] == 10000
    assert efter["savings_ore"] == 0
    assert efter["total_ore"] == fore - 4000  # bonusen försvann med den


def test_vanlig_post_tas_bort_ensam(conn):
    b = barn(conn, 10000)
    post = db.list_transactions(conn, b["id"])[0]
    assert db.delete_transaction(conn, post["id"]) == 1


# --- migrering -----------------------------------------------------------


def test_gammal_databas_far_de_nya_kolumnerna(tmp_path):
    """Databasen på Railway innehåller riktiga saldon och byggs inte om."""
    fil = tmp_path / "gammal.db"
    gammal = db.connect(fil)
    gammal.executescript(
        """
        CREATE TABLE child (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT
            NOT NULL, weekly_ore INTEGER NOT NULL DEFAULT 2500, start_date TEXT
            NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE txn (id INTEGER PRIMARY KEY AUTOINCREMENT, child_id INTEGER
            NOT NULL, date TEXT NOT NULL, amount_ore INTEGER NOT NULL, kind TEXT
            NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        INSERT INTO child (name, weekly_ore, start_date, created_at)
            VALUES ('Elsa', 2500, '2026-01-03', '2026-01-03');
        INSERT INTO txn (child_id, date, amount_ore, kind, note, created_at)
            VALUES (1, '2026-01-03', 2500, 'veckopeng', 'Veckopeng', '2026-01-03');
        """
    )
    gammal.commit()

    gjort = db.migrate(gammal)
    assert "txn.account" in gjort and "child.savings_on" in gjort

    # Saldot ska överleva, och den gamla raden ska räknas som fickpengar.
    barn_rad = db.get_child(gammal, 1)
    assert barn_rad["balance_ore"] == 2500
    assert barn_rad["savings_ore"] == 0
    assert barn_rad["savings_on"] == 0

    assert db.migrate(gammal) == []  # ska gå att köra om
    gammal.close()
