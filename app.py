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
        transactions=db.list_transactions(conn(), child_id),
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


@app.post("/transaktion/<int:txn_id>/ta-bort")
def delete_transaction(txn_id: int):
    row = db.get_transaction(conn(), txn_id)
    if row is None:
        flash("Den posten finns inte.", "fel")
        return redirect(url_for("index"))

    child_id = row["child_id"]
    db.delete_transaction(conn(), txn_id)
    if row["kind"] == allowance.KIND:
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
