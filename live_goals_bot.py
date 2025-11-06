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
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------- CONFIGURAZIONE ----------
import os

API_KEY = os.getenv("API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))       # Inserisci qui il chat_id del canale
POLL_INTERVAL = 600               # 10 minuti = 600 secondi
# Intervallo modificabile a runtime tramite /set_interval
poll_interval_seconds = POLL_INTERVAL

# Log in-memory per comandi
api_call_log = []  # [{time, endpoint, params, ok}]
notifications_log = []  # [{time, home, away, league, country, first_score, first_min, second_score, second_min}]

# Stato runtime per /status e /stats
last_check_started_at = None  # ISO string
last_check_finished_at = None  # ISO string
last_check_error = None  # Optional str

from collections import defaultdict
daily_notification_count = defaultdict(int)  # key: YYYY-MM-DD, value: count

import threading
force_check_lock = threading.Lock()
force_check_requested = False

# ---------- QUOTA / RATE LIMIT ----------
# Numero massimo di chiamate API consentite nelle ultime 24 ore (configurabile)
max_calls_per_day = 100
# Stima di quante chiamate consuma 1 "check" (fixtures + events, ecc.)
calls_per_check_estimate = 1

def _utc_now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _calls_last_24h():
    if not api_call_log:
        return 0
    now = datetime.utcnow()
    cutoff = now - timedelta(days=1)
    count = 0
    for c in api_call_log:
        try:
            t = datetime.strptime(c["time"], "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            # fallback: try without Z if needed
            try:
                t = datetime.fromisoformat(c["time"].replace("Z", ""))
            except Exception:
                continue
        if t >= cutoff:
            count += 1
    return count

def _recompute_min_interval_from_quota():
    # Intervallo minimo per non sforare la quota, assumendo la stima per check
    # 86400 sec/giorno * calls_per_check_estimate / max_calls_per_day
    if max_calls_per_day <= 0 or calls_per_check_estimate <= 0:
        return 86400  # fallback 24h
    seconds = int((86400 * calls_per_check_estimate) / max_calls_per_day)
    return max(1, seconds)

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
    endpoint = f"{BASE_URL}/fixtures"
    r = requests.get(endpoint, headers=headers, params=params, timeout=15)
    try:
        return r.json().get("response", [])
    finally:
        api_call_log.append({
            "time": _utc_now_iso(),
            "endpoint": endpoint,
            "params": params,
            "ok": r.ok,
        })

def get_fixture_events(fixture_id):
    headers = {"x-apisports-key": API_KEY}
    params = {"fixture": fixture_id}
    endpoint = f"{BASE_URL}/fixtures/events"
    r = requests.get(endpoint, headers=headers, params=params, timeout=15)
    try:
        return r.json().get("response", [])
    finally:
        api_call_log.append({
            "time": _utc_now_iso(),
            "endpoint": endpoint,
            "params": params,
            "ok": r.ok,
        })

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
    notifications_log.append({
        "time": _utc_now_iso(),
        "home": home,
        "away": away,
        "league": league,
        "country": country,
        "first_score": first_score,
        "first_min": first_min,
        "second_score": second_score,
        "second_min": second_min,
    })
    # Aggiorna contatore giornaliero
    day_key = datetime.utcnow().strftime("%Y-%m-%d")
    daily_notification_count[day_key] += 1


# ---------- COMANDI TELEGRAM ----------
def cmd_see_all_request(update, context):  # type: ignore[unused-argument]
    # Mostra gli ultimi elementi per non superare i limiti di Telegram
    last_calls = api_call_log[-20:]
    last_notif = notifications_log[-10:]

    lines = []
    lines.append("Richieste API (ultime 20):")
    for c in last_calls:
        status = "ok" if c.get("ok") else "fail"
        lines.append(f"- {c['time']} {c['endpoint']} {c['params']} [{status}]")

    lines.append("")
    lines.append("Notifiche inviate (ultime 10):")
    for n in last_notif:
        lines.append(
            f"- {n['time']} {n['home']} - {n['away']} ({n['league']} - {n['country']}) "
            f"{n['first_score']} ; {n['first_min']}'  ->  {n['second_score']} ; {n['second_min']}'"
        )

    text = "\n".join(lines) or "Nessun dato ancora."
    try:
        update.effective_message.reply_text(text[:4000])
    except Exception:
        # Se supera i limiti, invia un riassunto minimo
        update.effective_message.reply_text("Troppe righe; riduco l'output. Richieste: " + str(len(last_calls)) + ", Notifiche: " + str(len(last_notif)))

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


def run_check_once():
    global last_check_started_at, last_check_finished_at, last_check_error
    last_check_error = None
    last_check_started_at = _utc_now_iso()
    # Se la quota è satura nelle ultime 24h, salta il check
    try:
        recent = _calls_last_24h()
        if recent >= max_calls_per_day - calls_per_check_estimate:
            # Salta per non superare la quota
            last_check_error = "Quota giornaliera quasi raggiunta: check saltato"
            return
    except Exception:
        pass
    try:
        process_matches()
    except Exception as e:
        last_check_error = str(e)
        raise
    finally:
        last_check_finished_at = _utc_now_iso()

def main():
    global poll_interval_seconds
    while True:
        try:
            # Se richiesto un controllo immediato da /force_check
            global force_check_requested
            with force_check_lock:
                do_force = force_check_requested
                force_check_requested = False
            if do_force:
                run_check_once()
            else:
                run_check_once()
        except Exception as e:
            print("Errore:", e)
        # Calcola la soglia minima dal quota e applica al poll interval
        min_from_quota = _recompute_min_interval_from_quota()
        if poll_interval_seconds < min_from_quota:
            poll_interval_seconds = min_from_quota

        # Attendi l'intervallo corrente (può essere aggiornato da /set_interval o da quota)
        sleep_left = poll_interval_seconds
        # Spezzetta il sleep per poter reagire prima a /force_check
        step = 1
        while sleep_left > 0:
            time.sleep(min(step, sleep_left))
            sleep_left -= step
            with force_check_lock:
                if force_check_requested:
                    break

if __name__ == "__main__":
    # Configura Updater per comandi Telegram
    updater = None
    try:
        updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("see_all_request", cmd_see_all_request))
        # Nuovi comandi
        dp.add_handler(CommandHandler("ping", lambda u, c: u.effective_message.reply_text("pong")))

        def cmd_status(update, context):  # type: ignore[unused-argument]
            interval_min = int(poll_interval_seconds // 60)
            lines = []
            lines.append(f"Intervallo: {interval_min} min")
            lines.append(f"Quota max 24h: {max_calls_per_day} (stima/check: {calls_per_check_estimate})")
            lines.append(f"Chiamate ultime 24h: {_calls_last_24h()}")
            lines.append(f"Ultimo check start: {last_check_started_at}")
            lines.append(f"Ultimo check end: {last_check_finished_at}")
            if last_check_finished_at:
                try:
                    end_dt = datetime.strptime(last_check_finished_at, "%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    end_dt = None
                if end_dt is not None:
                    next_eta = end_dt + timedelta(seconds=poll_interval_seconds)
                    lines.append("Prossimo check (stimato UTC): " + next_eta.strftime("%Y-%m-%dT%H:%M:%SZ"))
            if last_check_error:
                lines.append("Ultimo errore: " + last_check_error)
            day_key = datetime.utcnow().strftime("%Y-%m-%d")
            lines.append(f"Notifiche oggi: {daily_notification_count.get(day_key, 0)}")
            update.effective_message.reply_text("\n".join(lines))

        def cmd_stats(update, context):  # type: ignore[unused-argument]
            # Mostra ultimi 7 giorni
            today = datetime.utcnow().date()
            lines = ["Notifiche per giorno (ultimi 7 giorni):"]
            for i in range(7):
                d = today - timedelta(days=i)
                k = d.strftime("%Y-%m-%d")
                lines.append(f"- {k}: {daily_notification_count.get(k, 0)}")
            update.effective_message.reply_text("\n".join(lines))

        def cmd_quota(update, context):  # type: ignore[unused-argument]
            min_sec = _recompute_min_interval_from_quota()
            update.effective_message.reply_text(
                f"Quota max 24h: {max_calls_per_day}\n"
                f"Stima chiamate per check: {calls_per_check_estimate}\n"
                f"Intervallo minimo da quota: {int(min_sec//60)} min\n"
                f"Chiamate ultime 24h: {_calls_last_24h()}"
            )

        def cmd_force_check(update, context):  # type: ignore[unused-argument]
            # Esegue un controllo immediato in un thread per non bloccare il dispatcher
            def _run():
                try:
                    run_check_once()
                    update.effective_message.reply_text("Controllo eseguito.")
                except Exception as e:
                    update.effective_message.reply_text(f"Errore: {e}")
            Thread(target=_run, daemon=True).start()

        def cmd_set_interval(update, context):  # type: ignore[unused-argument]
            global poll_interval_seconds
            try:
                if not context.args:
                    update.effective_message.reply_text("Uso: /set_interval <minuti>")
                    return
                minutes = int(context.args[0])
                if minutes < 1 or minutes > 1440:
                    update.effective_message.reply_text("Valore non valido (1-1440 minuti)")
                    return
                poll_interval_seconds = minutes * 60
                update.effective_message.reply_text(f"Intervallo aggiornato a {minutes} minuti")
            except Exception:
                update.effective_message.reply_text("Uso: /set_interval <minuti>")

        def cmd_set_quota(update, context):  # type: ignore[unused-argument]
            global max_calls_per_day, calls_per_check_estimate, poll_interval_seconds
            try:
                if not context.args:
                    update.effective_message.reply_text("Uso: /set_quota <max_24h> [stima_per_check]")
                    return
                new_max = int(context.args[0])
                if new_max < 1 or new_max > 10000:
                    update.effective_message.reply_text("Valore non valido (1-10000)")
                    return
                max_calls_per_day = new_max
                if len(context.args) >= 2:
                    est = int(context.args[1])
                    if est < 1 or est > 100:
                        update.effective_message.reply_text("Stima per check non valida (1-100)")
                        return
                    calls_per_check_estimate = est
                # Applica nuovo minimo
                min_from_quota = _recompute_min_interval_from_quota()
                if poll_interval_seconds < min_from_quota:
                    poll_interval_seconds = min_from_quota
                update.effective_message.reply_text(
                    f"Quota aggiornata. Max 24h: {max_calls_per_day}, stima/check: {calls_per_check_estimate}. "
                    f"Intervallo minimo: {int(min_from_quota//60)} min"
                )
            except Exception:
                update.effective_message.reply_text("Uso: /set_quota <max_24h> [stima_per_check]")

        def cmd_help(update, context):  # type: ignore[unused-argument]
            update.effective_message.reply_text(
                "Comandi disponibili:\n"
                "/ping - verifica se il bot è attivo\n"
                "/status - stato ultimo/ prossimo controllo e conteggi odierni\n"
                "/stats - notifiche per giorno (ultimi 7)\n"
                "/force_check - esegue subito un controllo\n"
                "/set_interval <minuti> - imposta intervallo di polling\n"
                "/quota - mostra quota e intervallo minimo\n"
                "/set_quota <max_24h> [stima_per_check] - configura quota\n"
                "/see_all_request - ultime richieste API e notifiche"
            )

        dp.add_handler(CommandHandler("status", cmd_status))
        dp.add_handler(CommandHandler("stats", cmd_stats))
        dp.add_handler(CommandHandler("force_check", cmd_force_check))
        dp.add_handler(CommandHandler("set_interval", cmd_set_interval, pass_args=True))
        dp.add_handler(CommandHandler("quota", cmd_quota))
        dp.add_handler(CommandHandler("set_quota", cmd_set_quota, pass_args=True))
        dp.add_handler(CommandHandler("help", cmd_help))

        # Gestione comandi anche nei CANALI (channel_post). Nei canali i comandi arrivano come channel_post.
        def handle_channel_command(update, context):  # type: ignore[unused-argument]
            post = getattr(update, "channel_post", None)
            if not post:
                return
            text = post.text or post.caption or ""
            if not text.startswith("/"):
                return
            parts = text.split()
            raw_cmd = parts[0]
            # rimuove eventuale @botusername
            cmd = raw_cmd.split("@")[0].lstrip("/")
            args = parts[1:]

            # Mappa ai callback già definiti
            if cmd == "ping":
                post.reply_text("pong")
            elif cmd == "see_all_request":
                cmd_see_all_request(update, context)
            elif cmd == "status":
                cmd_status(update, context)
            elif cmd == "stats":
                cmd_stats(update, context)
            elif cmd == "force_check":
                cmd_force_check(update, context)
            elif cmd == "set_interval":
                context.args = args  # passa args
                cmd_set_interval(update, context)
            elif cmd == "quota":
                cmd_quota(update, context)
            elif cmd == "set_quota":
                context.args = args
                cmd_set_quota(update, context)
            elif cmd == "help":
                cmd_help(update, context)

        # In PTB 13.x non esiste ChannelPostHandler: usa MessageHandler con filtro channel_posts
        dp.add_handler(MessageHandler(Filters.update.channel_posts, handle_channel_command))
        updater.start_polling()
        print("Updater Telegram avviato per comandi (/see_all_request)")
    except Exception as e:
        print("Updater non avviato:", e)

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