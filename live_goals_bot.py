"""
live_goals_bot.py
Bot Telegram per notifiche 1-1 live in tutti i campionati
Monitora partite live tramite scraping FlashScore Mobile
Traccia partite in stato 1-0/0-1 e notifica quando diventano 1-1 entro 10 minuti
"""

import time
import json
import os
from datetime import datetime, timedelta
from telegram import Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from threading import Thread
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from bs4 import BeautifulSoup

# ---------- CONFIGURAZIONE ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
POLL_INTERVAL = 300  # 5 minuti = 300 secondi
FLASHSCORE_URL = "https://m.flashscore.com"

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
            return set(json.load(f))
    except Exception:
        return set()


def save_sent_matches(sent_set):
    """Salva le partite già notificate su file"""
    with open(SENT_MATCHES_FILE, "w") as f:
        json.dump(list(sent_set), f)


def get_match_id(home, away, league):
    """Genera un ID univoco per una partita"""
    return f"{home}_{away}_{league}".lower().replace(" ", "_")


def setup_selenium_driver():
    """Configura e restituisce un driver Selenium per FlashScore Mobile"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Esegui in background
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1")
    
    # Prova a creare il driver (richiede chromedriver installato)
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        print(f"Errore nella configurazione di Selenium: {e}")
        print("Assicurati di avere ChromeDriver installato e nel PATH")
        raise


def scrape_flashscore_mobile():
    """Scraping di FlashScore Mobile per ottenere tutte le partite live"""
    driver = None
    try:
        driver = setup_selenium_driver()
        
        # Naviga direttamente alla pagina live
        print("Navigazione a FlashScore Mobile Live...")
        driver.get("https://m.flashscore.com/football/live/")
        
        # Attendi che la pagina si carichi completamente
        time.sleep(8)
        
        # Prova a scrollare per caricare più contenuti
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        # Ottieni l'HTML della pagina
        html = driver.page_source
        soup = BeautifulSoup(html, "lxml")
        
        matches = []
        
        # FlashScore Mobile: cerca l'elemento score-data che contiene tutte le partite
        score_data = soup.find("div", id="score-data")
        
        if not score_data:
            print("Elemento score-data non trovato, uso approccio alternativo...")
            # Fallback: cerca link a partite
            match_links = soup.find_all("a", href=lambda x: x and "/match/" in x)
            print(f"Trovati {len(match_links)} link a partite (fallback)")
            return matches
        
        print("Trovato elemento score-data, estrazione partite...")
        
        # Trova tutte le sezioni (ogni h4 definisce una lega)
        all_h4 = score_data.find_all("h4")
        
        # Se non ci sono h4, usa l'intero score-data
        if not all_h4:
            all_h4 = [score_data]
        
        # Per ogni sezione (lega), estrai le partite
        for h4_idx, h4 in enumerate(all_h4):
            # Estrai lega dall'h4
            league_full = h4.get_text(strip=True) if h4.name == "h4" else "Unknown"
            # Rimuovi "Standings" e link dalla lega
            league_full = league_full.split("Standings")[0].strip()
            # Estrai paese se presente (prima dei ":")
            country = "Unknown"
            league = league_full
            if ":" in league_full:
                country = league_full.split(":")[0].strip()
                league = league_full.split(":")[-1].strip()
            
            # Trova tutte le partite di questa sezione
            # Le partite sono tra questo h4 e il prossimo h4 (o fine)
            if h4_idx < len(all_h4) - 1:
                # Partite tra questo h4 e il prossimo
                next_h4 = all_h4[h4_idx + 1]
                section = score_data.find_all("a", href=lambda x: x and "/match/" in x)
                # Filtra solo quelle tra h4 e next_h4
                match_links = []
                current = h4.next_sibling
                while current and current != next_h4:
                    if hasattr(current, "find_all"):
                        links = current.find_all("a", href=lambda x: x and "/match/" in x)
                        match_links.extend(links)
                    current = current.next_sibling
            else:
                # Ultima sezione: tutte le partite dopo l'ultimo h4
                match_links = []
                current = h4.next_sibling
                while current:
                    if hasattr(current, "find_all"):
                        links = current.find_all("a", href=lambda x: x and "/match/" in x)
                        match_links.extend(links)
                    current = current.next_sibling
            
            # Se non trova partite con il metodo sopra, usa tutte le partite dopo l'h4
            if not match_links:
                # Trova tutti i link dopo questo h4
                all_links = score_data.find_all("a", href=lambda x: x and "/match/" in x)
                # Prendi solo quelli che vengono dopo questo h4
                h4_position = list(score_data.children).index(h4) if h4 in list(score_data.children) else 0
                match_links = [link for link in all_links if score_data.find_all().index(link) > h4_position] if all_links else []
            
            # Se ancora non trova, usa tutte le partite (fallback)
            if not match_links:
                match_links = score_data.find_all("a", href=lambda x: x and "/match/" in x)
            
            print(f"Sezione {h4_idx + 1}: {league} ({country}) - {len(match_links)} partite")
            
            # Per ogni link, estrai le informazioni della partita
            # Struttura: <span class="live">24'</span>Squadra1 - Squadra2 <a href="/match/..." class="live">1:0</a>
            for link in match_links:
                try:
                    # Estrai punteggio dal link (formato "1:0" o "1-0")
                    score_text = link.get_text(strip=True)
                    # Converti ":" in "-" se necessario
                    if ":" in score_text:
                        score_text = score_text.replace(":", "-")
                    
                    # Verifica che sia un punteggio valido
                    score_parts = score_text.split("-")
                    if len(score_parts) != 2 or not score_parts[0].isdigit() or not score_parts[1].isdigit():
                        continue
                    
                    score_home = int(score_parts[0])
                    score_away = int(score_parts[1])
                    
                    # Trova il span con il minuto prima del link
                    minute = None
                    prev_sibling = link.find_previous_sibling("span", class_="live")
                    if prev_sibling:
                        minute_text = prev_sibling.get_text(strip=True)
                        # Rimuovi apostrofo se presente
                        minute_text = minute_text.replace("'", "").replace("'", "")
                        try:
                            minute = int(minute_text)
                        except:
                            pass
                    
                    # Trova il testo con le squadre (tra il span e il link)
                    # Il testo delle squadre è nel contenitore padre
                    parent = link.parent
                    if not parent:
                        continue
                    
                    # Ottieni tutto il testo del parent e rimuovi il punteggio e il minuto
                    full_text = parent.get_text(strip=True)
                    # Rimuovi il punteggio dal testo
                    full_text = full_text.replace(score_text, "").strip()
                    # Rimuovi il minuto se presente
                    if minute:
                        full_text = full_text.replace(f"{minute}'", "").replace(f"{minute}'", "").strip()
                    
                    # Estrai squadre (formato "Squadra1 - Squadra2")
                    if " - " not in full_text:
                        continue
                    
                    parts = full_text.split(" - ")
                    if len(parts) != 2:
                        continue
                    
                    home = parts[0].strip()
                    away = parts[1].strip()
                    
                    # Rimuovi eventuali caratteri speciali o immagini
                    home = " ".join(home.split())
                    away = " ".join(away.split())
                    
                    if not home or not away:
                        continue
                    
                    matches.append({
                        "home": home,
                        "away": away,
                        "score_home": score_home,
                        "score_away": score_away,
                        "league": league,
                        "country": country,
                        "minute": minute
                    })
                except Exception as e:
                    print(f"Errore nell'estrazione partita: {e}")
                    continue
        
        print(f"Estratte {len(matches)} partite valide")
        return matches
    
    except Exception as e:
        print(f"Errore nello scraping FlashScore: {e}")
        return []
    
    finally:
        if driver:
            driver.quit()


def send_message(home, away, league, country, first_score, first_min, second_score, second_min):
    """Invia messaggio Telegram con i dettagli del pattern 1-1"""
    global total_notifications_sent
    
    text = f"{home} - {away} ({league} - {country})\n" \
           f"{first_score} ; {first_min}'\n" \
           f"{second_score} ; {second_min}'"
    bot.send_message(chat_id=CHAT_ID, text=text)
    
    # Aggiorna statistiche
    total_notifications_sent += 1
    today = datetime.now().strftime("%Y-%m-%d")
    daily_notifications[today] += 1


def cleanup_expired_matches(active_matches):
    """Rimuove partite scadute (>10 minuti dal primo gol)"""
    now = datetime.now()
    expired = []
    
    for match_id, match_data in active_matches.items():
        first_goal_time = match_data["first_goal_time"]
        elapsed = (now - first_goal_time).total_seconds() / 60  # minuti
        
        if elapsed > 10:
            expired.append(match_id)
    
    for match_id in expired:
        del active_matches[match_id]
        print(f"Partita scaduta rimossa dal tracking: {match_id}")
    
    return active_matches


# ---------- LOGICA PRINCIPALE ----------
def process_matches():
    """Processa tutte le partite live e gestisce il tracking 1-0/0-1 → 1-1"""
    active_matches = load_active_matches()
    sent_matches = load_sent_matches()
    
    # Rimuovi partite scadute (>10 minuti)
    active_matches = cleanup_expired_matches(active_matches)
    
    # Scraping partite live
    print("Scraping FlashScore Mobile...")
    live_matches = scrape_flashscore_mobile()
    print(f"Trovate {len(live_matches)} partite live")
    
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
                active_matches[match_id] = {
                    "home": home,
                    "away": away,
                    "league": league,
                    "country": country,
                    "first_goal_time": now,
                    "first_score": first_score,
                    "first_goal_minute": minute if minute else 0
                }
                print(f"Nuova partita tracciata: {home} - {away} ({first_score})")
        
        # CASO 2: Partita già tracciata (1-0/0-1) che diventa 1-1
        elif score_home == 1 and score_away == 1:
            if match_id in active_matches:
                # Calcola tempo trascorso dal primo gol
                match_data = active_matches[match_id]
                first_goal_time = match_data["first_goal_time"]
                elapsed_minutes = (now - first_goal_time).total_seconds() / 60
                
                # Se è diventata 1-1 entro 10 minuti, invia notifica
                if elapsed_minutes <= 10:
                    first_score = match_data["first_score"]
                    first_min = match_data["first_goal_minute"]
                    second_min = minute if minute else int(elapsed_minutes)  # Usa minuto corrente o calcolato
                    
                    send_message(home, away, league, country, first_score, first_min, "1-1", second_min)
                    sent_matches.add(match_id)
                    del active_matches[match_id]
                    print(f"Notifica inviata: {home} - {away} ({first_score} → 1-1)")
                else:
                    # Scaduta, rimuovi dal tracking
                    del active_matches[match_id]
                    print(f"Partita scaduta (>{elapsed_minutes:.1f} min): {home} - {away}")
        
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
def cmd_ping(update, context):
    """Verifica se il bot è attivo"""
    update.effective_message.reply_text("pong ✅")


def cmd_help(update, context):
    """Mostra guida dettagliata"""
    help_text = (
        "⚽ QrGolBot - Notifiche 1-1 Live\n\n"
        "Cosa fa: Monitora tutte le partite live (FlashScore) e invia notifiche "
        "quando il punteggio diventa 1-1 con questi criteri:\n"
        "• Partita era 1-0 o 0-1\n"
        "• Diventa 1-1 entro 10 minuti dal primo gol\n"
        "• Stessa metà tempo (1H o 2H)\n"
        "• Squadre opposte\n\n"
        "📋 Comandi disponibili:\n"
        "/ping - Verifica se il bot è attivo\n"
        "/help - Questa guida\n"
        "/status - Stato ultimo check, errori, statistiche\n"
        "/live - Elenco partite live attualmente monitorate\n"
        "/stats - Statistiche notifiche (ultimi 7 giorni)\n"
        "/active - Partite attualmente in tracking (1-0/0-1)"
    )
    update.effective_message.reply_text(help_text)


def cmd_status(update, context):
    """Mostra stato del bot"""
    lines = []
    lines.append("📊 Stato Bot:")
    lines.append(f"Intervallo controlli: {POLL_INTERVAL // 60} minuti")
    
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
        matches = scrape_flashscore_mobile()
        
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
            minute = f" {m['minute']}'" if m.get('minute') else ""
            lines.append(f"• {m['home']} - {m['away']} {m['score_home']}-{m['score_away']}{minute} ({m['league']})")
        
        if len(relevant) > 20:
            lines.append(f"... e altre {len(relevant) - 20} partite")
        
        update.effective_message.reply_text("\n".join(lines)[:4000])
    except Exception as e:
        update.effective_message.reply_text(f"Errore nel recupero partite: {e}")


def cmd_active(update, context):
    """Mostra partite attualmente in tracking (1-0/0-1)"""
    active_matches = load_active_matches()
    
    if not active_matches:
        update.effective_message.reply_text("Nessuna partita in tracking al momento.")
        return
    
    lines = [f"📋 Partite in tracking: {len(active_matches)}"]
    now = datetime.now()
    
    for match_id, match_data in list(active_matches.items())[:15]:  # Limita a 15
        first_goal_time = match_data["first_goal_time"]
        elapsed_minutes = (now - first_goal_time).total_seconds() / 60
        remaining = max(0, 10 - elapsed_minutes)
        
        lines.append(
            f"• {match_data['home']} - {match_data['away']} "
            f"({match_data['first_score']}) - "
            f"{remaining:.1f} min rimanenti"
        )
    
    if len(active_matches) > 15:
        lines.append(f"... e altre {len(active_matches) - 15} partite")
    
    update.effective_message.reply_text("\n".join(lines)[:4000])


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
        except:
            pass
        
        updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
        dp = updater.dispatcher
        
        # Registra comandi
        dp.add_handler(CommandHandler("ping", cmd_ping))
        dp.add_handler(CommandHandler("help", cmd_help))
        dp.add_handler(CommandHandler("status", cmd_status))
        dp.add_handler(CommandHandler("live", cmd_live))
        dp.add_handler(CommandHandler("active", cmd_active))
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
            if cmd == "ping":
                cmd_ping(update, context)
            elif cmd == "help":
                cmd_help(update, context)
            elif cmd == "status":
                cmd_status(update, context)
            elif cmd == "live":
                cmd_live(update, context)
            elif cmd == "active":
                cmd_active(update, context)
            elif cmd == "stats":
                cmd_stats(update, context)
        
        dp.add_handler(MessageHandler(Filters.update.channel_posts, handle_channel_command))
        
        # Avvia polling
        updater.start_polling(drop_pending_updates=True)
        print("✅ Updater Telegram avviato - Comandi disponibili")
        return updater
    except Exception as e:
        print(f"⚠️ Errore nell'avvio Updater: {e}")
        return None


def main():
    """Loop principale: controlla partite ogni POLL_INTERVAL secondi"""
    global last_check_started_at, last_check_finished_at, last_check_error
    
    print("Bot avviato. Monitoraggio partite live su FlashScore Mobile...")
    
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
