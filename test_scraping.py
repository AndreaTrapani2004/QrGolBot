"""
Script di test per lo scraping FlashScore Mobile
Esegui questo script per verificare se lo scraping funziona correttamente
"""

import sys
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

FLASHSCORE_URL = "https://m.flashscore.com"

def setup_driver():
    """Configura driver Selenium"""
    chrome_options = Options()
    # Per test, esegui in modalità visibile (rimuovi headless)
    # chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1")
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        print(f"❌ Errore nella configurazione di Selenium: {e}")
        print("Assicurati di avere ChromeDriver installato e nel PATH")
        sys.exit(1)

def test_scraping():
    """Test dello scraping FlashScore Mobile"""
    driver = None
    try:
        print("🔍 Avvio test scraping FlashScore Mobile...")
        driver = setup_driver()
        
        # Prova URL diretta per live
        print("📱 Caricamento pagina FlashScore Mobile...")
        driver.get("https://m.flashscore.com/football/live/")
        time.sleep(8)  # Attendi caricamento completo
        
        print("📄 HTML caricato, analisi struttura...")
        
        # Salva HTML per debug
        html = driver.page_source
        with open("flashscore_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("💾 HTML salvato in flashscore_debug.html")
        
        soup = BeautifulSoup(html, "lxml")
        
        # Prova diversi selettori comuni per FlashScore
        print("\n🔎 Test selettori...")
        
        # Selettore 1: Cerca elementi con classi comuni
        selectors = [
            ("div[class*='event']", "Classi con 'event'"),
            ("div[class*='match']", "Classi con 'match'"),
            ("div[class*='game']", "Classi con 'game'"),
            ("div[data-testid*='event']", "Data-testid con 'event'"),
            ("a[href*='/match/']", "Link partite"),
            ("div[class*='sportName']", "Classi con 'sportName'"),
        ]
        
        matches_found = []
        
        for selector, description in selectors:
            try:
                elements = soup.select(selector)
                print(f"  {description}: {len(elements)} elementi trovati")
                if elements:
                    # Mostra esempio
                    example = elements[0].get_text(strip=True)[:100]
                    print(f"    Esempio: {example}...")
            except Exception as e:
                print(f"  {description}: Errore - {e}")
        
        # Cerca pattern testo con squadre e punteggio
        print("\n🔍 Ricerca pattern testo...")
        all_text = soup.get_text()
        lines = [line.strip() for line in all_text.split("\n") if line.strip()]
        
        # Cerca righe che potrebbero essere partite (contengono " - " e numeri)
        potential_matches = []
        for line in lines:
            if " - " in line and any(char.isdigit() for char in line):
                # Verifica se contiene un punteggio (pattern X-Y)
                parts = line.split()
                for part in parts:
                    if "-" in part:
                        score_parts = part.split("-")
                        if len(score_parts) == 2 and score_parts[0].isdigit() and score_parts[1].isdigit():
                            potential_matches.append(line)
                            break
        
        print(f"📊 Trovate {len(potential_matches)} righe potenziali con pattern partita")
        if potential_matches:
            print("\n📋 Prime 10 righe trovate:")
            for i, match in enumerate(potential_matches[:10], 1):
                print(f"  {i}. {match}")
        
        # Cerca elementi specifici con BeautifulSoup
        print("\n🔍 Analisi struttura HTML dettagliata...")
        
        # Cerca tutti i div e analizza struttura
        all_divs = soup.find_all("div", limit=100)
        print(f"  Trovati {len(all_divs)} div (primi 100)")
        
        # Cerca elementi con testo che contiene pattern partita
        match_elements = []
        for div in all_divs:
            text = div.get_text(strip=True)
            if len(text) > 10 and " - " in text:
                # Verifica se contiene punteggio
                if any(char.isdigit() for char in text):
                    match_elements.append({
                        "text": text[:150],
                        "classes": div.get("class", []),
                        "attrs": {k: v for k, v in div.attrs.items() if k != "class"}
                    })
        
        print(f"\n📊 Trovati {len(match_elements)} elementi con pattern partita")
        if match_elements:
            print("\n📋 Prime 5 partite trovate:")
            for i, match in enumerate(match_elements[:5], 1):
                print(f"\n  {i}. Testo: {match['text']}")
                if match['classes']:
                    print(f"     Classi: {match['classes']}")
                if match['attrs']:
                    print(f"     Attributi: {match['attrs']}")
        
        # Cerca link a partite
        print("\n🔗 Ricerca link partite...")
        links = soup.find_all("a", href=True)
        match_links = [link for link in links if "/match/" in link.get("href", "")]
        print(f"  Trovati {len(match_links)} link a partite")
        if match_links:
            print("  Esempi:")
            for link in match_links[:5]:
                href = link.get("href", "")
                text = link.get_text(strip=True)[:50]
                print(f"    - {text} -> {href}")
        
        print("\n✅ Test completato!")
        print("\n💡 Prossimi passi:")
        print("  1. Controlla flashscore_debug.html per vedere la struttura HTML")
        print("  2. Usa i selettori che hanno trovato più elementi")
        print("  3. Aggiusta i selettori nel codice principale")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Errore durante il test: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        if driver:
            print("\n🔒 Chiusura browser...")
            driver.quit()

if __name__ == "__main__":
    print("=" * 60)
    print("TEST SCRAPING FLASHSCORE MOBILE")
    print("=" * 60)
    success = test_scraping()
    sys.exit(0 if success else 1)

