# Veckopeng

Håller koll på hur mycket vi är skyldiga barnen – veckopeng som fylls på
automatiskt varje lördag, plus pengar de tjänar in eller tar ut.

- **Startvy** med saldot för alla barn och en totalsumma.
- **Barnsida** med hela historiken, där du lägger till och drar bort pengar.
- **25 kr varje lördag**, bokfört automatiskt. Beloppet går att ändra per barn.

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
`VECKOPENG_DB` pekar om den om du vill ha den någon annanstans.

Pengar lagras i **öre** som heltal, aldrig som decimaltal. Ett saldo som
räknas i flyttal driver i väg efter tillräckligt många insättningar.

## Tester

```powershell
python -m pytest tests -q
```

## Filer

| Fil | Vad den gör |
| --- | --- |
| `app.py` | Flask-appen: rutter, formulärhantering, validering |
| `allowance.py` | Lördagspåfyllningen |
| `db.py` | SQLite-schema och alla frågor |
| `money.py` | Öre in och ut, svensk formatering |
| `clock.py` | Svensk tid, lördagar, datumformat |
| `templates/` | Startvy och barnsida |
| `static/style.css` | Utseendet, ljust och mörkt läge |
