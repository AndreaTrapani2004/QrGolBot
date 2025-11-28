#!/bin/bash

# Script per eseguire il bot in locale

cd "$(dirname "$0")"

# Attiva virtual environment
source .venv/bin/activate

# Verifica variabili d'ambiente
if [ -z "$TELEGRAM_TOKEN" ]; then
    echo "❌ TELEGRAM_TOKEN non impostato"
    echo "Imposta con: export TELEGRAM_TOKEN='xxxxx:xxxxx-xxxxx'"
    exit 1
fi

if [ -z "$CHAT_ID" ]; then
    echo "❌ CHAT_ID non impostato"
    echo "Imposta con: export CHAT_ID='123456789'"
    exit 1
fi

# Verifica ChromeDriver (cerca in posizioni comuni)
CHROMEDRIVER_PATH=""
if command -v chromedriver &> /dev/null; then
    CHROMEDRIVER_PATH=$(which chromedriver)
elif [ -f "/opt/homebrew/bin/chromedriver" ]; then
    CHROMEDRIVER_PATH="/opt/homebrew/bin/chromedriver"
    export PATH="/opt/homebrew/bin:$PATH"
elif [ -f "/usr/local/bin/chromedriver" ]; then
    CHROMEDRIVER_PATH="/usr/local/bin/chromedriver"
    export PATH="/usr/local/bin:$PATH"
fi

if [ -z "$CHROMEDRIVER_PATH" ]; then
    echo "❌ ChromeDriver non trovato"
    echo "Installa con: brew install chromedriver"
    exit 1
fi

echo "✅ ChromeDriver trovato: $CHROMEDRIVER_PATH"

echo "✅ Tutto pronto!"
echo "🚀 Avvio bot..."
echo ""

# Esegui il bot
python live_goals_bot.py

