# Guida Deploy QrGolBot - Esecuzione 24/7

## 🖥️ Esecuzione Locale (Macchina Fisica)

### Setup

1. **Installa ChromeDriver:**
   ```bash
   # macOS
   brew install chromedriver
   
   # Linux
   sudo apt-get install chromium-chromedriver
   ```

2. **Installa dipendenze:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configura variabili d'ambiente:**
   ```bash
   export TELEGRAM_TOKEN="xxxxx:xxxxx-xxxxx"
   export CHAT_ID="123456789"
   ```

4. **Esegui il bot:**
   ```bash
   python live_goals_bot.py
   ```

Il bot gira all'infinito finché non lo interrompi (Ctrl+C).

### Esecuzione in Background (Linux/macOS)

**Opzione 1: nohup**
```bash
nohup python live_goals_bot.py > bot.log 2>&1 &
```

**Opzione 2: systemd (Linux)**
Crea `/etc/systemd/system/qrgolbot.service`:
```ini
[Unit]
Description=QrGolBot
After=network.target

[Service]
Type=simple
User=tuo_utente
WorkingDirectory=/path/to/bot
Environment=TELEGRAM_TOKEN=xxxxx
Environment=CHAT_ID=123456789
ExecStart=/path/to/bot/.venv/bin/python /path/to/bot/live_goals_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Poi:
```bash
sudo systemctl daemon-reload
sudo systemctl enable qrgolbot
sudo systemctl start qrgolbot
```

## ☁️ Hosting Gratuito 24/7

### 1. **Railway** ⭐⭐⭐ (Raccomandato)

**Pro**: Gratuito (500 ore/mese), uptime 100%, setup semplice
**Contro**: Limite mensile (~16 ore/giorno), per 24/7 serve upgrade ($5/mese)

**Setup:**
1. Vai su [railway.app](https://railway.app) e registrati
2. Clicca "New Project" → "Deploy from GitHub repo"
3. Seleziona il tuo repository
4. Aggiungi variabili d'ambiente:
   - `TELEGRAM_TOKEN`
   - `CHAT_ID`
5. Railway rileva automaticamente Python e deploya!

**Nota**: Per 24/7 continuo, considera upgrade ($5/mese) o usa Oracle Cloud.

---

### 2. **Oracle Cloud Free Tier** ⭐⭐⭐ (Migliore per 24/7)

**Pro**: VPS gratuito permanente, 24/7 garantito, controllo completo
**Contro**: Setup più complesso, richiede carta di credito (non addebitata)

**Setup:**
1. Registrati su [Oracle Cloud](https://www.oracle.com/cloud/free/)
2. Crea VM (Always Free): Ubuntu 22.04
3. Connetti via SSH
4. Installa dipendenze:
   ```bash
   sudo apt-get update
   sudo apt-get install python3-pip chromium-chromedriver
   pip3 install -r requirements.txt
   ```
5. Configura systemd (vedi sopra)
6. Il bot gira 24/7 gratuitamente!

---

### 3. **Render** ⭐⭐

**Pro**: Gratuito, semplice
**Contro**: Free tier si addormenta dopo inattività

**Setup:**
1. Vai su [Render Dashboard](https://dashboard.render.com)
2. Clicca "New +" → "Background Worker" (non Web Service!)
3. Connetti repository GitHub
4. Configurazione:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python live_goals_bot.py`
5. Aggiungi variabili d'ambiente
6. Deploy!

**Nota**: Il codice attuale non ha HTTP server, quindi usa "Background Worker" invece di "Web Service".

---

### 4. **Fly.io** ⭐⭐

**Pro**: Free tier generoso (3 VM gratuite), uptime 100%
**Contro**: Setup più complesso, richiede CLI

**Setup:**
1. Installa Fly CLI: `curl -L https://fly.io/install.sh | sh`
2. Login: `fly auth login`
3. Crea `fly.toml`:
   ```toml
   app = "qrgolbot"
   primary_region = "iad"
   
   [build]
   
   [env]
     TELEGRAM_TOKEN = "xxxxx"
     CHAT_ID = "123456789"
   
   [[services]]
     internal_port = 8080
     protocol = "tcp"
   ```
4. Deploy: `fly launch` e poi `fly deploy`

---

### 5. **PythonAnywhere** ⭐

**Pro**: Gratuito, specifico per Python
**Contro**: Free tier limitato (1 task sempre-on), si addormenta

**Setup:**
1. Registrati su [PythonAnywhere](https://www.pythonanywhere.com)
2. Vai su "Tasks" → Crea nuovo task sempre-on
3. Upload codice via Git o file manager
4. Configura variabili d'ambiente
5. Esegui: `python3 live_goals_bot.py`

---

## 📊 Confronto Soluzioni

| Soluzione | Uptime | Complessità | Costo | Migliore Per |
|-----------|--------|------------|-------|--------------|
| **Railway** | 100% | ⭐ Facile | Gratuito (500h/mese) | Setup rapido |
| **Oracle Cloud** | 100% | ⭐⭐⭐ Complessa | Gratuito (sempre) | 24/7 permanente |
| **Render** | ~95% | ⭐ Facile | Gratuito | Semplice |
| **Fly.io** | 100% | ⭐⭐ Media | Gratuito (3 VM) | Performance |
| **PythonAnywhere** | ~95% | ⭐ Facile | Gratuito | Python-specific |
| **Macchina Fisica** | 100% | ⭐⭐ Media | Gratuito (hardware) | Controllo totale |

## 🎯 Raccomandazione

**Per 24/7 gratuito permanente:**
1. **Oracle Cloud** - VPS gratuito per sempre, controllo completo
2. **Railway** - Facile, ma per 24/7 serve upgrade ($5/mese)

**Per setup rapido:**
1. **Railway** - Più semplice, funziona subito
2. **Render** - Background Worker, semplice

## ⚙️ Configurazione Variabili d'Ambiente

Tutte le piattaforme richiedono queste variabili:

```
TELEGRAM_TOKEN=8529606536:AAEMv4BQPEvnvHE0xubu6QSm2587q0_qtUE
CHAT_ID=-1002523452868
```

**Come ottenere:**
- `TELEGRAM_TOKEN`: Crea bot con [@BotFather](https://t.me/BotFather)
- `CHAT_ID`: Scrivi a [@userinfobot](https://t.me/userinfobot) o aggiungilo al canale

## 🔧 Note Importanti

1. **ChromeDriver**: Richiesto per Selenium. Su hosting cloud, potrebbe essere necessario installarlo o usare un'immagine Docker con Chrome preinstallato.

2. **Memoria**: Il bot usa Selenium che può consumare memoria. Assicurati che il piano free abbia abbastanza RAM.

3. **Timeout**: Su alcuni hosting free, i processi possono essere terminati dopo un certo tempo. Usa `restart=always` in systemd o configurazione equivalente.

4. **Log**: Monitora i log per verificare che il bot funzioni correttamente:
   - Render: Dashboard → Logs
   - Railway: Dashboard → Deployments → View Logs
   - Oracle Cloud: `sudo journalctl -u qrgolbot -f`

## 🚀 Quick Start: Railway (Più Semplice)

1. Vai su [railway.app](https://railway.app)
2. "New Project" → "Deploy from GitHub repo"
3. Seleziona repository
4. Aggiungi variabili d'ambiente
5. Deploy automatico!

Il bot gira subito! 🎉

