# Veckopeng

Håller koll på hur mycket vi är skyldiga barnen – veckopeng som fylls på
automatiskt varje lördag, plus pengar de tjänar in eller tar ut.

- **Startvy** med saldot för alla barn och en totalsumma.
- **Barnsida** med hela historiken, där du lägger till och drar bort pengar.
- **25 kr varje lördag**, bokfört automatiskt. Beloppet går att ändra per barn.
- **Kanadensiska dollar** med en växel överst, och en momsknapp som visar vad
  barnen kan handla för när 13 % moms (Ontario) läggs på i kassan. Kursen
  hämtas automatiskt; `VECKOPENG_CAD_KURS` låser den om du vill.

## Kom igång

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Öppna <http://localhost:5000>. Första gången lägger du upp barnen; välj
startdatum för när veckopengen ska börja räknas.

På Mac/Linux är det `python3 -m venv .venv` och `source .venv/bin/activate`.

### Från mobilen

Appen lyssnar på hela nätverket, så telefonen på samma wifi kommer åt den via
datorns IP-adress: `http://192.168.x.x:5000`. Ta reda på adressen med
`ipconfig` (Windows) eller `ifconfig` (Mac/Linux).

## Så fungerar lördagspåfyllningen

Ingen schemaläggare, ingen bakgrundsprocess. Appen räknar **i efterskott**:
varje gång en sida öppnas bokförs alla lördagar som passerat sedan sist.

Det är avsiktligt. En cron-körning som missas för att datorn var avstängd i
helgen tappar en veckopeng, och då stämmer inte saldot längre. Den här
lösningen ger samma svar oavsett om appen legat nere i en dag eller i tre
månader. Dubbelbokning stoppas av ett unikt index i databasen, inte av
kontroller i koden – kör man påfyllningen tio gånger blir det ändå en peng
per lördag.

Ändrar du veckopengen gäller det bara framåt. Redan bokförda lördagar ligger
kvar med det gamla beloppet.

## Data

Allt ligger i `veckopeng.db` (SQLite) bredvid koden. Filen är gitignorerad –
kopiera den för att säkerhetskopiera, och lägg den tillbaka för att återställa.
`VECKOPENG_DB` pekar om den om du vill ha den någon annanstans; på Railway
måste den peka på en volym.

Hemma behövs inget lösenord. Sätts `VECKOPENG_LOSENORD` slås inloggningen på –
gör det så fort appen ligger på en publik adress.

Pengar lagras i **öre** som heltal, aldrig som decimaltal. Ett saldo som
räknas i flyttal driver i väg efter tillräckligt många insättningar.

## Lägga upp den på Railway

Fyra saker, och de två första är inte valfria.

### 1. En volym – annars försvinner all data

Railways filsystem nollställs vid varje deploy. Utan volym ligger
`veckopeng.db` i containern och raderas nästa gång du pushar en ändring. Alla
saldon är då borta.

I Railway: **Service → Variables → + Volume**, montera den på `/data`.

### 2. Miljövariabler

| Variabel | Värde | Varför |
| --- | --- | --- |
| `VECKOPENG_DB` | `/data/veckopeng.db` | Lägger databasen på volymen |
| `VECKOPENG_LOSENORD` | ett lösenord ni väljer | Adressen är publik – utan detta kan vem som helst som hittar den ändra barnens pengar |

`PORT` sätter Railway själv. `VECKOPENG_SECRET` behövs inte; nyckeln som
signerar inloggningen härleds ur lösenordet så att den överlever omstarter.

### 3. Startkommandot

`Procfile` säger redan åt Railway att köra gunicorn. Du behöver inte skriva
något i **Custom Start Command** – lämna det tomt.

Railway bygger med Nixpacks och hittar `requirements.txt` automatiskt.

### 4. Domän

**Settings → Networking → Generate Domain** ger appen en adress. Spara den som
bokmärke på hemskärmen i telefonen så ser den ut som en app.

### Byta lösenord senare

Ändra `VECKOPENG_LOSENORD` i Railway. Alla loggas ut, eftersom sessionsnyckeln
följer lösenordet.

### Säkerhetskopiera

Databasen ligger på volymen, inte i git. `railway ssh` och kopiera ut
`/data/veckopeng.db` med jämna mellanrum – det är familjens enda kopia.

## Tester

```powershell
python -m pytest tests -q
```

## Filer

| Fil | Vad den gör |
| --- | --- |
| `app.py` | Flask-appen: rutter, formulärhantering, validering |
| `auth.py` | Lösenordsskyddet när appen ligger publikt |
| `allowance.py` | Lördagspåfyllningen |
| `db.py` | SQLite-schema och alla frågor |
| `money.py` | Öre in och ut, svensk formatering |
| `valuta.py` | Kanadensiska dollar och moms |
| `clock.py` | Svensk tid, lördagar, datumformat |
| `templates/` | Startvy, barnsida och inloggning |
| `static/style.css` | Utseendet, ljust och mörkt läge |
| `Procfile` | Startkommandot Railway använder |
