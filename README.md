# QrGolBot - Bot Telegram per Notifiche 1-1 Live

Bot Telegram che monitora tutte le partite live e invia notifiche quando il punteggio diventa 1-1 con pattern specifici (1-0/0-1 → 1-1 entro 10 minuti, stessa metà tempo).

## 📋 Requisiti

- Python 3.11+ (3.13 supportato con shim `imghdr.py`)
- Account API-Football (piano free: 100 chiamate/giorno)
- Bot Telegram (creato via [@BotFather](https://t.me/BotFather))
- Account Render (o altro hosting gratuito)

## 🚀 Deploy su Render (Gratuito)

### 1. Preparazione Repository

Assicurati che il repository GitHub contenga:
- `live_goals_bot.py`
- `requirements.txt`
- `Procfile` (opzionale, ma consigliato)
- `imghdr.py` (shim per Python 3.13)
- `runtime.txt` (opzionale, per forzare Python 3.11)

### 2. Creazione Servizio su Render

1. Vai su [Render Dashboard](https://dashboard.render.com)
2. Clicca **"New +"** → **"Web Service"**
3. Connetti il repository GitHub: `AndreaTrapani2004/QrGolBot`
4. Configurazione:
   - **Name**: `qrgolbot` (o nome a scelta)
   - **Region**: scegli la più vicina
   - **Branch**: `main`
   - **Root Directory**: (lascia vuoto)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python live_goals_bot.py`

### 3. Variabili d'Ambiente

Nella sezione **"Environment"** del servizio, aggiungi:

```
TELEGRAM_TOKEN=xxxxx:xxxxx-xxxxx
API_KEY=xxxxx
CHAT_ID=123456789
```

**Come ottenere i valori:**
- `TELEGRAM_TOKEN`: Crea un bot con [@BotFather](https://t.me/BotFather), comando `/newbot`, copia il token
- `API_KEY`: Registrati su [API-Football](https://www.api-football.com/), vai in Dashboard → API Key
- `CHAT_ID`: 
  - Per canale: aggiungi [@userinfobot](https://t.me/userinfobot) al canale, invia un messaggio, il bot risponderà con l'ID
  - Per chat privata: scrivi a [@userinfobot](https://t.me/userinfobot), ti dirà il tuo ID

### 4. Deploy

1. Clicca **"Create Web Service"**
2. Attendi il build (circa 2-3 minuti)
3. Verifica i log: dovresti vedere "HTTP server in ascolto su 0.0.0.0:XXXX"
4. Il bot è attivo! 🎉

### 5. Verifica Funzionamento

- Invia `/ping` al bot → dovrebbe rispondere "pong"
- Invia `/status` → mostra stato del bot
- Invia `/live` → mostra partite live analizzate

## 🖥️ Setup Locale (Sviluppo/Test)

### 1. Clonare Repository

```bash
git clone https://github.com/AndreaTrapani2004/QrGolBot
cd QrGolBot
```

### 2. Creare Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate  # Su Windows: .venv\Scripts\activate
```

### 3. Installare Dipendenze

```bash
pip install -r requirements.txt
```

### 4. Configurare Variabili d'Ambiente

**Linux/macOS:**
```bash
export TELEGRAM_TOKEN="xxxxx:xxxxx-xxxxx"
export API_KEY="xxxxx"
export CHAT_ID="123456789"
```

**Windows (PowerShell):**
```powershell
$env:TELEGRAM_TOKEN="xxxxx:xxxxx-xxxxx"
$env:API_KEY="xxxxx"
$env:CHAT_ID="123456789"
```

**Windows (CMD):**
```cmd
set TELEGRAM_TOKEN=xxxxx:xxxxx-xxxxx
set API_KEY=xxxxx
set CHAT_ID=123456789
```

### 5. Eseguire il Bot

```bash
python live_goals_bot.py
```

## 🔧 Comandi Disponibili

| Comando | Descrizione |
|---------|-------------|
| `/ping` | Verifica se il bot è attivo |
| `/help` | Guida dettagliata |
| `/status` | Stato ultimo/prossimo check, errori, notifiche oggi |
| `/stats` | Notifiche per giorno (ultimi 7) |
| `/live` | Elenco partite live analizzate (una chiamata) |
| `/force_check` | Esegue subito un controllo |
| `/set_interval <minuti>` | Imposta intervallo polling (minimo da quota) |
| `/quota` | Mostra quota 24h, intervallo minimo, chiamate recenti |
| `/set_quota <max_24h> [stima_per_check]` | Configura quota |
| `/see_all_request` | Ultime richieste API e notifiche |

## 📊 Logica del Bot

Il bot monitora partite live e invia notifiche quando:
1. Il punteggio è **1-1**
2. I due gol sono stati segnati nella **stessa metà tempo** (1H o 2H)
3. I due gol sono stati segnati da **squadre opposte**
4. I due gol sono stati segnati entro **10 minuti** l'uno dall'altro
5. Il primo gol era **1-0** o **0-1**

**Esempio:**
- Juventus - Inter (Serie A - Italia)
- 0-1 al 30' (Inter segna)
- 1-1 al 37' (Juventus segna)
- ✅ Notifica inviata (stessa metà, squadre opposte, 7 minuti di differenza)

## 🔄 Migrazione Futura a python-telegram-bot v20+

### Perché Migrare?

- `python-telegram-bot==13.15` è deprecato e non più mantenuto
- v20+ è moderno, async, e attivamente sviluppato
- Migliori performance e funzionalità

### Passi per Migrazione

#### 1. Aggiornare `requirements.txt`

```txt
python-telegram-bot>=20.0
requests
```

#### 2. Refactor del Codice

**Prima (v13 - sincrono):**
```python
from telegram import Bot
from telegram.ext import Updater, CommandHandler

bot = Bot(token=TELEGRAM_TOKEN)
updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
```

**Dopo (v20 - async):**
```python
from telegram import Bot
from telegram.ext import Application, CommandHandler

application = Application.builder().token(TELEGRAM_TOKEN).build()
```

#### 3. Convertire Funzioni a Async

**Prima:**
```python
def cmd_status(update, context):
    update.effective_message.reply_text("Status...")
```

**Dopo:**
```python
async def cmd_status(update, context):
    await update.effective_message.reply_text("Status...")
```

#### 4. Aggiornare Loop Principale

**Prima:**
```python
def main():
    while True:
        process_matches()
        time.sleep(POLL_INTERVAL)
```

**Dopo:**
```python
async def main():
    while True:
        await process_matches()
        await asyncio.sleep(POLL_INTERVAL)
```

#### 5. Aggiornare HTTP Server

**Prima:**
```python
from http.server import HTTPServer, BaseHTTPRequestHandler
```

**Dopo:**
```python
from aiohttp import web

async def health_handler(request):
    return web.Response(text="OK")

app = web.Application()
app.router.add_get("/", health_handler)
```

### Esempio Completo v20+

```python
import asyncio
from telegram.ext import Application, CommandHandler

async def cmd_ping(update, context):
    await update.effective_message.reply_text("pong")

async def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("ping", cmd_ping))
    
    # Avvia polling
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Loop principale
    while True:
        await process_matches()
        await asyncio.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
```

### Note Importanti

- La migrazione richiede refactor completo del codice
- Testare accuratamente prima di deployare in produzione
- Considera di creare un branch separato per la migrazione

## 🐛 Troubleshooting

### Errore: "Conflict: terminated by other getUpdates request"

**Causa:** Più istanze del bot stanno usando lo stesso token.

**Soluzione:**
1. Verifica che non ci siano altri servizi Render attivi per lo stesso bot
2. Chiudi eventuali script locali in esecuzione
3. Se necessario, rigenera il token su BotFather (`/revoke`)

### Errore: "ModuleNotFoundError: No module named 'imghdr'"

**Causa:** Python 3.13 ha rimosso il modulo `imghdr`.

**Soluzione:** Il file `imghdr.py` è già incluso nel repository come shim. Se il problema persiste, verifica che il file sia presente.

### Errore: "Quota giornaliera quasi raggiunta"

**Causa:** Hai raggiunto il limite di 100 chiamate/giorno (piano free API-Football).

**Soluzione:**
- Usa `/set_quota 100 1` per ottimizzare
- Aumenta l'intervallo con `/set_interval 15` (o più)
- Considera di passare a un piano a pagamento API-Football

### Bot Non Risponde ai Comandi

**Causa:** Il bot potrebbe non essere amministratore del canale o il servizio è spento.

**Soluzione:**
1. Verifica che il bot sia amministratore del canale (per comandi nei canali)
2. Controlla i log su Render
3. Verifica che le variabili d'ambiente siano corrette

### Render Si Addormenta

**Causa:** Render free tier spegne i servizi dopo inattività.

**Soluzione:** Il bot include già un HTTP server che mantiene il servizio attivo. Se il problema persiste, verifica che il servizio sia configurato come "Web Service" e non "Background Worker".

## 📝 File Struttura

```
QrGolBot/
├── live_goals_bot.py      # Codice principale del bot
├── requirements.txt        # Dipendenze Python
├── Procfile              # Configurazione Render (opzionale)
├── runtime.txt           # Versione Python (opzionale)
├── imghdr.py             # Shim per Python 3.13
├── sent_matches.json     # Cache partite già notificate (generato)
└── README.md             # Questa guida
```

## 🔐 Sicurezza

- **Non committare** file con token/API key
- Usa sempre variabili d'ambiente per segreti
- Aggiungi `sent_matches.json` a `.gitignore` se necessario
- Rigenera token se esposto accidentalmente

## 📚 Risorse Utili

- [python-telegram-bot Documentation](https://python-telegram-bot.readthedocs.io/)
- [API-Football Documentation](https://www.api-football.com/documentation-v3)
- [Render Documentation](https://render.com/docs)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## 📄 Licenza

Questo progetto è rilasciato sotto licenza MIT. Vedi file `LICENSE` per dettagli.

## 🤝 Contributi

Contributi sono benvenuti! Apri una issue o una pull request.

---

**Sviluppato con ❤️ per monitorare partite live e notificare pattern 1-1 interessanti.**

