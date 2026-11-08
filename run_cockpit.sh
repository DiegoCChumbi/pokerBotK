#!/usr/bin/env bash
# Script de conveniencia para ejecutar PokerCockpitApp
cd "$(dirname "$0")"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

exec python3 -m poker_tui "$@"
