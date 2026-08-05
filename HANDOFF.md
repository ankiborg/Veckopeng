# Handoff – Veckopeng

Projektets minne mellan chattar. Läs den här först.

## Nuläge (2026-08-05)

Första versionen är byggd och mergad: startvy för alla barn, drilldown till
enskild sida, automatisk påfyllning med 25 kr varje lördag, samt att lägga
till och dra bort pengar manuellt.

Därefter gjord redo för Railway – volym, gunicorn, lösenordsskydd. Se
"Railway" nedan och deploy-avsnittet i `README.md`.

45 tester gröna. Appen är klickad igenom i webbläsare i ljust och mörkt läge,
och provkörd under gunicorn med lösenord och volym påslaget.

## Railway

Appen är byggd för att köras där, men **två inställningar är inte valfria**:

1. **Volym monterad på `/data`, med `VECKOPENG_DB=/data/veckopeng.db`.**
   Railways filsystem nollställs vid varje deploy. Utan volym raderas alla
   saldon nästa gång någon pushar.
2. **`VECKOPENG_LOSENORD` satt.** Adressen är publik. Utan lösenord kan vem
   som helst som hittar den läsa och ändra barnens pengar.

`Procfile` sköter starten (gunicorn, 2 arbetare). `PORT` sätter Railway själv.
`VECKOPENG_SECRET` behöver inte sättas – nyckeln härleds ur lösenordet.

Databasen ligger bara på volymen. Den är familjens enda kopia, så hämta ut
`/data/veckopeng.db` med jämna mellanrum.

## Beslut som är värda att känna till

**Påfyllningen räknas i efterskott, inte av en schemaläggare.** Varje
sidvisning bokför alla lördagar som passerat sedan sist (`allowance.py`).
En cron som inte kördes för att datorn var avstängd i helgen tappar en
veckopeng och då stämmer inte saldot – det här ger samma svar oavsett hur
länge appen legat nere. Dubbelbokning stoppas av det partiella unique-indexet
`ux_txn_veckopeng` i `db.SCHEMA`, inte av kontroller i koden.

**Pengar är heltal i öre.** 25,50 kr = 2550. Flyttal driver i väg och det
här är pengar vi faktiskt är skyldiga barnen.

**Tid är alltid Europe/Stockholm** (`clock.py`), aldrig serverns tid. Kör
appen någonstans i UTC hamnar lördagens påfyllning fel kring midnatt.

**Månadsnamn kommer ur `clock.MONTHS`, inte ur systemets locale.** En server
i USA skulle annars skriva "Aug" i stället för "aug".

**Ändrad veckopeng gäller bara framåt.** Redan bokförda lördagar rörs inte.

## Fällor som redan kostat tid

- **Tar man bort en automatisk veckopeng lägger appen en nollpost på samma
  datum.** Utan den skulle nästa sidvisning bokföra lördagen igen – posten
  ockuperar platsen i unique-indexet. Den visas nedtonad i historiken.
- **CSS: `.historik .belopp` sätter grön färg.** Minusregeln måste därför stå
  sist i `style.css`, annars skrivs uttagen i grönt trots `.minus`-klassen.
- **Barnkortets grid behöver explicita rader och kolumner.** Saldot spänner
  över två rader; med bara auto-placering hamnar det i vänsterkolumnen och
  knuffar undan namnet.
- **`tzdata` måste vara installerat på Windows.** `zoneinfo` har ingen egen
  tidszonsdatabas där och appen kraschar utan den.
- **`debug=True` får aldrig stå på mot internet.** Werkzeugs felsida har en
  konsol där vem som helst kan köra kod på servern. Därför styrs den av
  `VECKOPENG_DEBUG=1`, som måste sättas med flit.
- **`auth.install()` måste anropas före `_accrue` registreras.**
  `before_request` körs i registreringsordning, och en utloggad besökare ska
  stoppas innan appen börjar skriva veckopengar.
- **Sessionsnyckeln får inte slumpas vid start.** Railway startar om vid varje
  deploy och alla skulle loggas ut. Den härleds ur lösenordet i stället.
- **SQLite körs i WAL-läge.** Utan det låser gunicorns två arbetare ut
  varandra och sidan svarar "database is locked" när två telefoner laddar
  samtidigt.
- **`?nasta=` vid inloggning släpper bara igenom interna sökvägar.** Annars går
  sidan att använda för att skicka någon vidare till en annan webbplats.

## Backlog

Inget av det här är beställt än – det är sådant som ligger nära till hands.

1. **Automatisk säkerhetskopia.** Databasen på Railways volym är enda kopian.
   En nattlig dump till någon annanstans vore billig försäkring.
2. **Sparmål.** "Elsa sparar till en cykel, 800 kr" med en stapel som visar
   hur långt hon kommit.
3. **Återkommande sysslor** med fast belopp, i stället för att skriva in
   samma sak varje vecka.
4. **Export.** En CSV-knapp så historiken går att titta på i Excel.
5. **Barnvy.** En sida barnen kan öppna själva, utan knapparna som ändrar.
   Kräver ett andra lösenord med läsbehörighet.

## Filkarta

| Fil | Vad den gör |
| --- | --- |
| `app.py` | Flask-appen: rutter, formulärhantering, validering |
| `auth.py` | Lösenordsskyddet, avstängt när `VECKOPENG_LOSENORD` är tom |
| `allowance.py` | Lördagspåfyllningen och `next_saturday()` |
| `db.py` | SQLite-schema och alla frågor |
| `money.py` | `parse_amount()`, `format_ore()`, `ore_to_input()` |
| `clock.py` | Svensk tid, `saturdays()`, `format_date()` |
| `templates/index.html` | Startvyn med alla barn |
| `templates/child.html` | Barnsidan med historik och formulär |
| `templates/login.html` | Inloggningen |
| `static/style.css` | Utseendet, ljust och mörkt läge |
| `Procfile` | Startkommandot Railway använder |
| `tests/test_veckopeng.py` | 34 tester för pengar, lördagar och saldon |
| `tests/test_auth.py` | 11 tester för inloggningen |
