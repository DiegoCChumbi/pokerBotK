import asyncio
import json
import logging
from typing import Any, Dict

from .exploitative_tracker import ExploitativeTracker
from .heuristic_engine import HeuristicEngine
from .inference_engine import InferenceEngine
from .models import BotDecision
from .network_watchdog import TransportAdapter, WatchdogBotClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pokerfight_bot")


class LocalApiMockTransport:
    """Implementación de referencia para pruebas locales."""

    async def receive_game_state(self) -> Dict[str, Any]:
        return {}

    async def send_action(self, hand_id: str, decision: BotDecision) -> None:
        logger.info(f"Jugada enviada para hand {hand_id}: {decision.action.value} {decision.amount}")


def create_bot(db_path: str = "pokerfight_tracker.db") -> WatchdogBotClient:
    tracker = ExploitativeTracker(db_path=db_path)
    heuristic = HeuristicEngine(default_iterations=12000, batch_size=400)
    inference = InferenceEngine(heuristic_engine=heuristic, tracker=tracker)
    transport = LocalApiMockTransport()

    client = WatchdogBotClient(
        transport=transport,
        inference_engine=inference,
        tracker=tracker,
        hard_timeout=4.50,
        soft_timeout=3.80,
    )
    return client


if __name__ == "__main__":
    bot = create_bot()
    print("PokerFight Bot inicializado correctamente con arquitectura de 4 módulos.")
