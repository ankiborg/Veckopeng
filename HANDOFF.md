# Handoff – Veckopeng

Projektets minne mellan chattar. Läs den här först.

## Nuläge (2026-08-05)

Första versionen är byggd och verifierad. Allt som efterfrågades finns:
startvy för alla barn, drilldown till enskild sida, automatisk påfyllning
med 25 kr varje lördag, samt att lägga till och dra bort pengar manuellt.

34 tester gröna. Appen är klickad igenom i webbläsare, ljust och mörkt läge.

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

## Backlog

Inget av det här är beställt än – det är sådant som ligger nära till hands.

1. **Delad åtkomst.** Appen kör lokalt på en dator. Ska den nås från flera
   telefoner utan att datorn är på behöver den ligga på en hosting.
2. **Sparmål.** "Elsa sparar till en cykel, 800 kr" med en stapel som visar
   hur långt hon kommit.
3. **Återkommande sysslor** med fast belopp, i stället för att skriva in
   samma sak varje vecka.
4. **Export.** En CSV-knapp så historiken går att titta på i Excel.
5. **Barnvy.** En sida barnen kan öppna själva, utan knapparna som ändrar.

## Filkarta

| Fil | Vad den gör |
| --- | --- |
| `app.py` | Flask-appen: rutter, formulärhantering, validering |
| `allowance.py` | Lördagspåfyllningen och `next_saturday()` |
| `db.py` | SQLite-schema och alla frågor |
| `money.py` | `parse_amount()`, `format_ore()`, `ore_to_input()` |
| `clock.py` | Svensk tid, `saturdays()`, `format_date()` |
| `templates/index.html` | Startvyn med alla barn |
| `templates/child.html` | Barnsidan med historik och formulär |
| `static/style.css` | Utseendet, ljust och mörkt läge |
| `tests/test_veckopeng.py` | 34 tester |
