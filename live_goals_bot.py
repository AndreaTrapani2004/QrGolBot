"""
live_goals_bot.py
Bot Telegram per notifiche 1-1 live in tutti i campionati
Monitora partite live tramite API SofaScore
Traccia partite in stato 1-0/0-1 e notifica quando diventano 1-1 entro 10 minuti
"""

import time
import json
import os
import re
import requests
from datetime import datetime, timedelta
from telegram import Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram.error import Conflict, NetworkError
from threading import Thread
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------- CONFIGURAZIONE ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
POLL_INTERVAL = 60  # 1 minuto = 60 secondi
SOFASCORE_API_URL = "https://api.sofascore.com/api/v1"

# Bot Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# File per salvare le partite attive in tracking
ACTIVE_MATCHES_FILE = "active_matches.json"
# File per salvare le partite già notificate (evita duplicati)
SENT_MATCHES_FILE = "sent_matches.json"


# ---------- FUNZIONI UTILI ----------
def load_active_matches():
    """Carica le partite attive in tracking (1-0/0-1) da file"""
    try:
        with open(ACTIVE_MATCHES_FILE, "r") as f:
            data = json.load(f)
            # Converti timestamp string in datetime
            for match_id, match_data in data.items():
                match_data["first_goal_time"] = datetime.fromisoformat(match_data["first_goal_time"])
            return data
    except Exception:
        return {}


def save_active_matches(active_matches):
    """Salva le partite attive in tracking su file"""
    # Converti datetime in string per JSON
    data = {}
    for match_id, match_data in active_matches.items():
        data[match_id] = match_data.copy()
        data[match_id]["first_goal_time"] = match_data["first_goal_time"].isoformat()
    
    with open(ACTIVE_MATCHES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_sent_matches():
    """Carica le partite già notificate da file"""
    try:
        with open(SENT_MATCHES_FILE, "r") as f:
            data = json.load(f)
            # Se è una lista (vecchio formato), converti in dict
            if isinstance(data, list):
                return {match_id: {} for match_id in data}
            return data
    except Exception:
        return {}


def save_sent_matches(sent_dict):
    """Salva le partite già notificate su file"""
    with open(SENT_MATCHES_FILE, "w") as f:
        json.dump(sent_dict, f, indent=2)


def get_match_id(home, away, league):
    """Genera un ID univoco per una partita"""
    return f"{home}_{away}_{league}".lower().replace(" ", "_")


def scrape_sofascore():
    """Ottiene tutte le partite live tramite API SofaScore"""
    try:
        # Header per sembrare un browser reale
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.sofascore.com/",
            "Origin": "https://www.sofascore.com"
        }
        
        # Endpoint per partite live
        url = f"{SOFASCORE_API_URL}/sport/football/events/live"
        
        print(f"Richiesta API SofaScore: {url}...")
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"⚠️ Errore API SofaScore: {response.status_code}")
            return []
        
        data = response.json()
        matches = []
        
        # Estrai partite dai dati JSON
        events = data.get("events", [])
        print(f"✅ Trovate {len(events)} partite live")
        
        for event in events:
            try:
                # Estrai informazioni partita
                tournament = event.get("tournament", {})
                league = tournament.get("name", "Unknown")
                country = tournament.get("category", {}).get("name", "Unknown")
                
                home_team = event.get("homeTeam", {})
                away_team = event.get("awayTeam", {})
                home = home_team.get("name", "Unknown")
                away = away_team.get("name", "Unknown")
                
                # Estrai punteggio (sono oggetti con 'current' o 'display')
                score_home_obj = event.get("homeScore", {})
                score_away_obj = event.get("awayScore", {})
                
                # Estrai valore numerico dal punteggio
                if isinstance(score_home_obj, dict):
                    score_home = score_home_obj.get("current", score_home_obj.get("display", 0))
                else:
                    score_home = score_home_obj if score_home_obj is not None else 0
                
                if isinstance(score_away_obj, dict):
                    score_away = score_away_obj.get("current", score_away_obj.get("display", 0))
                else:
                    score_away = score_away_obj if score_away_obj is not None else 0
                
                # NON filtrare 0-0 - includiamo tutte le partite
                
                # Estrai minuto e calcola attendibilità
                time_obj = event.get("time", {})
                status = event.get("status", {})
                minute = None
                reliability = 0  # Attendibilità 0-5
                
                if isinstance(time_obj, dict):
                    # Determina periodo (1st half o 2nd half)
                    status_desc = status.get("description", "").lower()
                    status_code = status.get("code")
                    is_first_half = "1st half" in status_desc or status_code == 6
                    is_second_half = "2nd half" in status_desc or status_code == 7
                    
                    # Calcola minuto corrente basato su currentPeriodStartTimestamp
                    if "currentPeriodStartTimestamp" in time_obj:
                        start_ts = time_obj.get("currentPeriodStartTimestamp")
                        if start_ts:
                            elapsed_seconds = datetime.now().timestamp() - start_ts
                            elapsed_minutes = int(elapsed_seconds / 60)
                            
                            if is_second_half:
                                # Secondo tempo: aggiungi 45 minuti
                                minute = 45 + max(0, elapsed_minutes)
                                reliability = 4  # Calcolo corretto con periodo
                            elif is_first_half:
                                # Primo tempo: minuto diretto
                                minute = max(0, elapsed_minutes)
                                reliability = 4  # Calcolo corretto con periodo
                            else:
                                # Periodo non determinato, usa solo elapsed
                                minute = max(0, elapsed_minutes)
                                reliability = 2  # Minuto calcolato ma senza periodo
                    
                    # Se non disponibile, prova a estrarre da status description
                    if minute is None:
                        desc = status.get("description", "")
                        if "1st half" in desc or "2nd half" in desc:
                            # Estrai numero se presente nella descrizione (es. "1st half 23'")
                            match = re.search(r'(\d+)\s*[\'"]', desc)
                            if match:
                                extracted_min = int(match.group(1))
                                if is_second_half and extracted_min < 45:
                                    # Se è secondo tempo ma il minuto è < 45, aggiungi 45
                                    minute = 45 + extracted_min
                                else:
                                    minute = extracted_min
                                reliability = 3  # Minuto estratto da descrizione
                elif isinstance(time_obj, (int, float)):
                    minute = int(time_obj)
                    reliability = 1  # Minuto diretto ma senza contesto
                
                # Estrai stato partita
                status = event.get("status", {})
                status_type = status.get("type", "")
                # NON filtrare partite non iniziate - includiamo tutte le partite
                
                # Determina metà tempo (1st half o 2nd half)
                period = None
                status_desc = status.get("description", "").lower()
                if "1st half" in status_desc or status.get("code") == 6:
                    period = 1  # Primo tempo
                elif "2nd half" in status_desc or status.get("code") == 7:
                    period = 2  # Secondo tempo
                elif minute is not None:
                    # Determina dalla base del minuto
                    if minute <= 45:
                        period = 1
                    else:
                        period = 2
                
                matches.append({
                    "home": home,
                    "away": away,
                    "score_home": score_home,
                    "score_away": score_away,
                    "league": league,
                    "country": country,
                    "minute": minute,
                    "period": period,  # 1 = primo tempo, 2 = secondo tempo
                    "reliability": reliability  # Attendibilità 0-5
                })
            except Exception as e:
                print(f"Errore nell'estrazione partita: {e}")
                continue
        
        print(f"✅ Estratte {len(matches)} partite da SofaScore (stato ≠ 0-0)")
        return matches
    
    except requests.exceptions.RequestException as e:
        print(f"Errore nella richiesta API SofaScore: {e}")
        return []
    except Exception as e:
        print(f"Errore nello scraping SofaScore: {e}")
        return []


def send_message(home, away, league, country, first_score, first_min, second_score, second_min, reliability=0):
    """Invia messaggio Telegram con i dettagli del pattern 1-1"""
    global total_notifications_sent
    
    # Emoji per attendibilità
    reliability_emoji = ["❌", "⚠️", "⚠️", "✅", "✅", "✅✅"]
    reliability_text = ["Nessun dato", "Basso", "Medio", "Buono", "Alto", "Massimo"]
    
    reliability_str = f"{reliability_emoji[min(reliability, 5)]} Attendibilità: {reliability}/5 ({reliability_text[min(reliability, 5)]})"
    
    text = f"{home} - {away} ({league} - {country})\n" \
           f"{first_score} ; {first_min}'\n" \
           f"{second_score} ; {second_min}'\n\n" \
           f"{reliability_str}"
    bot.send_message(chat_id=CHAT_ID, text=text)
    
    # Aggiorna statistiche
    total_notifications_sent += 1
    today = datetime.now().strftime("%Y-%m-%d")
    daily_notifications[today] += 1


def cleanup_expired_matches(active_matches, current_matches_dict):
    """Rimuove partite scadute (>10 minuti di gioco dal primo gol)"""
    expired = []
    
    for match_id, match_data in active_matches.items():
        first_goal_minute = match_data.get("first_goal_minute", 0)
        
        # Cerca la partita nelle partite live attuali per ottenere il minuto corrente
        if match_id in current_matches_dict:
            current_minute = current_matches_dict[match_id].get("minute")
            if current_minute is not None and current_minute > 0:
                # Calcola differenza in minuti di gioco
                elapsed_game_minutes = current_minute - first_goal_minute
                if elapsed_game_minutes > 10:
                    expired.append(match_id)
        else:
            # Se la partita non è più nelle partite live, rimuovila dopo un timeout
            # (fallback: usa tempo reale se non abbiamo minuto di gioco)
            first_goal_time = match_data.get("first_goal_time")
            if first_goal_time:
                now = datetime.now()
                elapsed = (now - first_goal_time).total_seconds() / 60
                if elapsed > 15:  # Timeout più lungo per sicurezza
                    expired.append(match_id)
    
    for match_id in expired:
        del active_matches[match_id]
        print(f"Partita scaduta rimossa dal tracking: {match_id}")
    
    return active_matches


# ---------- LOGICA PRINCIPALE ----------
def process_matches():
    """Processa tutte le partite live e gestisce il tracking 1-0/0-1 → 1-1"""
    active_matches = load_active_matches()
    sent_matches = load_sent_matches()  # Ora è un dict, non un set
    
    # Scraping partite live
    print("Scraping SofaScore...")
    live_matches = scrape_sofascore()
    print(f"Trovate {len(live_matches)} partite live")
    
    # Crea dizionario per lookup veloce delle partite live
    current_matches_dict = {}
    for match in live_matches:
        match_id = get_match_id(match["home"], match["away"], match["league"])
        current_matches_dict[match_id] = match
    
    # Rimuovi partite scadute (>10 minuti di gioco)
    active_matches = cleanup_expired_matches(active_matches, current_matches_dict)
    
    now = datetime.now()
    
    for match in live_matches:
        home = match["home"]
        away = match["away"]
        score_home = match["score_home"]
        score_away = match["score_away"]
        league = match["league"]
        country = match.get("country", "Unknown")
        minute = match.get("minute")
        
        match_id = get_match_id(home, away, league)
        
        # Se la partita è già stata notificata, salta
        if match_id in sent_matches:
            continue
        
        # CASO 1: Partita in stato 1-0 o 0-1 (non ancora tracciata)
        if (score_home == 1 and score_away == 0) or (score_home == 0 and score_away == 1):
            if match_id not in active_matches:
                # Nuova partita da tracciare
                first_score = "1-0" if score_home == 1 else "0-1"
                period = match.get("period")  # 1 = primo tempo, 2 = secondo tempo
                active_matches[match_id] = {
                    "home": home,
                    "away": away,
                    "league": league,
                    "country": country,
                    "first_goal_time": now,
                    "first_score": first_score,
                    "first_goal_minute": minute if minute else 0,
                    "first_goal_period": period,  # Salva metà tempo del primo gol
                    "first_goal_reliability": match.get("reliability", 0)  # Salva attendibilità del primo gol
                }
                print(f"Nuova partita tracciata: {home} - {away} ({first_score}) al minuto {minute if minute else 'N/A'}")
        
        # CASO 2: Partita già tracciata (1-0/0-1) che diventa 1-1
        elif score_home == 1 and score_away == 1:
            if match_id in active_matches:
                match_data = active_matches[match_id]
                first_score = match_data["first_score"]
                first_min = match_data.get("first_goal_minute", 0)
                first_period = match_data.get("first_goal_period")  # 1 = primo tempo, 2 = secondo tempo
                second_min = minute if minute is not None else 0
                second_period = match.get("period")  # Metà tempo corrente
                
                # VERIFICA: Entrambi i gol devono essere nella stessa metà tempo
                same_period = True
                if first_period is not None and second_period is not None:
                    same_period = (first_period == second_period)
                elif first_min > 0 and second_min > 0:
                    # Fallback: determina metà tempo dal minuto
                    first_is_first_half = (first_min <= 45)
                    second_is_first_half = (second_min <= 45)
                    same_period = (first_is_first_half == second_is_first_half)
                
                if not same_period:
                    # Gol in metà tempo diverse, non notificare
                    del active_matches[match_id]
                    print(f"Partita scartata (gol in metà tempo diverse): {home} - {away} ({first_score} al {first_min}' → 1-1 al {second_min}')")
                    continue
                
                # Calcola differenza in minuti di gioco
                if first_min > 0 and second_min > 0:
                    elapsed_game_minutes = second_min - first_min
                else:
                    # Fallback: usa tempo reale se minuti di gioco non disponibili
                    first_goal_time = match_data.get("first_goal_time")
                    if first_goal_time:
                        elapsed_game_minutes = (now - first_goal_time).total_seconds() / 60
                    else:
                        elapsed_game_minutes = 999  # Non valido, non notificare
                
                # Se è diventata 1-1 entro 10 minuti di gioco E stessa metà tempo, invia notifica
                if elapsed_game_minutes <= 10 and elapsed_game_minutes >= 0:
                    # Calcola attendibilità combinata (minimo tra i due)
                    first_reliability = match_data.get("first_goal_reliability", 0)
                    second_reliability = match.get("reliability", 0)
                    combined_reliability = min(first_reliability, second_reliability)
                    
                    send_message(home, away, league, country, first_score, first_min, "1-1", second_min, combined_reliability)
                    # Salva dettagli della partita notificata
                    sent_matches[match_id] = {
                        "home": home,
                        "away": away,
                        "league": league,
                        "country": country,
                        "first_score": first_score,
                        "first_minute": first_min,
                        "second_minute": second_min,
                        "reliability": combined_reliability,
                        "notified_at": now.isoformat()
                    }
                    del active_matches[match_id]
                    print(f"Notifica inviata: {home} - {away} ({first_score} → 1-1) - {elapsed_game_minutes:.1f} min di gioco (stessa metà tempo, attendibilità {combined_reliability}/5)")
                else:
                    # Scaduta, rimuovi dal tracking
                    del active_matches[match_id]
                    print(f"Partita scaduta (>{elapsed_game_minutes:.1f} min di gioco): {home} - {away}")
        
        # CASO 3: Partita tracciata che non è più 1-0/0-1 e non è 1-1 (es. 2-0, 0-2, ecc.)
        elif match_id in active_matches:
            # Rimuovi dal tracking (non è più interessante)
            del active_matches[match_id]
            print(f"Partita rimossa dal tracking (punteggio cambiato): {home} - {away}")
    
    # Salva stato
    save_active_matches(active_matches)
    save_sent_matches(sent_matches)


# ---------- STATO RUNTIME PER COMANDI ----------
from collections import defaultdict

last_check_started_at = None
last_check_finished_at = None
last_check_error = None
total_notifications_sent = 0
daily_notifications = defaultdict(int)


# ---------- COMANDI TELEGRAM ----------
def cmd_start(update, context):
    """Messaggio di benvenuto"""
    welcome_text = (
        "👋 Benvenuto in QrGolBot!\n\n"
        "⚽ Bot per notifiche 1-1 Live\n\n"
        "Il bot monitora tutte le partite live da SofaScore e ti avvisa quando:\n"
        "• Una partita è 1-0 o 0-1\n"
        "• Diventa 1-1 entro 10 minuti di gioco\n"
        "• Entrambi i gol sono nella stessa metà tempo\n\n"
        "📊 Sistema di Attendibilità (0-5):\n"
        "❌ 0: Nessun dato disponibile\n"
        "⚠️ 1-2: Dati parziali o calcolati\n"
        "✅ 3-4: Dati buoni/alti\n"
        "✅✅ 5: Massima attendibilità\n\n"
        "📋 Usa /help per vedere tutti i comandi disponibili\n"
        "🔍 Usa /live per vedere le partite live rilevanti\n"
        "📊 Usa /status per lo stato del bot"
    )
    update.effective_message.reply_text(welcome_text)


def cmd_ping(update, context):
    """Verifica se il bot è attivo"""
    update.effective_message.reply_text("pong ✅")


def cmd_help(update, context):
    """Mostra guida dettagliata"""
    help_text = (
        "⚽ QrGolBot - Notifiche 1-1 Live\n\n"
        "Cosa fa: Monitora tutte le partite live (SofaScore) e invia notifiche "
        "quando il punteggio diventa 1-1 con questi criteri:\n"
        "• Partita era 1-0 o 0-1\n"
        "• Diventa 1-1 entro 10 minuti di gioco dal primo gol\n"
        "• Entrambi i gol nella stessa metà tempo (1H o 2H)\n"
        "• Squadre opposte\n\n"
        "📊 Sistema di Attendibilità (0-5):\n"
        "Ogni notifica include un indicatore di attendibilità:\n"
        "❌ 0: Nessun dato minuto disponibile\n"
        "⚠️ 1-2: Minuto calcolato ma con dati parziali\n"
        "✅ 3-4: Minuto calcolato correttamente con periodo\n"
        "✅✅ 5: Massima attendibilità\n\n"
        "📋 Comandi disponibili:\n"
        "/start - Messaggio di benvenuto\n"
        "/ping - Verifica se il bot è attivo\n"
        "/help - Questa guida\n"
        "/status - Stato ultimo check, errori, statistiche\n"
        "/live - Elenco partite live rilevanti (1-0/0-1/1-1)\n"
        "/see_all_games - Tutte le partite trovate\n"
        "/active - Partite attualmente in tracking (1-0/0-1)\n"
        "/interested - Partite che sono state notificate (reportate)\n"
        "/stats - Statistiche notifiche (ultimi 7 giorni)"
    )
    update.effective_message.reply_text(help_text)


def cmd_status(update, context):
    """Mostra stato del bot"""
    lines = []
    lines.append("📊 Stato Bot:")
    lines.append(f"Intervallo controlli: {POLL_INTERVAL} secondi ({POLL_INTERVAL // 60} minuto{'i' if POLL_INTERVAL // 60 > 1 else ''})")
    
    if last_check_started_at:
        lines.append(f"Ultimo check start: {last_check_started_at.strftime('%H:%M:%S')}")
    else:
        lines.append("Ultimo check start: Nessuno")
    
    if last_check_finished_at:
        lines.append(f"Ultimo check end: {last_check_finished_at.strftime('%H:%M:%S')}")
        if last_check_started_at:
            elapsed = (last_check_finished_at - last_check_started_at).total_seconds()
            lines.append(f"Durata ultimo check: {elapsed:.1f}s")
    else:
        lines.append("Ultimo check end: Nessuno")
    
    if last_check_error:
        lines.append(f"⚠️ Ultimo errore: {last_check_error}")
    else:
        lines.append("✅ Nessun errore")
    
    # Carica partite attive
    active_matches = load_active_matches()
    lines.append(f"Partite in tracking: {len(active_matches)}")
    
    # Statistiche giornaliere
    today = datetime.now().strftime("%Y-%m-%d")
    lines.append(f"Notifiche oggi: {daily_notifications.get(today, 0)}")
    lines.append(f"Totale notifiche: {total_notifications_sent}")
    
    update.effective_message.reply_text("\n".join(lines))


def cmd_live(update, context):
    """Mostra partite live attualmente monitorate"""
    try:
        # Esegui uno scraping veloce
        matches = scrape_sofascore()
        
        if not matches:
            update.effective_message.reply_text("Nessuna partita live al momento.")
            return
        
        # Filtra solo partite 1-0, 0-1, o 1-1
        relevant = [m for m in matches if 
                   (m["score_home"] == 1 and m["score_away"] == 0) or
                   (m["score_home"] == 0 and m["score_away"] == 1) or
                   (m["score_home"] == 1 and m["score_away"] == 1)]
        
        if not relevant:
            update.effective_message.reply_text(f"Trovate {len(matches)} partite live, nessuna in stato 1-0/0-1/1-1.")
            return
        
        lines = [f"📊 Partite live rilevanti: {len(relevant)}"]
        for m in relevant[:20]:  # Limita a 20 per non superare limiti Telegram
            minute_str = f" {m['minute']}'" if m.get('minute') is not None else " N/A'"
            reliability = m.get('reliability', 0)
            reliability_emoji = ["❌", "⚠️", "⚠️", "✅", "✅", "✅✅"][min(reliability, 5)]
            lines.append(f"• {m['home']} - {m['away']} {m['score_home']}-{m['score_away']}{minute_str} {reliability_emoji} ({m['league']})")
        
        if len(relevant) > 20:
            lines.append(f"... e altre {len(relevant) - 20} partite")
        
        update.effective_message.reply_text("\n".join(lines)[:4000])
    except Exception as e:
        update.effective_message.reply_text(f"Errore nel recupero partite: {e}")


def cmd_see_all_games(update, context):
    """Mostra TUTTE le partite trovate dallo scraper"""
    try:
        # Esegui scraping
        update.effective_message.reply_text("🔍 Scraping in corso...")
        matches = scrape_sofascore()
        
        if not matches:
            update.effective_message.reply_text("Nessuna partita trovata al momento.")
            return
        
        lines = [f"⚽ Tutte le partite trovate: {len(matches)}"]
        lines.append("")
        
        # Mostra tutte le partite (senza filtri, incluso 0-0)
        for i, m in enumerate(matches, 1):
            minute_str = f" {m['minute']}'" if m.get('minute') is not None else " N/A'"
            reliability = m.get('reliability', 0)
            reliability_emoji = ["❌", "⚠️", "⚠️", "✅", "✅", "✅✅"][min(reliability, 5)]
            country = f" ({m['country']})" if m.get('country') and m['country'] != "Unknown" else ""
            lines.append(f"{i}. {m['home']} - {m['away']} {m['score_home']}-{m['score_away']}{minute_str} {reliability_emoji}")
            lines.append(f"   {m['league']}{country}")
            lines.append("")
            
            # Limita a 50 partite per non superare i limiti di Telegram (4096 caratteri)
            if i >= 50:
                lines.append(f"... e altre {len(matches) - 50} partite")
                break
        
        # Se il messaggio è troppo lungo, dividilo in più messaggi
        text = "\n".join(lines)
        if len(text) > 4000:
            # Dividi in chunk
            chunks = []
            current_chunk = [lines[0], lines[1], ""]  # Header
            current_length = len("\n".join(current_chunk))
            
            for line in lines[2:]:
                line_len = len(line) + 1  # +1 per newline
                if current_length + line_len > 4000:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = [line]
                    current_length = line_len
                else:
                    current_chunk.append(line)
                    current_length += line_len
            
            if current_chunk:
                chunks.append("\n".join(current_chunk))
            
            # Invia primo chunk
            update.effective_message.reply_text(chunks[0])
            # Invia altri chunk se presenti
            for chunk in chunks[1:]:
                update.effective_message.reply_text(chunk)
        else:
            update.effective_message.reply_text(text)
            
    except Exception as e:
        update.effective_message.reply_text(f"Errore nel recupero partite: {e}")


def cmd_active(update, context):
    """Mostra partite attualmente in tracking (1-0/0-1)"""
    active_matches = load_active_matches()
    
    if not active_matches:
        update.effective_message.reply_text("Nessuna partita in tracking al momento.")
        return
    
    # Filtra solo quelle in 1-0 o 0-1
    filtered = {}
    for match_id, match_data in active_matches.items():
        first_score = match_data.get("first_score", "")
        if first_score in ["1-0", "0-1"]:
            filtered[match_id] = match_data
    
    if not filtered:
        update.effective_message.reply_text("Nessuna partita in tracking (1-0/0-1) al momento.")
        return
    
    lines = [f"📋 Partite in tracking (1-0/0-1): {len(filtered)}"]
    now = datetime.now()
    
    # Ottieni partite live per mostrare minuto attuale
    try:
        live_matches = scrape_sofascore()
        live_dict = {get_match_id(m["home"], m["away"], m["league"]): m for m in live_matches}
    except:
        live_dict = {}
    
    for match_id, match_data in list(filtered.items())[:15]:  # Limita a 15
        first_goal_time = match_data["first_goal_time"]
        elapsed_minutes = (now - first_goal_time).total_seconds() / 60
        remaining = max(0, 10 - elapsed_minutes)
        
        # Mostra minuto attuale se disponibile
        current_minute = "N/A"
        reliability_emoji = ""
        if match_id in live_dict:
            live_match = live_dict[match_id]
            if live_match.get('minute') is not None:
                current_minute = f"{live_match['minute']}'"
                reliability = live_match.get('reliability', 0)
                reliability_emoji = ["❌", "⚠️", "⚠️", "✅", "✅", "✅✅"][min(reliability, 5)]
        
        lines.append(
            f"• {match_data['home']} - {match_data['away']} "
            f"({match_data['first_score']} al {match_data.get('first_goal_minute', 'N/A')}') - "
            f"Minuto attuale: {current_minute} {reliability_emoji} - "
            f"{remaining:.1f} min rimanenti"
        )
    
    if len(filtered) > 15:
        lines.append(f"... e altre {len(filtered) - 15} partite")
    
    update.effective_message.reply_text("\n".join(lines)[:4000])


def cmd_interested(update, context):
    """Mostra partite che sono state notificate (reportate)"""
    sent_matches = load_sent_matches()
    
    if not sent_matches:
        update.effective_message.reply_text("Nessuna partita notificata finora.")
        return
    
    lines = [f"📢 Partite notificate (reportate): {len(sent_matches)}"]
    lines.append("")
    
    # Ordina per data di notifica (più recenti prima)
    sorted_matches = sorted(
        sent_matches.items(),
        key=lambda x: x[1].get("notified_at", ""),
        reverse=True
    )
    
    for i, (match_id, match_data) in enumerate(sorted_matches[:20], 1):  # Limita a 20
        if isinstance(match_data, dict) and match_data:
            home = match_data.get("home", "?")
            away = match_data.get("away", "?")
            league = match_data.get("league", "Unknown")
            country = match_data.get("country", "")
            first_score = match_data.get("first_score", "?")
            first_min = match_data.get("first_minute", "?")
            second_min = match_data.get("second_minute", "?")
            notified_at = match_data.get("notified_at", "")
            
            country_str = f" ({country})" if country and country != "Unknown" else ""
            reliability = match_data.get("reliability", 0)
            reliability_emoji = ["❌", "⚠️", "⚠️", "✅", "✅", "✅✅"][min(reliability, 5)]
            
            lines.append(f"{i}. {home} - {away}")
            lines.append(f"   {league}{country_str}")
            lines.append(f"   {first_score} al {first_min}' → 1-1 al {second_min}'")
            lines.append(f"   Attendibilità: {reliability}/5 {reliability_emoji}")
            if notified_at:
                try:
                    dt = datetime.fromisoformat(notified_at)
                    lines.append(f"   Notificata: {dt.strftime('%d/%m/%Y %H:%M')}")
                except:
                    pass
            lines.append("")
        else:
            # Vecchio formato (solo ID)
            lines.append(f"{i}. {match_id}")
            lines.append("")
    
    if len(sent_matches) > 20:
        lines.append(f"... e altre {len(sent_matches) - 20} partite")
    
    # Se il messaggio è troppo lungo, dividilo
    text = "\n".join(lines)
    if len(text) > 4000:
        # Dividi in chunk
        chunks = []
        current_chunk = [lines[0], lines[1], ""]
        current_length = len("\n".join(current_chunk))
        
        for line in lines[2:]:
            line_len = len(line) + 1
            if current_length + line_len > 4000:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = line_len
            else:
                current_chunk.append(line)
                current_length += line_len
        
        if current_chunk:
            chunks.append("\n".join(current_chunk))
        
        update.effective_message.reply_text(chunks[0])
        for chunk in chunks[1:]:
            update.effective_message.reply_text(chunk)
    else:
        update.effective_message.reply_text(text)


def cmd_stats(update, context):
    """Mostra statistiche notifiche"""
    today = datetime.now().date()
    lines = ["📊 Statistiche notifiche (ultimi 7 giorni):"]
    
    total_week = 0
    for i in range(7):
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        count = daily_notifications.get(date_str, 0)
        total_week += count
        day_name = d.strftime("%a %d/%m")
        lines.append(f"• {day_name}: {count}")
    
    lines.append(f"\nTotale settimana: {total_week}")
    lines.append(f"Totale generale: {total_notifications_sent}")
    
    update.effective_message.reply_text("\n".join(lines))


def setup_telegram_commands():
    """Configura e avvia Updater per comandi Telegram"""
    try:
        # Elimina webhook se presente
        try:
            bot.delete_webhook(drop_pending_updates=True)
            print("✅ Webhook eliminato (se presente)")
        except Exception as e:
            print(f"⚠️ Errore eliminazione webhook (probabilmente non presente): {e}")
        
        updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
        dp = updater.dispatcher
        
        # Configura logging per sopprimere errori Conflict
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.WARNING
        )
        
        # Filtra errori Conflict dal logging di python-telegram-bot
        class ConflictFilter(logging.Filter):
            def filter(self, record):
                msg = str(record.getMessage())
                return "Conflict" not in msg and "conflict" not in msg.lower()
        
        # Applica filtro ai logger di telegram
        telegram_logger = logging.getLogger('telegram')
        telegram_logger.addFilter(ConflictFilter())
        updater_logger = logging.getLogger('telegram.ext.updater')
        updater_logger.addFilter(ConflictFilter())
        
        # Gestione errori
        def error_handler(update, context):
            """Gestisce errori durante l'elaborazione degli update"""
            error = context.error
            if isinstance(error, Conflict):
                # Ignora silenziosamente errori Conflict (più istanze in esecuzione)
                return
            elif isinstance(error, NetworkError):
                # Ignora silenziosamente errori di rete temporanei
                return
            else:
                # Log altri errori
                print(f"⚠️ Errore durante elaborazione update: {error}")
        
        dp.add_error_handler(error_handler)
        
        # Registra comandi
        dp.add_handler(CommandHandler("start", cmd_start))
        dp.add_handler(CommandHandler("ping", cmd_ping))
        dp.add_handler(CommandHandler("help", cmd_help))
        dp.add_handler(CommandHandler("status", cmd_status))
        dp.add_handler(CommandHandler("live", cmd_live))
        dp.add_handler(CommandHandler("see_all_games", cmd_see_all_games))
        dp.add_handler(CommandHandler("active", cmd_active))
        dp.add_handler(CommandHandler("interested", cmd_interested))
        dp.add_handler(CommandHandler("reported", cmd_interested))  # Alias
        dp.add_handler(CommandHandler("stats", cmd_stats))
        
        # Gestione comandi nei canali
        def handle_channel_command(update, context):
            post = getattr(update, "channel_post", None)
            if not post:
                return
            text = post.text or post.caption or ""
            if not text.startswith("/"):
                return
            
            parts = text.split()
            cmd = parts[0].split("@")[0].lstrip("/")
            args = parts[1:] if len(parts) > 1 else []
            
            # Mappa comandi
            if cmd == "start":
                cmd_start(update, context)
            elif cmd == "ping":
                cmd_ping(update, context)
            elif cmd == "help":
                cmd_help(update, context)
            elif cmd == "status":
                cmd_status(update, context)
            elif cmd == "live":
                cmd_live(update, context)
            elif cmd == "see_all_games":
                cmd_see_all_games(update, context)
            elif cmd == "active":
                cmd_active(update, context)
            elif cmd == "stats":
                cmd_stats(update, context)
        
        dp.add_handler(MessageHandler(Filters.update.channel_posts, handle_channel_command))
        
        # Avvia polling con gestione errori silenziosa
        try:
            updater.start_polling(drop_pending_updates=True)
            print("✅ Updater Telegram avviato - Comandi disponibili")
        except Conflict:
            print("⚠️ Errore Conflict all'avvio (probabilmente più istanze in esecuzione)")
            print("⚠️ Il bot continuerà a funzionare ma potrebbe non ricevere comandi")
        except Exception as e:
            print(f"⚠️ Errore all'avvio polling: {e}")
        
        return updater
    except Exception as e:
        print(f"⚠️ Errore nell'avvio Updater: {e}")
        return None


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Handler per HTTP server di keep-alive"""
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Disabilita logging HTTP per ridurre spam
        pass


def start_http_server(port=8080):
    """Avvia HTTP server per keep-alive (evita che Render si addormenti)"""
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        Thread(target=server.serve_forever, daemon=True).start()
        print(f"✅ HTTP server avviato su porta {port} (keep-alive)")
    except Exception as e:
        print(f"⚠️ Errore avvio HTTP server: {e}")


def main():
    """Loop principale: controlla partite ogni POLL_INTERVAL secondi"""
    global last_check_started_at, last_check_finished_at, last_check_error
    
    print("Bot avviato. Monitoraggio partite live su SofaScore...")
    
    # Avvia HTTP server per keep-alive (se PORT è definito, usa quello)
    port = int(os.getenv('PORT', 8080))
    start_http_server(port)
    
    # Avvia Updater per comandi Telegram in background
    updater = setup_telegram_commands()
    
    while True:
        try:
            last_check_started_at = datetime.now()
            last_check_error = None
            process_matches()
            last_check_finished_at = datetime.now()
        except Exception as e:
            last_check_error = str(e)
            print(f"Errore: {e}")
        print(f"Attesa {POLL_INTERVAL} secondi prima del prossimo controllo...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
