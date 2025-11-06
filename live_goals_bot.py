"""
live_goals_bot.py
Bot Telegram per notifiche 1-1 live in tutti i campionati
Chiamate API ogni 5 minuti per risparmiare richieste (piano free API-Football)
"""

import requests
import time
import json
from datetime import datetime, timedelta
from telegram import Bot
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------- CONFIGURAZIONE ----------
import os

API_KEY = os.getenv("API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))       # Inserisci qui il chat_id del canale
POLL_INTERVAL = 300               # 5 minuti = 300 secondi

# Base URL API-Football
BASE_URL = "https://v3.football.api-sports.io"

# Bot Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# File per salvare le partite già notificate
SENT_FILE = "sent_matches.json"

# ---------- FUNZIONI UTILI ----------
def load_sent_matches():
    try:
        with open(SENT_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_sent_matches(sent_set):
    with open(SENT_FILE, "w") as f:
        json.dump(list(sent_set), f)

def get_live_fixtures():
    headers = {"x-apisports-key": API_KEY}
    params = {"live": "all"}
    r = requests.get(f"{BASE_URL}/fixtures", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("response", [])

def get_fixture_events(fixture_id):
    headers = {"x-apisports-key": API_KEY}
    params = {"fixture": fixture_id}
    r = requests.get(f"{BASE_URL}/fixtures/events", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("response", [])

def parse_minute(ev):
    try:
        return int(ev.get("time", {}).get("elapsed"))
    except:
        return None

def send_message(home, away, league, country, first_score, first_min, second_score, second_min):
    text = f"{home} - {away} ({league} - {country})\n" \
           f"{first_score} ; {first_min}'\n" \
           f"{second_score} ; {second_min}'"
    bot.send_message(chat_id=CHAT_ID, text=text)

# ---------- LOGICA PRINCIPALE ----------
def process_matches():
    sent_matches = load_sent_matches()
    fixtures = get_live_fixtures()

    for f in fixtures:
        fid = f["fixture"]["id"]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        league = f["league"]["name"]
        country = f["league"]["country"]

        score_home = f["goals"]["home"]
        score_away = f["goals"]["away"]

        if score_home != 1 or score_away != 1:
            continue  # ci interessano solo 1-1

        if fid in sent_matches:
            continue  # già notificata

        # Recupera eventi per la partita
        events = get_fixture_events(fid)
        goal_events = [e for e in events if e.get("type") == "Goal"]
        if len(goal_events) < 2:
            continue  # non ci sono 2 gol, skip

        # Prendi gli ultimi due gol
        g1 = goal_events[-2]
        g2 = goal_events[-1]
        m1 = parse_minute(g1)
        m2 = parse_minute(g2)
        p1 = g1.get("time", {}).get("period")
        p2 = g2.get("time", {}).get("period")
        t1 = "home" if g1.get("team", {}).get("id") == f["teams"]["home"]["id"] else "away"
        t2 = "home" if g2.get("team", {}).get("team", {}).get("id") == f["teams"]["home"]["id"] else "away"

        # Condizioni: stessa metà e differenza <= 10 minuti, squadre opposte
        if p1 == p2 and t1 != t2 and m1 is not None and m2 is not None and (m2 - m1) <= 10 and p1 == "1H":
            first_score = "1-0" if t1 == "home" else "0-1"
            second_score = "1-1"
            send_message(home, away, league, country, first_score, m1, second_score, m2)
            sent_matches.add(fid)

    save_sent_matches(sent_matches)

def main():
    while True:
        try:
            process_matches()
        except Exception as e:
            print("Errore:", e)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    # Se è presente la variabile PORT (es. Render Web Service), esponi una porta HTTP
    port = os.getenv("PORT")

    if port:
        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # type: ignore[override]
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"OK")

            def log_message(self, format, *args):  # noqa: A003 - silence default logging
                return

        # Avvia il loop di polling in background
        t = Thread(target=main, daemon=True)
        t.start()

        # Avvia un piccolo HTTP server per soddisfare Render (porta obbligatoria)
        server = HTTPServer(("0.0.0.0", int(port)), HealthHandler)
        try:
            print(f"HTTP server in ascolto su 0.0.0.0:{port}")
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    else:
        # Ambiente locale / worker: esegui solo il polling
        main()