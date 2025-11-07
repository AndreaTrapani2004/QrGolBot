# Setup Bot su Render (Gratuito, senza carta di credito)

## ⚠️ IMPORTANTE: Usa "Web Service" NON "Background Worker"

Render **Web Service** ha un free tier gratuito, mentre **Background Worker** è a pagamento ($7/mese minimo).

## Step-by-Step:

### 1. Prepara il codice su GitHub
- Assicurati che il codice sia su GitHub
- Il bot ha già un HTTP server integrato per il keep-alive

### 2. Vai su Render.com
- Registrati con GitHub (gratuito, senza carta di credito)
- Vai su [dashboard.render.com](https://dashboard.render.com)

### 3. Crea un Web Service (NON Background Worker!)
- Clicca "New +" → **"Web Service"** (non Background Worker!)
- Connetti il tuo repository GitHub
- Seleziona il repository

### 4. Configurazione:
- **Name**: `qrgolbot` (o qualsiasi nome)
- **Region**: `Frankfurt` (o più vicino a te)
- **Branch**: `main` (o il tuo branch)
- **Root Directory**: (lascia vuoto)
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python live_goals_bot.py`

### 5. Variabili d'ambiente:
Aggiungi queste variabili:
- `TELEGRAM_TOKEN` = `8529606536:AAEMv4BQPEvnvHE0xubu6QSm2587q0_qtUE`
- `CHAT_ID` = `-1002523452868`
- `PORT` = (Render lo assegna automaticamente, non serve impostarlo)

### 6. Deploy!
- Clicca "Create Web Service"
- Render inizierà il deploy automaticamente
- Attendi che finisca (2-3 minuti)

### 7. Mantieni il bot sveglio 24/7 (gratuito)

Il bot si addormenta dopo 15 minuti di inattività. Per mantenerlo sveglio:

**Opzione A: UptimeRobot (gratuito, senza carta)**
1. Vai su [uptimerobot.com](https://uptimerobot.com)
2. Crea account gratuito
3. Aggiungi monitor HTTP(S):
   - URL: `https://qrgolbot.onrender.com/health` (sostituisci con il tuo URL Render)
   - Interval: `5 minutes`
4. UptimeRobot farà ping ogni 5 minuti per mantenere il bot sveglio

**Opzione B: Cron-job.org (gratuito)**
1. Vai su [cron-job.org](https://cron-job.org)
2. Crea account gratuito
3. Crea nuovo cron job:
   - URL: `https://qrgolbot.onrender.com/health`
   - Schedule: `*/5 * * * *` (ogni 5 minuti)

### 8. Verifica che funzioni
- Vai su `https://qrgolbot.onrender.com/health` (sostituisci con il tuo URL)
- Dovresti vedere "OK"
- Se vedi "OK", il bot è attivo!

## ⚠️ Limitazioni Free Tier Render:
- Il bot si addormenta dopo 15 minuti di inattività
- Tempo di avvio più lento dopo il risveglio (~30 secondi)
- Con UptimeRobot/Cron-job, il bot rimane sveglio 24/7

## ✅ Vantaggi:
- Completamente gratuito
- Non richiede carta di credito
- Funziona 24/7 con keep-alive
- Setup semplice

## 🔧 Troubleshooting:
- Se il bot non si avvia, controlla i log su Render Dashboard
- Se il bot si addormenta, verifica che UptimeRobot stia facendo ping
- Se vedi errori, controlla che le variabili d'ambiente siano corrette

