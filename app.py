"""Veckopeng – koll på vad vi är skyldiga barnen.

Kör: python app.py   →   http://localhost:5000
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import allowance
import auth
import db
import valuta
from clock import format_date, now_local, parse_date, today, weekday_name
from money import AmountError, format_ore, ore_to_input, parse_amount

DEFAULT_WEEKLY_ORE = 2500  # 25 kr

app = Flask(__name__)
# En säkerhetskopia är några hundra kB. Taket stoppar en felvald film.
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

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


# --- visning: kronor eller kanadensiska dollar ---------------------------
#
# Valet sparas i sessionskakan, per telefon. Databasen är alltid i öre.


def i_cad() -> bool:
    return session.get("valuta") == "CAD"


def med_moms() -> bool:
    """Momsläget finns bara i CAD – svenska prislappar har redan momsen i sig."""
    return i_cad() and bool(session.get("moms"))


def kurs() -> valuta.Kurs:
    if "kurs" not in g:
        g.kurs = valuta.kurs(conn())
    return g.kurs


def visa(ore: int) -> str:
    """Ett belopp i den valuta som är vald."""
    return valuta.format_cent(kurs().till_cent(ore)) if i_cad() else format_ore(ore)


def handla(ore: int) -> str:
    """Ett saldo som det barnet kan handla för – före moms i momsläget."""
    if med_moms() and ore > 0:
        return valuta.format_cent(valuta.fore_moms(kurs().till_cent(ore)))
    return visa(ore)


@app.context_processor
def _visning():
    return {
        "sparas_sakert": db.sparas_sakert(),
        "i_cad": i_cad(),
        "med_moms": med_moms(),
        "kurs": kurs if i_cad() else None,
        "moms_procent": valuta.moms_procent(),
    }


app.jinja_env.filters["kr"] = visa
app.jinja_env.filters["handla"] = handla
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


@app.post("/visning")
def set_display():
    if (vald := request.form.get("valuta")) in {"SEK", "CAD"}:
        session["valuta"] = vald
    if (moms := request.form.get("moms")) is not None:
        session["moms"] = moms == "1"
    session.permanent = True  # ska komma ihåg valet, inte glömma det vid stängd flik
    tillbaka = request.form.get("tillbaka", "")
    # Bara interna sökvägar, samma spärr som vid inloggningen.
    if tillbaka.startswith("/") and not tillbaka.startswith("//"):
        return redirect(tillbaka)
    return redirect(url_for("index"))


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
    text = request.form.get("amount") or ""
    try:
        if i_cad():
            # Skrivet i dollar: tolkas som cent och räknas om till öre.
            # Det skrivna beloppet hamnar i anteckningen, så historiken
            # stämmer med kvittot även när kursen rört sig.
            cent = abs(parse_amount(text.replace("$", "").upper().replace("CAD", "")))
            belopp = kurs().till_ore(cent)
        else:
            belopp = abs(parse_amount(text))
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
    standard = "Uttag" if riktning == "ut" else "Insättning"
    note = note or standard
    if i_cad():
        note = f"{note} ({valuta.format_cent(cent)})"
        skrivet = valuta.format_cent(cent)
    else:
        skrivet = format_ore(belopp)
    if riktning == "ut":
        db.add_transaction(conn(), child_id, when, -belopp, "uttag", note)
        flash(f"Drog bort {skrivet} från {row['name']}.", "ok")
    else:
        db.add_transaction(conn(), child_id, when, belopp, "manuell", note)
        flash(f"La till {skrivet} till {row['name']}.", "ok")
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
    flash(f"Betalade ut {visa(saldo)} till {row['name']}. Saldot är nollat.", "ok")
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


# --- säkerhetskopia -------------------------------------------------------


@app.get("/sakerhetskopia")
def download_backup():
    """Hela databasen som en fil. Går att spara i Filer på telefonen."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    kopia = sqlite3.connect(tmp)
    try:
        db.kopiera(conn(), kopia)
    finally:
        kopia.close()
    svar = send_file(
        tmp,
        as_attachment=True,
        download_name=f"veckopeng-{today().isoformat()}.db",
        mimetype="application/octet-stream",
        max_age=0,
    )
    svar.call_on_close(lambda: os.unlink(tmp))
    return svar


@app.post("/sakerhetskopia/aterstall")
def restore_backup():
    fil = request.files.get("fil")
    if fil is None or not fil.filename:
        flash("Välj en säkerhetskopia att läsa in.", "fel")
        return redirect(url_for("index"))

    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        fil.save(tmp)
        if not db.ar_veckopengsfil(tmp):
            flash("Filen är ingen säkerhetskopia från Veckopeng. Inget ändrades.", "fel")
            return redirect(url_for("index"))

        # Det som ligger i appen nu sparas bredvid innan det skrivs över –
        # valde man fel fil ska det gå att ångra.
        fore = db.db_path().parent / f"fore-aterstallning-{now_local():%Y%m%d-%H%M%S}.db"
        sparad = sqlite3.connect(str(fore))
        try:
            db.kopiera(conn(), sparad)
        finally:
            sparad.close()

        kalla = sqlite3.connect(tmp)
        try:
            db.kopiera(kalla, conn())
        finally:
            kalla.close()
        db.init(conn())  # en äldre kopia kan sakna nyare tabeller
    finally:
        os.unlink(tmp)

    allowance.accrue_all(conn())
    flash("Säkerhetskopian är inläst.", "ok")
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
