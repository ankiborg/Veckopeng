"""Veckopeng – koll på vad vi är skyldiga barnen.

Kör: python app.py   →   http://localhost:5000
"""

from __future__ import annotations

import os
import sqlite3

from flask import Flask, flash, g, redirect, render_template, request, url_for

import allowance
import auth
import db
import savings
from clock import format_date, parse_date, today, weekday_name
from money import AmountError, format_ore, ore_to_input, parse_amount

DEFAULT_WEEKLY_ORE = 2500  # 25 kr

app = Flask(__name__)

# Installeras FÖRE _accrue nedan. before_request-funktioner körs i den
# ordning de registreras, och en utloggad besökare ska stoppas innan
# appen börjar skriva veckopengar i databasen.
auth.install(app)


# --- databas per request -------------------------------------------------


def conn() -> sqlite3.Connection:
    if "db" not in g:
        g.db = db.connect()
        db.init(g.db)
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    c = g.pop("db", None)
    if c is not None:
        c.close()


@app.before_request
def _accrue():
    """Bokför alla lördagar som passerat sedan förra sidvisningen."""
    if request.endpoint in {"static", "login", "logout", None}:
        return
    allowance.accrue_all(conn())


app.jinja_env.filters["kr"] = format_ore
app.jinja_env.filters["datum"] = format_date
app.jinja_env.filters["falt"] = ore_to_input


# --- vyer ----------------------------------------------------------------


@app.route("/")
def index():
    children = db.list_children(conn())
    return render_template(
        "index.html",
        children=children,
        total=db.total_owed(conn()),
        next_saturday=allowance.next_saturday(),
        weekday_name=weekday_name,
        today=today().isoformat(),
        default_weekly=DEFAULT_WEEKLY_ORE,
    )


@app.route("/barn/<int:child_id>")
def child(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))
    return render_template(
        "child.html",
        child=row,
        transactions=db.list_transactions(conn(), child_id, db.FICKPENGAR),
        savings_transactions=(
            db.list_transactions(conn(), child_id, db.SPARKONTO)
            if row["savings_on"] else []
        ),
        uttag_kvar=savings.withdrawals_left(conn(), row),
        matchat_denna_manad=savings.matched_this_month(conn(), child_id),
        next_saturday=allowance.next_saturday(),
        weekday_name=weekday_name,
        today=today().isoformat(),
    )


# --- åtgärder ------------------------------------------------------------


@app.post("/barn/ny")
def add_child():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Barnet behöver ett namn.", "fel")
        return redirect(url_for("index"))

    try:
        weekly = parse_amount(request.form.get("weekly") or "25")
        if weekly < 0:
            raise AmountError("Veckopengen kan inte vara minus.")
    except AmountError as exc:
        flash(str(exc), "fel")
        return redirect(url_for("index"))

    start = (request.form.get("start_date") or "").strip() or today().isoformat()
    try:
        parse_date(start)
    except ValueError:
        flash("Startdatumet är inget giltigt datum.", "fel")
        return redirect(url_for("index"))

    child_id = db.add_child(conn(), name, weekly, start)
    allowance.accrue_all(conn())
    flash(f"{name} är tillagd.", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/barn/<int:child_id>/transaktion")
def add_transaction(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))

    riktning = request.form.get("direction", "in")
    try:
        belopp = abs(parse_amount(request.form.get("amount") or ""))
    except AmountError as exc:
        flash(str(exc), "fel")
        return redirect(url_for("child", child_id=child_id))

    when = (request.form.get("date") or "").strip() or today().isoformat()
    try:
        parse_date(when)
    except ValueError:
        flash("Datumet är inte giltigt.", "fel")
        return redirect(url_for("child", child_id=child_id))

    note = (request.form.get("note") or "").strip()
    if riktning == "ut":
        db.add_transaction(conn(), child_id, when, -belopp, "uttag", note or "Uttag")
        flash(f"Drog bort {format_ore(belopp)} från {row['name']}.", "ok")
    else:
        db.add_transaction(
            conn(), child_id, when, belopp, "manuell", note or "Insättning"
        )
        flash(f"La till {format_ore(belopp)} till {row['name']}.", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/barn/<int:child_id>/betala-ut")
def pay_out(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))

    saldo = int(row["balance_ore"])
    if saldo <= 0:
        flash(f"{row['name']} har inget att få ut.", "fel")
        return redirect(url_for("child", child_id=child_id))

    db.add_transaction(
        conn(), child_id, today().isoformat(), -saldo, "uttag", "Utbetalt"
    )
    flash(f"Betalade ut {format_ore(saldo)} till {row['name']}. Saldot är nollat.", "ok")
    return redirect(url_for("child", child_id=child_id))


# --- sparkonto -----------------------------------------------------------


@app.post("/barn/<int:child_id>/spara")
def save_to_savings(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))

    # "Allt" flyttar hela fickpengssaldot utan att man behöver räkna själv.
    rå = (request.form.get("amount") or "").strip()
    if rå.lower() in {"allt", "alla"}:
        belopp = int(row["balance_ore"])
    else:
        try:
            belopp = abs(parse_amount(rå))
        except AmountError as exc:
            flash(str(exc), "fel")
            return redirect(url_for("child", child_id=child_id))

    when = (request.form.get("date") or "").strip() or today().isoformat()
    try:
        parse_date(when)
    except ValueError:
        flash("Datumet är inte giltigt.", "fel")
        return redirect(url_for("child", child_id=child_id))

    try:
        bonus = savings.deposit(conn(), row, belopp, when)
    except savings.SavingsError as exc:
        flash(str(exc), "fel")
        return redirect(url_for("child", child_id=child_id))

    if bonus:
        flash(
            f"{format_ore(belopp)} till sparkontot – och vi la på "
            f"{format_ore(bonus)}. {row['name']} fick "
            f"{format_ore(belopp + bonus)}.",
            "ok",
        )
    elif int(row["match_percent"] or 0) > 0:
        # Taket är slut. Säg det rakt ut i stället för att tyst hoppa över.
        flash(
            f"{format_ore(belopp)} till sparkontot. Månadens matchning är "
            f"redan uttömd, så ingen bonus den här gången.",
            "ok",
        )
    else:
        flash(f"{format_ore(belopp)} flyttat till sparkontot.", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/barn/<int:child_id>/ta-ut-sparade")
def withdraw_savings(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))

    rå = (request.form.get("amount") or "").strip()
    if rå.lower() in {"allt", "alla"}:
        belopp = int(row["savings_ore"])
    else:
        try:
            belopp = abs(parse_amount(rå))
        except AmountError as exc:
            flash(str(exc), "fel")
            return redirect(url_for("child", child_id=child_id))

    override = request.form.get("override") == "1"
    try:
        savings.withdraw(conn(), row, belopp, override=override)
    except savings.SavingsError as exc:
        flash(str(exc), "fel")
        return redirect(url_for("child", child_id=child_id))

    kvar = savings.withdrawals_left(conn(), row)
    svans = "" if kvar < 0 else f" {kvar} uttag kvar i år."
    flash(f"{format_ore(belopp)} tillbaka till fickpengarna.{svans}", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/barn/<int:child_id>/sparinstallningar")
def savings_settings(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))

    pa = request.form.get("savings_on") == "1"

    def heltal(falt: str, standard: int) -> int:
        try:
            return max(0, int((request.form.get(falt) or "").strip() or standard))
        except ValueError:
            return standard

    procent = heltal("match_percent", 100)
    uttag = heltal("withdrawals_per_year", 2)
    try:
        tak_text = (request.form.get("match_cap") or "").strip()
        tak = abs(parse_amount(tak_text)) if tak_text else 0
    except AmountError:
        tak = int(row["match_cap_ore"])

    db.update_savings_settings(conn(), child_id, pa, procent, tak, uttag)
    flash("Sparkontots inställningar är sparade.", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/transaktion/<int:txn_id>/ta-bort")
def delete_transaction(txn_id: int):
    row = db.get_transaction(conn(), txn_id)
    if row is None:
        flash("Den posten finns inte.", "fel")
        return redirect(url_for("index"))

    child_id = row["child_id"]
    antal = db.delete_transaction(conn(), txn_id)
    if row["group_id"]:
        # En överföring är flera rader. Tas bara den ena bort uppstår
        # pengar ur tomma intet på det andra kontot.
        flash(f"Hela överföringen togs bort ({antal} rader).", "ok")
    elif row["kind"] == allowance.KIND:
        # Utan spärr skulle nästa sidvisning bokföra samma lördag igen.
        db.add_transaction(
            conn(),
            child_id,
            row["date"],
            0,
            allowance.KIND,
            "Veckopeng borttagen",
        )
        flash("Veckopengen togs bort och fylls inte på igen.", "ok")
    else:
        flash("Posten togs bort.", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/barn/<int:child_id>/installningar")
def update_child(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        flash("Det barnet finns inte.", "fel")
        return redirect(url_for("index"))

    name = (request.form.get("name") or "").strip() or row["name"]
    try:
        weekly = parse_amount(request.form.get("weekly") or "")
        if weekly < 0:
            raise AmountError("Veckopengen kan inte vara minus.")
    except AmountError as exc:
        flash(str(exc), "fel")
        return redirect(url_for("child", child_id=child_id))

    db.update_child(conn(), child_id, name, weekly)
    flash("Sparat.", "ok")
    return redirect(url_for("child", child_id=child_id))


@app.post("/barn/<int:child_id>/ta-bort")
def remove_child(child_id: int):
    row = db.get_child(conn(), child_id)
    if row is None:
        return redirect(url_for("index"))
    if (request.form.get("confirm") or "").strip().lower() != row["name"].lower():
        flash("Skriv barnets namn för att bekräfta borttagningen.", "fel")
        return redirect(url_for("child", child_id=child_id))
    db.delete_child(conn(), child_id)
    flash(f"{row['name']} och all historik är borttagen.", "ok")
    return redirect(url_for("index"))


if __name__ == "__main__":
    # Bara för körning hemma. På Railway startar gunicorn appen i stället
    # (se Procfile) – Flasks egen server är inte byggd för att stå publik.
    #
    # debug=True får ALDRIG stå på mot internet: felsidan har en konsol
    # där vem som helst kan köra kod på servern. Därför en egen variabel
    # som måste sättas med flit, inte ett påslaget standardläge.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("VECKOPENG_DEBUG") == "1",
    )
