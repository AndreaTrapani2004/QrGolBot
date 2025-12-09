"""
live_goals_bot.py
Bot Telegram per notifiche 1-1 live in tutti i campionati
Monitora partite live tramite API SofaScore
Traccia partite in stato 1-0/0-1 e notifica quando diventano 1-1 entro 10 minuti
"""

import time
import sys
import json
import tempfile
from io import BytesIO
import os
import re
import requests
from datetime import datetime, timedelta
from telegram import Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram.error import Conflict, NetworkError
from threading import Thread, Lock
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------- CONFIGURAZIONE ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
POLL_INTERVAL = 30  # Intervallo di controllo in secondi
SOFASCORE_API_URL = "https://api.sofascore.com/api/v1"
# Proxy opzionale per SofaScore (es. Cloudflare Workers). Se settato, sostituisce la base URL.
SOFASCORE_PROXY_BASE = os.getenv("SOFASCORE_PROXY_BASE", SOFASCORE_API_URL)

# Bot Telegram
bot = Bot(token=TELEGRAM_TOKEN)

# File per salvare le partite attive in tracking
ACTIVE_MATCHES_FILE = "active_matches.json"
# File per salvare le partite già notificate (evita duplicati)
SENT_MATCHES_FILE = "sent_matches.json"
# File per salvare la deadlist (partite da non controllare)
DEADLIST_FILE = "deadlist.json"

# ---------- RATE LIMITING GLOBALE ----------
_last_api_call_time = 0
_rate_limit_lock = Lock()
MIN_DELAY_BETWEEN_API_CALLS = 0.2  # Secondi minimi tra chiamate API (evita rate limiting, ma non troppo aggressivo)


# ---------- FUNZIONI UTILI ----------
def load_active_matches():
    """Carica le partite attive in tracking (0-0 o 1-0/0-1) da file"""
    try:
        with open(ACTIVE_MATCHES_FILE, "r") as f:
            data = json.load(f)
            # Converti timestamp string in datetime (solo se esiste)
            for match_id, match_data in data.items():
                if "first_goal_time" in match_data:
                    try:
                        match_data["first_goal_time"] = datetime.fromisoformat(match_data["first_goal_time"])
                    except:
                        # Se la conversione fallisce, rimuovi la chiave
                        del match_data["first_goal_time"]
            return data
    except Exception:
        return {}


def save_active_matches(active_matches):
    """Salva le partite attive in tracking su file"""
    # Converti datetime in string per JSON (solo se esiste)
    data = {}
    for match_id, match_data in active_matches.items():
        data[match_id] = match_data.copy()
        if "first_goal_time" in match_data and match_data["first_goal_time"]:
            # Converti datetime in string solo se esiste
            if isinstance(match_data["first_goal_time"], datetime):
                data[match_id]["first_goal_time"] = match_data["first_goal_time"].isoformat()
            # Se è già una stringa, lasciala così
        # Se non esiste (partite 0-0), non aggiungere la chiave
    
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


def load_deadlist():
    """Carica la deadlist (partite da non controllare) da file"""
    try:
        with open(DEADLIST_FILE, "r") as f:
            data = json.load(f)
            # Se è una lista (vecchio formato), converti in set
            if isinstance(data, list):
                return set(data)
            # Se è un dict, usa le chiavi come set
            if isinstance(data, dict):
                return set(data.keys())
            return set(data) if data else set()
    except Exception:
        return set()


def save_deadlist(deadlist):
    """Salva la deadlist su file"""
    # Salva come lista per semplicità
    with open(DEADLIST_FILE, "w") as f:
        json.dump(list(deadlist), f, indent=2)


def should_be_deadlisted(match, sent_matches, active_matches):
    """
    Determina se una partita dovrebbe essere aggiunta alla deadlist.
    
    Una partita va in deadlist se:
    1. È già stata notificata (sent_matches)
    2. Ha un punteggio che non può diventare 1-1 (es. 2-0, 0-2, 2-1, 3-0, ecc.)
    3. È finita
    4. Era 1-0/0-1 ma è scaduta (>10 minuti dal primo gol)
    """
    match_id = get_match_id(match["home"], match["away"], match["league"])
    score_home = match["score_home"]
    score_away = match["score_away"]
    status_type = (match.get("status_type") or "").lower()
    minute = match.get("minute")
    
    # 1. Già notificata
    if match_id in sent_matches:
        return True, "già notificata"
    
    # 2. Finita
    if status_type in ("finished", "after overtime", "after penalty", "afterpenalties", "after overtime and penalties"):
        return True, "finita"
    
    # 3. Punteggio che non può diventare 1-1
    # Solo questi punteggi possono diventare 1-1: 0-0, 1-0, 0-1, 1-1
    # Tutti gli altri (2-0, 0-2, 2-1, 1-2, 3-0, ecc.) vanno in deadlist
    if not ((score_home == 0 and score_away == 0) or 
            (score_home == 1 and score_away == 0) or 
            (score_home == 0 and score_away == 1) or 
            (score_home == 1 and score_away == 1)):
        return True, f"punteggio {score_home}-{score_away} non può diventare 1-1"
    
    # 4. Era 1-0/0-1 ma è scaduta (>10 minuti dal primo gol)
    if match_id in active_matches:
        match_data = active_matches[match_id]
        if "first_score" in match_data:  # Era 1-0/0-1
            first_goal_minute = match_data.get("first_goal_minute", 0)
            if first_goal_minute > 0 and minute is not None and minute > 0:
                elapsed = minute - first_goal_minute
                if elapsed > 10:
                    return True, f"scaduta ({elapsed} minuti dal primo gol)"
    
    return False, None


def get_match_id(home, away, league):
    """Genera un ID univoco per una partita"""
    return f"{home}_{away}_{league}".lower().replace(" ", "_")


def _wait_for_rate_limit():
    """Attende se necessario per rispettare il rate limiting globale"""
    global _last_api_call_time
    with _rate_limit_lock:
        now = time.time()
        elapsed = now - _last_api_call_time
        if elapsed < MIN_DELAY_BETWEEN_API_CALLS:
            sleep_time = MIN_DELAY_BETWEEN_API_CALLS - elapsed
            time.sleep(sleep_time)
        _last_api_call_time = time.time()


def _fetch_sofascore_json(url, headers, max_retries=2):
    """
    Tenta fetch diretto; su 403 usa fallback r.jina.ai come proxy pubblico.
    Con retry e exponential backoff per errori 429.
    """
    now_utc = datetime.utcnow().isoformat() + "Z"
    
    # Rate limiting: attendi prima di fare la chiamata
    _wait_for_rate_limit()
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                print(f"[{now_utc}] ⚠️ JSON non valido dalla API diretta, lunghezza body={len(resp.text)}")
                sys.stdout.flush()
                return None
        if resp.status_code != 403:
            print(f"[{now_utc}] ⚠️ Errore API SofaScore: status={resp.status_code}")
            sys.stdout.flush()
            return None
        
        # Fallback via r.jina.ai (no crediti, spesso evita blocchi IP)
        # Convertiamo https://... in http://... per l'URL interno
        inner = url.replace("https://", "http://")
        proxy_url = f"https://r.jina.ai/{inner}"
        
        # Retry con exponential backoff per errori 429
        for attempt in range(max_retries + 1):
            if attempt > 0:
                # Exponential backoff: 1s, 2s, 4s...
                backoff_time = 2 ** (attempt - 1)
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ⏳ Retry {attempt}/{max_retries} dopo {backoff_time}s...")
                sys.stdout.flush()
                time.sleep(backoff_time)
            
            _wait_for_rate_limit()  # Rate limiting anche per retry
            
            if attempt == 0:
                print(f"[{now_utc}] 🔁 Fallback via r.jina.ai: {proxy_url}")
                sys.stdout.flush()
            
            prox_resp = requests.get(
                proxy_url,
                headers={
                    "User-Agent": headers.get("User-Agent", "Mozilla/5.0"),
                    "Accept": "application/json",
                },
                timeout=20,
            )
            
            if prox_resp.status_code == 200:
                try:
                    import json as _json
                    wrapper = prox_resp.json()
                    # r.jina.ai restituisce un wrapper con data.content come stringa JSON
                    if isinstance(wrapper, dict) and "data" in wrapper:
                        data_obj = wrapper.get("data", {})
                        if isinstance(data_obj, dict) and "content" in data_obj:
                            content_str = data_obj.get("content", "")
                            if isinstance(content_str, str) and content_str.strip().startswith("{"):
                                # Parse il JSON annidato
                                try:
                                    return _json.loads(content_str)
                                except Exception as e:
                                    print(f"[{now_utc}] ⚠️ Errore parse JSON annidato da r.jina.ai: {e}")
                                    sys.stdout.flush()
                    # Se non è il formato r.jina.ai, restituisci direttamente
                    return wrapper
                except Exception:
                    # Alcuni proxy restituiscono testo JSON valido: prova json.loads
                    import json as _json
                    try:
                        return _json.loads(prox_resp.text)
                    except Exception:
                        print(f"[{now_utc}] ⚠️ Impossibile parsare JSON dal fallback, primi 200 char: {prox_resp.text[:200]!r}")
                        sys.stdout.flush()
                        return None
            elif prox_resp.status_code == 429:
                # Rate limited - continuerà con il retry
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ⚠️ Rate limited (429) da r.jina.ai, tentativo {attempt + 1}/{max_retries + 1}")
                sys.stdout.flush()
                if attempt < max_retries:
                    continue  # Prova di nuovo
                else:
                    print(f"[{now_utc}] ⚠️ Fallback r.jina.ai fallito dopo {max_retries + 1} tentativi: status=429")
                    sys.stdout.flush()
                    return None
            else:
                # Altro errore
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ⚠️ Fallback r.jina.ai fallito: status={prox_resp.status_code}")
                sys.stdout.flush()
                return None
        
        return None
    except Exception as e:
        print(f"[{now_utc}] ⚠️ Eccezione fetch SofaScore: {e}")
        sys.stdout.flush()
        return None


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
        
        # Prova multipli endpoint per recuperare eventi live
        endpoints = [
            f"{SOFASCORE_PROXY_BASE}/sport/football/events/live",
            f"{SOFASCORE_PROXY_BASE}/sport/football/events/inplay",
            f"{SOFASCORE_PROXY_BASE}/sport/football/livescore",
        ]
        
        now_utc = datetime.utcnow().isoformat() + "Z"
        events = []
        for idx, url in enumerate(endpoints, start=1):
            print(f"[{now_utc}] Richiesta API SofaScore: {url}... (tentativo {idx})")
            sys.stdout.flush()
            data = _fetch_sofascore_json(url, headers)
            if not data:
                continue
            # Normalizza le possibili chiavi
            events = data.get("events") or data.get("results") or []
            print(f"[{now_utc}] ✅ Trovate {len(events)} partite live dalla API (tentativo {idx})")
            sys.stdout.flush()
            if events:
                break
            else:
                # Log breve del payload per capire il formato
                try:
                    import json as _json
                    raw = _json.dumps(data)[:200]
                except Exception:
                    raw = str(data)[:200]
                print(f"[{now_utc}] ℹ️ Nessun evento nell'endpoint, anteprima payload: {raw}")
                sys.stdout.flush()
        
        matches = []
        if not events:
            print(f"[{now_utc}] ⚠️ Nessun evento trovato su tutti gli endpoint live")
            sys.stdout.flush()
            return []
        
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
                
                # Estrai ID partita per recuperare eventi/gol
                event_id = event.get("id")
                
                # PROVA: Verifica se l'evento contiene già i risultati per periodo
                # (potrebbe essere disponibile nella prima chiamata senza bisogno di chiamate aggiuntive)
                result_1h = None
                result_2h = None
                
                # DEBUG: Log tutte le chiavi disponibili nell'evento (solo per la prima partita)
                if len(matches) == 0 and event_id:
                    now_utc = datetime.utcnow().isoformat() + "Z"
                    event_keys = list(event.keys())
                    print(f"[{now_utc}] 🔍 DEBUG: Chiavi disponibili nell'evento {event_id}: {event_keys}")
                    sys.stdout.flush()
                    # Verifica se c'è un campo periods
                    if "periods" in event:
                        print(f"[{now_utc}] ✅ DEBUG: Campo 'periods' trovato nell'evento!")
                        sys.stdout.flush()
                
                # Cerca periods nell'evento stesso
                periods = event.get("periods", [])
                if periods:
                    period_1h = None
                    period_2h = None
                    for p in periods:
                        period_num = p.get("period")
                        if period_num == 1:
                            period_1h = p
                        elif period_num == 2:
                            period_2h = p
                    
                    if period_1h and period_2h:
                        home_1h = period_1h.get("homeScore", 0)
                        away_1h = period_1h.get("awayScore", 0)
                        home_ft = period_2h.get("homeScore", 0)
                        away_ft = period_2h.get("awayScore", 0)
                        result_1h = f"{home_1h}-{away_1h}"
                        result_2h = f"{home_ft}-{away_ft}"
                
                matches.append({
                    "home": home,
                    "away": away,
                    "score_home": score_home,
                    "score_away": score_away,
                    "league": league,
                    "country": country,
                    "minute": minute,
                    "period": period,  # 1 = primo tempo, 2 = secondo tempo
                    "reliability": reliability,  # Attendibilità 0-5
                    "event_id": event_id,  # ID partita per recuperare eventi/gol
                    "status_code": status.get("code"),
                    "status_type": status.get("type"),
                    "status_description": status.get("description", ""),
                    "result_1h": result_1h,  # Risultato 1H se disponibile dalla prima chiamata
                    "result_2h": result_2h   # Risultato 2H se disponibile dalla prima chiamata
                })
            except Exception as e:
                print(f"Errore nell'estrazione partita: {e}")
                continue
        
        print(f"[{now_utc}] ✅ Estratte {len(matches)} partite totali dalla risposta")
        sys.stdout.flush()
        return matches
    
    except requests.exceptions.RequestException as e:
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] Errore nella richiesta API SofaScore: {e}")
        sys.stdout.flush()
        return []
    except Exception as e:
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] Errore nello scraping SofaScore: {e}")
        sys.stdout.flush()
        return []


def get_match_goal_minute(event_id, score_home, score_away, headers, goal_number=1):
    """
    Recupera il minuto esatto di un gol dalla partita tramite API SofaScore
    
    Args:
        event_id: ID della partita
        score_home: Punteggio casa
        score_away: Punteggio trasferta
        headers: Headers HTTP per la richiesta
        goal_number: 1 = primo gol, 2 = secondo gol, -1 = ultimo gol
    """
    if not event_id:
        return None, 0
    
    try:
        # Endpoint per eventi/incidents della partita
        url = f"{SOFASCORE_PROXY_BASE}/event/{event_id}/incidents"
        
        now_utc = datetime.utcnow().isoformat() + "Z"
        data = _fetch_sofascore_json(url, headers)
        
        if not data:
            return None, 0
        
        # Estrai incidents/events
        incidents = data.get("incidents") or data.get("events") or []
        
        # Filtra solo i gol (type=100 goal, type=101 own goal)
        goals = []
        for incident in incidents:
            incident_type = incident.get("type", {})
            if isinstance(incident_type, dict):
                type_id = incident_type.get("id")
            else:
                type_id = incident_type
            
            # Type 100 = goal, 101 = own goal
            if type_id in [100, 101]:
                minute = incident.get("minute")
                if minute is None:
                    continue
                
                # Estrai informazioni squadra (può essere isHome/isAway o team)
                is_home = incident.get("isHome")
                is_away = incident.get("isAway")
                
                # Se non trovato con isHome/isAway, prova con team
                if is_home is None and is_away is None:
                    team = incident.get("team", {})
                    if isinstance(team, dict):
                        # Controlla se è la squadra di casa
                        if team.get("id") == incident.get("homeTeam", {}).get("id") if isinstance(incident.get("homeTeam"), dict) else False:
                            is_home = True
                            is_away = False
                        elif team.get("id") == incident.get("awayTeam", {}).get("id") if isinstance(incident.get("awayTeam"), dict) else False:
                            is_home = False
                            is_away = True
                
                # Se ancora non abbiamo informazioni sulla squadra, salta
                if is_home is None and is_away is None:
                    continue
                
                # Normalizza: se uno è True, l'altro deve essere False
                if is_home is True:
                    is_away = False
                elif is_away is True:
                    is_home = False
                elif is_home is None:
                    is_home = False
                elif is_away is None:
                    is_away = False
                
                goals.append({
                    "minute": minute,
                    "is_home": bool(is_home),
                    "incident": incident
                })
        
        if not goals:
            now_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[{now_utc}] ⚠️ Nessun gol trovato negli incidents per event_id={event_id}")
            sys.stdout.flush()
            return None, 0
        
        # Ordina per minuto (cronologico)
        goals.sort(key=lambda x: x["minute"])
        
        # Seleziona il gol richiesto
        if goal_number == -1:
            # Ultimo gol
            selected_goal = goals[-1]
            goal_desc = "ultimo"
        elif goal_number == 1:
            # Primo gol
            selected_goal = goals[0]
            goal_desc = "primo"
        elif goal_number == 2:
            # Secondo gol
            if len(goals) >= 2:
                selected_goal = goals[1]
                goal_desc = "secondo"
            else:
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ⚠️ Secondo gol non trovato (solo {len(goals)} gol disponibili) per event_id={event_id}")
                sys.stdout.flush()
                return None, 0
        else:
            # Numero gol non valido
            return None, 0
        
        goal_minute = selected_goal["minute"]
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] ✅ Minuto ESATTO recuperato dall'API: {goal_desc} gol al minuto {goal_minute}' (event_id={event_id}, totale gol={len(goals)})")
        sys.stdout.flush()
        
        return goal_minute, 5  # Attendibilità massima perché è il minuto esatto dall'API
    except Exception as e:
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] ⚠️ Errore recupero minuto gol da eventi: {e}")
        sys.stdout.flush()
        return None, 0


def send_message(home, away, league, country, first_score, first_min, second_score, second_min, reliability=0, event_id=None):
    """Invia messaggio Telegram con i dettagli del pattern 1-1"""
    global total_notifications_sent
    
    # Emoji per attendibilità
    reliability_emoji = ["❌", "⚠️", "⚠️", "✅", "✅", "✅✅"]
    reliability_idx = min(reliability, 5)
    reliability_emoji_str = reliability_emoji[reliability_idx]

    # Costruisci link SofaScore se event_id disponibile
    link = ""
    if event_id:
        link = f"\n🔗 https://www.sofascore.com/event/{event_id}"

    text = (
        f"⚽ GOL QR {reliability_emoji_str}\n\n"
        f"🏠 {home}\n"
        f"🆚 {away}\n"
        f"📊 {league} - {country}\n"
        f"⏱️ Minuto {first_score} ; {first_min}'\n"
        f"⏱️ Minuto {second_score} ; {second_min}'{link}"
    )
    bot.send_message(chat_id=CHAT_ID, text=text)
    
    # Aggiorna statistiche
    total_notifications_sent += 1
    today = datetime.now().strftime("%Y-%m-%d")
    daily_notifications[today] += 1


def cleanup_expired_matches(active_matches, current_matches_dict):
    """Rimuove partite scadute (>10 minuti di gioco dal primo gol)"""
    expired = []
    
    for match_id, match_data in active_matches.items():
        # Le partite 0-0 tracciate non scadono, rimangono tracciate finché non cambiano punteggio
        if match_data.get("score") == "0-0":
            continue  # Non rimuovere partite 0-0
        
        # Solo le partite con primo gol (1-0/0-1) possono scadere
        first_goal_minute = match_data.get("first_goal_minute", 0)
        if first_goal_minute == 0:
            continue  # Se non c'è minuto del primo gol, non scadere
        
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
def get_scores_from_incidents(event_id, headers):
    """
    Recupera il risultato all'intervallo (1H) e finale (2H) dall'API SofaScore.
    Torna (result_1H, result_2H) come stringhe 'H-A'. Se non disponibili, torna ('', '').
    """
    try:
        if not event_id:
            return "", ""
        
        # Prova prima a recuperare dal dettaglio evento (più affidabile per partite finite)
        try:
            url = f"{SOFASCORE_PROXY_BASE}/event/{event_id}"
            now_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[{now_utc}] 🔍 DEBUG: Chiamata API /event/{event_id} per recuperare risultati")
            sys.stdout.flush()
            
            event_data = _fetch_sofascore_json(url, headers)
            if event_data:
                print(f"[{now_utc}] 🔍 DEBUG: Risposta API /event/{event_id} ricevuta, keys: {list(event_data.keys())}")
                sys.stdout.flush()
                
                # Cerca i risultati nei periodi
                event_obj = event_data.get("event", {})
                periods = event_obj.get("periods", [])
                
                print(f"[{now_utc}] 🔍 DEBUG: Periodi trovati: {len(periods)}")
                sys.stdout.flush()
                
                if periods:
                    # Primo periodo (1H)
                    period_1h = None
                    # Secondo periodo (2H) o risultato finale
                    period_2h = None
                    
                    for period in periods:
                        period_num = period.get("period")
                        print(f"[{now_utc}] 🔍 DEBUG: Periodo trovato: {period_num}, homeScore={period.get('homeScore')}, awayScore={period.get('awayScore')}")
                        sys.stdout.flush()
                        if period_num == 1:
                            period_1h = period
                        elif period_num == 2:
                            period_2h = period
                    
                    # Se abbiamo i periodi, usa quelli
                    if period_1h and period_2h:
                        home_1h = period_1h.get("homeScore", 0)
                        away_1h = period_1h.get("awayScore", 0)
                        home_ft = period_2h.get("homeScore", 0)
                        away_ft = period_2h.get("awayScore", 0)
                        result_1h = f"{home_1h}-{away_1h}"
                        result_2h = f"{home_ft}-{away_ft}"
                        print(f"[{now_utc}] ✅ DEBUG: Risultati recuperati da /event: 1H={result_1h}, 2H={result_2h}")
                        sys.stdout.flush()
                        return result_1h, result_2h
                    else:
                        print(f"[{now_utc}] ⚠️ DEBUG: Periodi 1H o 2H non trovati (1H={period_1h is not None}, 2H={period_2h is not None})")
                        sys.stdout.flush()
                else:
                    print(f"[{now_utc}] ⚠️ DEBUG: Nessun periodo trovato in event_data")
                    sys.stdout.flush()
            else:
                print(f"[{now_utc}] ⚠️ DEBUG: event_data è None o vuoto")
                sys.stdout.flush()
        except Exception as e:
            now_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[{now_utc}] ⚠️ DEBUG: Errore recupero da /event/{event_id}: {e}")
            sys.stdout.flush()
            pass  # Fallback agli incidents
        
        # Fallback: calcola dai incidents
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] 🔍 DEBUG: Fallback a /incidents per event_id {event_id}")
        sys.stdout.flush()
        
        url = f"{SOFASCORE_PROXY_BASE}/event/{event_id}/incidents"
        data = _fetch_sofascore_json(url, headers)
        incidents = (data or {}).get("incidents") or (data or {}).get("events") or []
        
        print(f"[{now_utc}] 🔍 DEBUG: Incidents trovati: {len(incidents)}")
        sys.stdout.flush()
        
        # Estrai solo gol e autogol
        goals = []
        for inc in incidents:
            inc_type = inc.get("type", {})
            type_id = inc_type.get("id") if isinstance(inc_type, dict) else inc_type
            
            # Type 100 = goal, 101 = own goal
            if type_id in [100, 101]:
                minute = inc.get("minute")
                if minute is None:
                    continue
                
                # Estrai informazioni squadra (può essere isHome/isAway o team)
                is_home = inc.get("isHome")
                is_away = inc.get("isAway")
                
                # Se non trovato con isHome/isAway, prova con team
                if is_home is None and is_away is None:
                    team = inc.get("team", {})
                    if isinstance(team, dict):
                        # Controlla se è la squadra di casa
                        if team.get("id") == inc.get("homeTeam", {}).get("id") if isinstance(inc.get("homeTeam"), dict) else False:
                            is_home = True
                            is_away = False
                        elif team.get("id") == inc.get("awayTeam", {}).get("id") if isinstance(inc.get("awayTeam"), dict) else False:
                            is_home = False
                            is_away = True
                
                # Se ancora non abbiamo informazioni sulla squadra, prova a dedurlo dal tipo
                if is_home is None and is_away is None:
                    # Se non possiamo determinare la squadra, salta questo gol
                    # (potrebbe essere un gol annullato o un errore nei dati)
                    continue
                
                # Normalizza: se uno è True, l'altro deve essere False
                if is_home is True:
                    is_away = False
                elif is_away is True:
                    is_home = False
                elif is_home is None:
                    is_home = False
                elif is_away is None:
                    is_away = False
                
                goals.append({"minute": minute, "is_home": bool(is_home), "is_away": bool(is_away)})
        
        print(f"[{now_utc}] 🔍 DEBUG: Gol trovati negli incidents: {len(goals)}")
        sys.stdout.flush()
        
        if not goals:
            print(f"[{now_utc}] ⚠️ DEBUG: Nessun gol trovato, restituisco ('', '')")
            sys.stdout.flush()
            return "", ""
        
        # Ordina per minuto
        goals.sort(key=lambda g: g["minute"])
        
        # Calcola risultati
        home_1h = away_1h = 0
        home_ft = away_ft = 0
        for g in goals:
            if g["is_home"]:
                home_ft += 1
            elif g["is_away"]:
                away_ft += 1
            # Halftime: conteggia gol fino al 45'
            if g["minute"] <= 45:
                if g["is_home"]:
                    home_1h += 1
                elif g["is_away"]:
                    away_1h += 1
        
        result_1h = f"{home_1h}-{away_1h}"
        result_2h = f"{home_ft}-{away_ft}"
        print(f"[{now_utc}] ✅ DEBUG: Risultati calcolati da incidents: 1H={result_1h}, 2H={result_2h}")
        sys.stdout.flush()
        
        return result_1h, result_2h
    except Exception as e:
        # Log errore per debug
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] ⚠️ Errore recupero risultati per event_id {event_id}: {e}")
        sys.stdout.flush()
        return "", ""


def update_results_for_sent_matches(sent_matches, current_matches_dict, max_per_cycle=None):
    """
    Aggiorna le partite notificate salvando i risultati 1H e 2H
    non appena disponibili.
    
    Args:
        sent_matches: Dict delle partite già notificate
        current_matches_dict: Dict delle partite live attuali
        max_per_cycle: Numero massimo di partite da processare per ciclo (None = tutte)
    """
    if not sent_matches:
        return
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com"
    }
    
    # Filtra solo le partite che hanno bisogno di aggiornamento
    matches_to_update = []
    for match_id, match_data in sent_matches.items():
        if not isinstance(match_data, dict) or not match_data:
            continue
        
        event_id = match_data.get("event_id")
        if not event_id:
            continue
        
        live_match = current_matches_dict.get(match_id)
        minute = live_match.get("minute") if live_match else None
        period = live_match.get("period") if live_match else None
        status_type = (live_match.get("status_type") or "").lower() if live_match else ""
        
        halftime_ready = False
        final_ready = False
        
        if live_match:
            if (minute is not None and minute >= 45) or (period and period >= 2):
                halftime_ready = True
            if status_type in ("finished", "after overtime", "after penalty", "afterpenalties", "after overtime and penalties"):
                final_ready = True
            elif minute is not None and minute >= 95:
                final_ready = True
        else:
            # Se la partita non è più live assumiamo che sia conclusa
            halftime_ready = True
            final_ready = True
        
        need_halftime = halftime_ready and not match_data.get("result_1H")
        need_final = final_ready and not match_data.get("result_2H")
        
        if need_halftime or need_final:
            matches_to_update.append((match_id, match_data, live_match, need_halftime, need_final))
    
    # Limita il numero di partite processate per ciclo (solo se max_per_cycle è specificato)
    if max_per_cycle is not None and len(matches_to_update) > max_per_cycle:
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] ⚡ Limite update_results: processando {max_per_cycle} su {len(matches_to_update)} partite che necessitano aggiornamento")
        sys.stdout.flush()
        matches_to_update = matches_to_update[:max_per_cycle]
    
    for match_id, match_data, live_match, need_halftime, need_final in matches_to_update:
        event_id = match_data.get("event_id")
        
        # OTTIMIZZAZIONE: Prima controlla se i risultati sono già disponibili dalla prima chiamata API
        r1 = None
        r2 = None
        
        if live_match:
            # Se la partita è ancora live, controlla se abbiamo già i risultati dalla prima chiamata
            if need_halftime and live_match.get("result_1h"):
                r1 = live_match.get("result_1h")
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ✅ Risultato 1H recuperato dalla prima chiamata per {match_id}: {r1}")
                sys.stdout.flush()
            
            if need_final and live_match.get("result_2h"):
                r2 = live_match.get("result_2h")
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ✅ Risultato 2H recuperato dalla prima chiamata per {match_id}: {r2}")
                sys.stdout.flush()
        
        # Solo se non disponibili dalla prima chiamata, fai chiamata API aggiuntiva
        if (need_halftime and not r1) or (need_final and not r2):
            now_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[{now_utc}] 🔍 Risultati non disponibili dalla prima chiamata, faccio chiamata API aggiuntiva per {match_id}")
            sys.stdout.flush()
            api_r1, api_r2 = get_scores_from_incidents(event_id, headers)
            if need_halftime and not r1:
                r1 = api_r1
            if need_final and not r2:
                r2 = api_r2
        
        if need_halftime and r1:
            match_data["result_1H"] = r1
            now_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[{now_utc}] ✅ Risultato 1H salvato per {match_id}: {r1}")
            sys.stdout.flush()
        
        if need_final and r2:
            match_data["result_2H"] = r2
            now_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[{now_utc}] ✅ Risultato finale salvato per {match_id}: {r2}")
            sys.stdout.flush()

def process_matches():
    """Processa tutte le partite live e gestisce il tracking 1-0/0-1 → 1-1"""
    active_matches = load_active_matches()
    sent_matches = load_sent_matches()  # Ora è un dict, non un set
    deadlist = load_deadlist()  # Carica deadlist
    
    # Scraping partite live
    print("Scraping SofaScore...")
    live_matches = scrape_sofascore()
    now_utc = datetime.utcnow().isoformat() + "Z"
    print(f"[{now_utc}] ✅ Trovate {len(live_matches)} partite live totali dalla API")
    sys.stdout.flush()
    
    # Crea dizionario per lookup veloce delle partite live
    current_matches_dict = {}
    live_match_ids = set()
    for match in live_matches:
        match_id = get_match_id(match["home"], match["away"], match["league"])
        current_matches_dict[match_id] = match
        live_match_ids.add(match_id)
    
    # Aggiorna deadlist: aggiungi partite che devono essere deadlisted
    new_deadlisted = 0
    for match in live_matches:
        match_id = get_match_id(match["home"], match["away"], match["league"])
        if match_id not in deadlist:
            should_deadlist, reason = should_be_deadlisted(match, sent_matches, active_matches)
            if should_deadlist:
                deadlist.add(match_id)
                new_deadlisted += 1
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] 🚫 Partita aggiunta alla deadlist: {match['home']} - {match['away']} ({match['score_home']}-{match['score_away']}) - motivo: {reason}")
                sys.stdout.flush()
    
    # Pulisci deadlist: rimuovi partite che non sono più live (potrebbero essere finite o non più disponibili)
    removed_from_deadlist = 0
    deadlist_copy = deadlist.copy()
    for match_id in deadlist_copy:
        if match_id not in live_match_ids:
            # Mantieni in deadlist solo se è già stata notificata (non rimuoverla mai)
            if match_id not in sent_matches:
                deadlist.discard(match_id)
                removed_from_deadlist += 1
    
    if new_deadlisted > 0 or removed_from_deadlist > 0:
        now_utc = datetime.utcnow().isoformat() + "Z"
        print(f"[{now_utc}] 📊 Deadlist aggiornata: +{new_deadlisted} nuove, -{removed_from_deadlist} rimosse, totale: {len(deadlist)}")
        sys.stdout.flush()
    
    # Rimuovi partite scadute (>10 minuti di gioco)
    active_matches = cleanup_expired_matches(active_matches, current_matches_dict)
    
    now = datetime.now()
    
    # Conta quante partite vengono saltate per deadlist
    skipped_deadlist = 0
    
    for match in live_matches:
        home = match["home"]
        away = match["away"]
        score_home = match["score_home"]
        score_away = match["score_away"]
        league = match["league"]
        country = match.get("country", "Unknown")
        minute = match.get("minute")
        
        match_id = get_match_id(home, away, league)
        
        # OTTIMIZZAZIONE: Se la partita è in deadlist, salta completamente
        if match_id in deadlist:
            skipped_deadlist += 1
            continue
        
        # Se la partita è già stata notificata, salta (e aggiungi a deadlist)
        if match_id in sent_matches:
            deadlist.add(match_id)
            continue
        
        # CASO 0: Traccia partite 0-0 per rilevare quando diventano 1-0/0-1
        if score_home == 0 and score_away == 0:
            if match_id not in active_matches:
                # Traccia partita 0-0 per rilevare quando diventa 1-0/0-1
                active_matches[match_id] = {
                    "home": home,
                    "away": away,
                    "league": league,
                    "country": country,
                    "score": "0-0",
                    "last_minute": minute if minute is not None else 0,
                    "last_period": match.get("period")
                }
        
        # CASO 1: Partita passa da 0-0 a 1-0 o 0-1 (gol appena segnato!)
        elif (score_home == 1 and score_away == 0) or (score_home == 0 and score_away == 1):
            if match_id in active_matches:
                match_data = active_matches[match_id]
                # Se era 0-0, ora è diventata 1-0/0-1: il gol è stato segnato ora!
                if match_data.get("score") == "0-0":
                    first_score = "1-0" if score_home == 1 else "0-1"
                    period = match.get("period")  # 1 = primo tempo, 2 = secondo tempo
                    
                    # Il minuto del gol è il minuto corrente (o poco prima, massimo 1 minuto)
                    goal_minute = minute if minute is not None else 0
                    if goal_minute > 0:
                        # Sottrai 0-1 minuto per essere più precisi (il gol è stato segnato poco prima)
                        goal_minute = max(1, goal_minute - 1)
                    
                    # Aggiorna con i dati del primo gol
                    active_matches[match_id] = {
                        "home": home,
                        "away": away,
                        "league": league,
                        "country": country,
                        "first_goal_time": now,
                        "first_score": first_score,
                        "first_goal_minute": goal_minute,
                        "first_goal_period": period,
                        "first_goal_reliability": match.get("reliability", 4)  # Attendibilità alta perché rilevato al momento
                    }
                    now_utc = datetime.utcnow().isoformat() + "Z"
                    print(f"[{now_utc}] ✅ Partita tracciata: {home} - {away} (0-0 → {first_score}) al minuto {goal_minute}' - ESATTO (rilevato al momento)")
                    sys.stdout.flush()
            elif match_id not in active_matches:
                # Partita già 1-0/0-1 quando viene rilevata (non era tracciata come 0-0)
                # Non possiamo sapere il minuto esatto, quindi non tracciarla
                now_utc = datetime.utcnow().isoformat() + "Z"
                first_score = "1-0" if score_home == 1 else "0-1"
                print(f"[{now_utc}] ⚠️ Partita NON tracciata: {home} - {away} ({first_score}) - già in corso quando rilevata (minuto esatto non disponibile)")
                sys.stdout.flush()
        
        # CASO 2: Partita già tracciata (1-0/0-1) che diventa 1-1 (secondo gol appena segnato!)
        elif score_home == 1 and score_away == 1:
            if match_id in active_matches:
                match_data = active_matches[match_id]
                # Verifica che sia una partita tracciata con primo gol (non una 0-0)
                if "first_score" not in match_data:
                    # Era una 0-0, non tracciarla come 1-1
                    continue
                
                first_score = match_data["first_score"]
                first_min = match_data.get("first_goal_minute", 0)
                first_period = match_data.get("first_goal_period")  # 1 = primo tempo, 2 = secondo tempo
                
                # Il minuto del secondo gol è il minuto corrente (o poco prima, massimo 1 minuto)
                second_min = minute if minute is not None else 0
                if second_min > 0:
                    # Sottrai 0-1 minuto per essere più precisi (il gol è stato segnato poco prima)
                    second_min = max(1, second_min - 1)
                
                second_goal_reliability = match.get("reliability", 4)  # Attendibilità alta perché rilevato al momento
                
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
                    deadlist.add(match_id)  # Aggiungi a deadlist perché non può più essere tracciata
                    print(f"Partita scartata (gol in metà tempo diverse): {home} - {away} ({first_score} al {first_min}' → 1-1 al {second_min}')")
                    continue
                
                # Calcola differenza in minuti di gioco
                if first_min > 0 and second_min > 0:
                    elapsed_game_minutes = second_min - first_min
                else:
                    # Se non abbiamo minuti, non notificare
                    now_utc = datetime.utcnow().isoformat() + "Z"
                    print(f"[{now_utc}] ⚠️ Notifica NON inviata: {home} - {away} ({first_score} → 1-1) - minuti non disponibili (first_min={first_min}, second_min={second_min})")
                    sys.stdout.flush()
                    del active_matches[match_id]
                    deadlist.add(match_id)  # Aggiungi a deadlist
                    continue
                
                # Se è diventata 1-1 entro 10 minuti di gioco E stessa metà tempo, invia notifica
                if elapsed_game_minutes <= 10 and elapsed_game_minutes >= 0:
                    # Calcola attendibilità combinata (minimo tra i due)
                    first_reliability = match_data.get("first_goal_reliability", 0)
                    combined_reliability = min(first_reliability, second_goal_reliability)
                    
                    send_message(home, away, league, country, first_score, first_min, "1-1", second_min, combined_reliability, match.get("event_id"))
                    # Salva dettagli della partita notificata
                    sent_matches[match_id] = {
                        "home": home,
                        "away": away,
                        "league": league,
                        "country": country,
                        "event_id": match.get("event_id"),
                        "first_score": first_score,
                        "first_minute": first_min,
                        "second_minute": second_min,
                        "reliability": combined_reliability,
                        "notified_at": now.isoformat()
                    }
                    del active_matches[match_id]
                    deadlist.add(match_id)  # Aggiungi a deadlist perché già notificata
                    # Entrambi i minuti sono esatti perché rilevati al momento (0-0 → 1-0/0-1 e 1-0/0-1 → 1-1)
                    now_utc = datetime.utcnow().isoformat() + "Z"
                    print(f"[{now_utc}] ✅ Notifica inviata: {home} - {away} ({first_score} al {first_min}' [ESATTO] → 1-1 al {second_min}' [ESATTO]) - {elapsed_game_minutes:.1f} min di gioco (stessa metà tempo, attendibilità {combined_reliability}/5)")
                    sys.stdout.flush()
                else:
                    # Scaduta, rimuovi dal tracking e aggiungi a deadlist
                    del active_matches[match_id]
                    deadlist.add(match_id)  # Aggiungi a deadlist perché scaduta
                    print(f"Partita scaduta (>{elapsed_game_minutes:.1f} min di gioco): {home} - {away}")
        
        # CASO 3: Partita tracciata che cambia punteggio in modo non interessante
        elif match_id in active_matches:
            match_data = active_matches[match_id]
            # Se era 0-0 e ora non è più 0-0 e non è 1-0/0-1, rimuovila e aggiungi a deadlist
            if match_data.get("score") == "0-0":
                # Era 0-0, ora è cambiata ma non è 1-0/0-1 (es. 2-0, 0-2, 1-1, ecc.)
                del active_matches[match_id]
                # Se non è 1-1 (che viene gestito nel CASO 2), aggiungi a deadlist
                if not (score_home == 1 and score_away == 1):
                    deadlist.add(match_id)
                now_utc = datetime.utcnow().isoformat() + "Z"
                print(f"[{now_utc}] ⚠️ Partita rimossa dal tracking: {home} - {away} (era 0-0, ora {score_home}-{score_away})")
                sys.stdout.flush()
            # Se era 1-0/0-1 e ora non è più 1-0/0-1 e non è 1-1, rimuovila e aggiungi a deadlist
            elif "first_score" in match_data:
                # Era 1-0/0-1, ora è cambiata ma non è 1-1 (es. 2-0, 0-2, 2-1, ecc.)
                del active_matches[match_id]
                deadlist.add(match_id)  # Aggiungi a deadlist perché non può più diventare 1-1
                print(f"Partita rimossa dal tracking (punteggio cambiato): {home} - {away} (era {match_data.get('first_score')}, ora {score_home}-{score_away})")
    
    # Log statistiche finali
    processed_count = len(live_matches) - skipped_deadlist
    now_utc = datetime.utcnow().isoformat() + "Z"
    print(f"[{now_utc}] 📊 Statistiche ciclo: {len(live_matches)} partite ottenute, {processed_count} processate, {skipped_deadlist} saltate (deadlist)")
    sys.stdout.flush()
    
    # Aggiorna risultati salvati e persisti stato
    update_results_for_sent_matches(sent_matches, current_matches_dict)
    save_active_matches(active_matches)
    save_sent_matches(sent_matches)
    save_deadlist(deadlist)  # Salva deadlist


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
        "📋 Comandi disponibili:\n"
        "/start - Messaggio di benvenuto\n"
        "/ping - Verifica se il bot è attivo\n"
        "/help - Questa guida\n"
        "/status - Stato ultimo check, errori, statistiche\n"
        "/live - Elenco partite live rilevanti (1-0/0-1/1-1)\n"
        "/see_all_games - Tutte le partite trovate\n"
        "/active - Partite attualmente in tracking (1-0/0-1)\n"
        "/interested - Partite che sono state notificate (reportate)\n"
        "/stats - Statistiche notifiche (ultimi 7 giorni)\n"
        "/excel - Scarica Excel completo con risultati e minuti"
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
        first_goal_time = match_data.get("first_goal_time")
        if not first_goal_time:
            # Se non c'è first_goal_time, salta questa partita (probabilmente è ancora 0-0)
            continue
        
        # Converti stringa ISO in datetime se necessario
        if isinstance(first_goal_time, str):
            try:
                first_goal_time = datetime.fromisoformat(first_goal_time)
            except:
                continue
        
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


def cmd_excel(update, context):
    """Genera e invia un file Excel con tutte le partite notificate"""
    try:
        sent_matches = load_sent_matches()
        if not sent_matches:
            update.effective_message.reply_text("Nessuna partita notificata finora.")
            return
        
        # Prepara workbook
        try:
            from openpyxl import Workbook
        except ImportError:
            update.effective_message.reply_text("Libreria openpyxl non disponibile sul server.")
            return
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Matches"
        # Header
        ws.append([
            "home_team",
            "away_team",
            "country",
            "league",
            "result_1H",
            "result_2H",
            "min_1Gol",
            "min_1-1"
        ])
        
        rows_written = 0
        missing_count = 0
        missing_examples = []
        for match_id, m in sent_matches.items():
            if not isinstance(m, dict) or not m:
                # Vecchio formato - salta
                continue
            home = m.get("home", "")
            away = m.get("away", "")
            country = m.get("country", "")
            league = m.get("league", "")
            first_min = m.get("first_minute", "")
            second_min = m.get("second_minute", "")
            
            # Controlla se abbiamo già i risultati salvati
            result_1h = m.get("result_1H", "")
            result_2h = m.get("result_2H", "")
            
            if not result_1h or not result_2h:
                missing_count += 1
                if len(missing_examples) < 5:
                    missing_examples.append(f"{home} - {away}")
                continue
            
            ws.append([
                home,
                away,
                country,
                league,
                result_1h,
                result_2h,
                first_min,
                second_min
            ])
            rows_written += 1
        
        if rows_written == 0:
            msg = "Nessuna partita ha risultati completi da esportare."
            if missing_count:
                msg += f" In attesa di {missing_count} partite."
            update.effective_message.reply_text(msg)
            return
        
        # Salva su file temporaneo
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp_path = tmp.name
        wb.save(tmp_path)
        
        # Invia file
        with open(tmp_path, "rb") as f:
            update.effective_message.reply_document(document=f, filename="matches.xlsx", caption="Excel generato")
        
        if missing_count:
            info_msg = f"{missing_count} partite sono ancora senza risultati completi."
            if missing_examples:
                info_msg += " Esempi: " + ", ".join(missing_examples)
            update.effective_message.reply_text(info_msg)
        
        # Prova a rimuovere il file
        try:
            os.remove(tmp_path)
        except Exception:
            pass
    except Exception as e:
        update.effective_message.reply_text(f"Errore generazione Excel: {e}")

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
        dp.add_handler(CommandHandler("excel", cmd_excel))
        
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
            elif cmd == "excel":
                cmd_excel(update, context)
        
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
    def _send_health_response(self):
        """Invia risposta di health check"""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def do_GET(self):
        """Gestisce richieste GET"""
        if self.path == '/health' or self.path == '/':
            self._send_health_response()
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_HEAD(self):
        """Gestisce richieste HEAD (usate da Render e UptimeRobot)"""
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.send_header('Content-Length', '2')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    
    def do_OPTIONS(self):
        """Gestisce richieste OPTIONS"""
        self.send_response(200)
        self.send_header('Allow', 'GET, HEAD, OPTIONS')
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
    sys.stdout.flush()
    
    # Avvia HTTP server per keep-alive (se PORT è definito, usa quello)
    port = int(os.getenv('PORT', 8080))
    start_http_server(port)
    
    # Avvia Updater per comandi Telegram in background
    updater = setup_telegram_commands()
    
    while True:
        try:
            last_check_started_at = datetime.now()
            cycle_start_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[${cycle_start_utc}] ▶️ Inizio ciclo controllo partite")
            sys.stdout.flush()
            last_check_error = None
            process_matches()
            last_check_finished_at = datetime.now()
            cycle_end_utc = datetime.utcnow().isoformat() + "Z"
            print(f"[${cycle_end_utc}] ⏹️ Fine ciclo controllo partite")
            sys.stdout.flush()
        except Exception as e:
            last_check_error = str(e)
            print(f"Errore: {e}")
            sys.stdout.flush()
        print(f"Attesa {POLL_INTERVAL} secondi prima del prossimo controllo...")
        sys.stdout.flush()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
