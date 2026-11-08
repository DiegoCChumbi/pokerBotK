"""Punto de entrada de la TUI Cockpit para PokerFight."""
import sys
from .app import PokerCockpitApp, TEXTUAL_AVAILABLE
from .backend_bridge import DummyBrainBackend, LivePokerBotBridge


def main() -> None:
    if not TEXTUAL_AVAILABLE:
        print("[ERROR] El framework 'textual' no está instalado en este entorno de Python.")
        print("Instálalo ejecutando: pip install textual")
        print("\nEl backend del bot (poker_bot) y la suite de pruebas siguen 100% funcionales.")
        sys.exit(1)

    use_live = "--live" in sys.argv
    backend = LivePokerBotBridge() if use_live else DummyBrainBackend()
    mode_str = "LIVE BOT ENGINE" if use_live else "DUMMY BACKEND"
    print(f"Iniciando PokerCockpitApp con {mode_str}...")

    app = PokerCockpitApp(backend=backend)
    app.run()


if __name__ == "__main__":
    main()
