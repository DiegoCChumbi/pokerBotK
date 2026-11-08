import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Protocol

from .exploitative_tracker import ExploitativeTracker
from .inference_engine import InferenceEngine
from .models import (
    ActionEvent,
    ActionType,
    BotDecision,
    GameState,
    PlayerState,
    Street,
)


class TransportAdapter(Protocol):
    """Interfaz abstracta para conectar vía WebSocket, HTTP o Mock."""
    async def receive_game_state(self) -> Dict[str, Any]: ...
    async def send_action(self, hand_id: str, decision: BotDecision) -> None: ...


class WatchdogBotClient:
    """
    Cliente principal con temporizador estricto (Watchdog).
    Garantiza una respuesta antes de los 4.5 segundos frente al límite de 5.0s.
    """
    HARD_TIMEOUT_SECONDS: float = 4.50
    SOFT_COMPUTE_DEADLINE_SECONDS: float = 3.80

    def __init__(
        self,
        transport: Optional[TransportAdapter],
        inference_engine: InferenceEngine,
        tracker: ExploitativeTracker,
        hard_timeout: float = HARD_TIMEOUT_SECONDS,
        soft_timeout: float = SOFT_COMPUTE_DEADLINE_SECONDS,
    ) -> None:
        self.transport = transport
        self.inference_engine = inference_engine
        self.tracker = tracker
        self.hard_timeout = hard_timeout
        self.soft_timeout = soft_timeout
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mc_worker")

    @staticmethod
    def compute_safe_fallback(state: GameState) -> BotDecision:
        """Determina en < 0.1ms la acción defensiva gratuita o de menor costo."""
        if state.to_call <= 0:
            return BotDecision(
                action=ActionType.CHECK,
                amount=0,
                reasoning="WATCHDOG_FALLBACK_FREE_CHECK",
                is_fallback=True,
            )
        return BotDecision(
            action=ActionType.FOLD,
            amount=0,
            reasoning="WATCHDOG_FALLBACK_SAFE_FOLD",
            is_fallback=True,
        )

    def parse_game_state(self, raw: Dict[str, Any]) -> GameState:
        """Deserializa el payload JSON de la mesa en una estructura GameState tipada."""
        players = []
        for p in raw.get("players", []):
            players.append(
                PlayerState(
                    player_id=p["player_id"],
                    seat=p.get("seat", 0),
                    stack=p.get("stack", 0),
                    current_bet=p.get("current_bet", 0),
                    is_active=p.get("is_active", True),
                    is_all_in=p.get("is_all_in", False),
                    position=p.get("position", "BTN"),
                )
            )

        actions = []
        for a in raw.get("action_history", []):
            actions.append(
                ActionEvent(
                    player_id=a["player_id"],
                    street=Street(a.get("street", "PREFLOP").upper()),
                    action_type=a.get("action_type", "CHECK"),
                    amount=a.get("amount", 0),
                    pot_before=a.get("pot_before", 0),
                    to_call_before=a.get("to_call_before", 0),
                )
            )

        street_str = raw.get("street", "PREFLOP").upper()
        street = Street(street_str) if hasattr(Street, street_str) else Street.PREFLOP

        return GameState(
            hand_id=raw.get("hand_id", "hand_0"),
            table_id=raw.get("table_id", "table_0"),
            hero_id=raw.get("hero_id", "hero"),
            street=street,
            hole_cards=raw.get("hole_cards", []),
            community_cards=raw.get("community_cards", []),
            pot=raw.get("pot", 0),
            to_call=raw.get("to_call", 0),
            min_raise=raw.get("min_raise", 0),
            max_raise=raw.get("max_raise", 1000),
            small_blind=raw.get("small_blind", 10),
            big_blind=raw.get("big_blind", 20),
            dealer_seat=raw.get("dealer_seat", 0),
            players=players,
            action_history=actions,
        )

    async def handle_turn(self, raw_payload: Dict[str, Any]) -> BotDecision:
        """
        Orquesta un turno individual bajo vigilancia estricta de asyncio.wait_for.
        Si la inferencia o red demoran más de hard_timeout (4.5s), aborta y
        retorna inmediatamente el safe fallback.
        """
        t_start = time.perf_counter()
        state = self.parse_game_state(raw_payload)
        fallback = self.compute_safe_fallback(state)
        cancel_event = threading.Event()

        # Encola la ingestión del estado de forma asíncrona no bloqueante
        self.tracker.enqueue_state_ingestion(state)

        try:
            decision = await asyncio.wait_for(
                self.inference_engine.decide_action(
                    state=state,
                    executor=self._executor,
                    cancel_event=cancel_event,
                    compute_budget_sec=self.soft_timeout,
                ),
                timeout=self.hard_timeout,
            )
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return BotDecision(
                action=decision.action,
                amount=decision.amount,
                reasoning=decision.reasoning,
                is_fallback=False,
                latency_ms=elapsed_ms,
            )
        except asyncio.TimeoutError:
            cancel_event.set()
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return BotDecision(
                action=fallback.action,
                amount=fallback.amount,
                reasoning=f"WATCHDOG_TIMEOUT_EXCEEDED ({self.hard_timeout}s)",
                is_fallback=True,
                latency_ms=elapsed_ms,
            )
        except Exception as exc:
            cancel_event.set()
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            return BotDecision(
                action=fallback.action,
                amount=fallback.amount,
                reasoning=f"RUNTIME_ERROR_FALLBACK: {type(exc).__name__}",
                is_fallback=True,
                latency_ms=elapsed_ms,
            )

    def shutdown(self) -> None:
        """Cierra el executor de hilos ordenadamente."""
        self._executor.shutdown(wait=False)
