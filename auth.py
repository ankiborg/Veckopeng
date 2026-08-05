"""Lösenordsskydd för appen när den ligger på internet.

Hemma på egna wifit behövs inget. Ute på Railway har appen en publik
adress, och utan spärr kan vem som helst som hittar den läsa och ändra
barnens pengar. Sätt VECKOPENG_LOSENORD där.

Är variabeln tom är skyddet avstängt – det är det som gör att
`python app.py` hemma fungerar utan inloggning.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta

from flask import redirect, render_template, request, session, url_for

SESSION_KEY = "inloggad"
INLOGGNINGEN_VARAR = timedelta(days=90)  # telefonen ska slippa fråga varje dag


def password() -> str:
    return os.environ.get("VECKOPENG_LOSENORD", "").strip()


def required() -> bool:
    return bool(password())


def secret_key() -> str:
    """Nyckeln som signerar sessionskakan.

    Slumpas den vid start loggas alla ut varje gång appen startar om, och
    Railway startar om vid varje deploy. Härleds den ur lösenordet blir
    den stabil utan att bli ännu en miljövariabel att hålla reda på.
    """
    if explicit := os.environ.get("VECKOPENG_SECRET", "").strip():
        return explicit
    if lösen := password():
        return hashlib.sha256(f"veckopeng-session:{lösen}".encode()).hexdigest()
    return "veckopeng-lokal-nyckel"


def install(app) -> None:
    """Koppla på inloggningen. Gör ingenting om inget lösenord är satt."""
    app.secret_key = secret_key()
    app.permanent_session_lifetime = INLOGGNINGEN_VARAR

    @app.context_processor
    def _mallvariabler():
        # Utloggningsknappen ska bara synas när det finns något att logga
        # ut från – inte hemma utan lösenord, och inte på inloggningssidan.
        return {"losenordsskydd": required() and bool(session.get(SESSION_KEY))}

    @app.before_request
    def _kraev_inloggning():
        if not required():
            return None
        if request.endpoint in {"static", "login", "logout", None}:
            return None
        if session.get(SESSION_KEY):
            return None
        return redirect(url_for("login", nasta=request.path))

    @app.route("/logga-in", methods=["GET", "POST"])
    def login():
        if not required() or session.get(SESSION_KEY):
            return redirect(url_for("index"))

        fel = None
        if request.method == "POST":
            # compare_digest i stället för == : en vanlig jämförelse
            # avbryter vid första felaktiga tecknet och läcker längden.
            if secrets.compare_digest(request.form.get("losenord", ""), password()):
                session.permanent = True
                session[SESSION_KEY] = True
                nasta = request.args.get("nasta", "")
                # Bara interna sökvägar – annars går sidan att använda för
                # att skicka någon vidare till en annan webbplats.
                if nasta.startswith("/") and not nasta.startswith("//"):
                    return redirect(nasta)
                return redirect(url_for("index"))
            fel = "Fel lösenord."

        return render_template("login.html", fel=fel), (401 if fel else 200)

    @app.post("/logga-ut")
    def logout():
        session.clear()
        return redirect(url_for("login") if required() else url_for("index"))
