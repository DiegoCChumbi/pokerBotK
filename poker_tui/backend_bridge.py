import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Protocol


def canonicalize_card(card_str: str) -> str:
    """Convierte '3e' a '3s', '3t' a '3c', '3c' a '3h', etc."""
    if len(card_str) != 2:
        return card_str
    r = card_str[0].upper()
    s = card_str[1].lower()
    spanish_map = {"e": "s", "c": "h", "d": "d", "t": "c", "s": "s", "h": "h"}
    return f"{r}{spanish_map.get(s, s)}"


@dataclass(frozen=True)
class BrainResponse:
    action: Literal["FOLD", "CHECK", "CALL", "RAISE", "ALL_IN"]
    amount: int
    equity: float
    pot_odds: float
    reasoning: str
    latency_ms: float


class BrainBackendProtocol(Protocol):
    async def evaluate_table_state(self, payload: Dict[str, Any]) -> BrainResponse:
        """
        Recibe el diccionario ensamblado por la TUI:
        {
            "hand": ["As", "Kd"],
            "board": ["7h", "8h", "2c"],
            "pot": 150,
            "to_call": 50,
            "active": 3,
            "pos": "BTN"
        }
        y devuelve un BrainResponse sin bloquear el event loop de la interfaz.
        """
        ...


class DummyBrainBackend:
    """
    Esqueleto asíncrono simulado para pruebas de reactividad, latencia y estados
    visuales (FOLD en rojo, CALL/CHECK en verde, RAISE en amarillo parpadeante).
    """

    def __init__(self, simulated_delay_sec: float = 0.15) -> None:
        self.simulated_delay_sec = simulated_delay_sec

    async def evaluate_table_state(self, payload: Dict[str, Any]) -> BrainResponse:
        t0 = time.perf_counter()
        if self.simulated_delay_sec > 0:
            await asyncio.sleep(self.simulated_delay_sec)

        hand = [canonicalize_card(c) for c in payload.get("hand", [])]
        board = [canonicalize_card(c) for c in payload.get("board", [])]
        hero_stack = int(payload.get("stack", 1000))
        pot = int(payload.get("pot", 0))
        to_call = int(payload.get("to_call", 0))
        pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 and to_call > 0 else 0.0

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        # Heurística dummy demostrativa para probar los tres colores en la TUI
        has_ace_or_pair = any(c.startswith(("A", "K")) for c in hand) or (
            len(hand) == 2 and hand[0][0] == hand[1][0]
        )

        if has_ace_or_pair and pot > 0:
            # RAISE / ALL_IN: No podemos apostar más de lo que tenemos en el stack
            raw_raise = max(to_call * 3, int(pot * 0.75), 50)
            if hero_stack <= to_call or raw_raise >= hero_stack:
                return BrainResponse(
                    action="ALL_IN",
                    amount=hero_stack,
                    equity=0.76,
                    pot_odds=pot_odds,
                    reasoning="VALUE_ALL_IN_STACK_COMMITTED_DEMO",
                    latency_ms=elapsed_ms,
                )
            return BrainResponse(
                action="RAISE",
                amount=raw_raise,
                equity=0.76,
                pot_odds=pot_odds,
                reasoning="VALUE_RAISE_DEMO_STRONG_HAND",
                latency_ms=elapsed_ms,
            )
        elif to_call == 0 or (to_call <= max(10, pot * 0.35) and len(hand) == 2):
            # CALL o CHECK (Verde)
            if to_call >= hero_stack:
                return BrainResponse(
                    action="ALL_IN",
                    amount=hero_stack,
                    equity=0.48,
                    pot_odds=pot_odds,
                    reasoning="CALL_ALL_IN_DEMO",
                    latency_ms=elapsed_ms,
                )
            action_type = "CALL" if to_call > 0 else "CHECK"
            return BrainResponse(
                action=action_type,
                amount=to_call,
                equity=0.48,
                pot_odds=pot_odds,
                reasoning="POT_ODDS_FAVORABLE_DEMO",
                latency_ms=elapsed_ms,
            )
        else:
            # FOLD (Rojo)
            return BrainResponse(
                action="FOLD",
                amount=0,
                equity=0.18,
                pot_odds=pot_odds,
                reasoning="EV_NEGATIVE_FOLD_DEMO",
                latency_ms=elapsed_ms,
            )


class LivePokerBotBridge:
    """
    Puente que traduce el payload de la TUI hacia el motor backend existente:
    WatchdogBotClient + InferenceEngine + HeuristicEngine + ExploitativeTracker.
    """

    def __init__(self, db_path: str = "pokerfight_tracker.db") -> None:
        from poker_bot.main import create_bot
        self._bot = create_bot(db_path=db_path)
        self._hand_counter = 0

    async def evaluate_table_state(self, payload: Dict[str, Any]) -> BrainResponse:
        t0 = time.perf_counter()
        self._hand_counter += 1

        hand = [canonicalize_card(c) for c in payload.get("hand", [])]
        board = [canonicalize_card(c) for c in payload.get("board", [])]
        hero_stack = int(payload.get("stack", 1000))
        pot = int(payload.get("pot", 0))
        to_call = int(payload.get("to_call", 0))
        active_count = int(payload.get("active", 6))
        pos = payload.get("pos", "BTN")

        # Determinar calle
        if len(board) >= 5:
            street = "RIVER"
        elif len(board) >= 4:
            street = "TURN"
        elif len(board) >= 3:
            street = "FLOP"
        else:
            street = "PREFLOP"

        # Armar jugadores simulados para el motor
        players = [
            {
                "player_id": "hero",
                "seat": 0,
                "stack": hero_stack,
                "current_bet": 0,
                "is_active": True,
                "position": pos,
            }
        ]
        for i in range(1, active_count):
            players.append(
                {
                    "player_id": f"opp_{i}",
                    "seat": i,
                    "stack": max(1000, pot * 2),
                    "current_bet": to_call,
                    "is_active": True,
                    "position": "UTG",
                }
            )

        # Si hay to_call > 0, registramos que el rival subió
        history = []
        if to_call > 0:
            history.append(
                {
                    "player_id": "opp_1",
                    "street": street,
                    "action_type": "BET" if pot == to_call else "RAISE",
                    "amount": to_call,
                    "pot_before": pot - to_call,
                    "to_call_before": 0,
                }
            )

        raw_state = {
            "hand_id": f"manual_hand_{self._hand_counter}",
            "table_id": "manual_tui_table",
            "hero_id": "hero",
            "street": street,
            "hole_cards": hand,
            "community_cards": board,
            "pot": pot,
            "to_call": to_call,
            "min_raise": max(20, to_call * 2),
            "max_raise": max(1000, pot * 2),
            "small_blind": 10,
            "big_blind": 20,
            "dealer_seat": 0,
            "players": players,
            "action_history": history,
        }

        # Invocar al bot bajo vigilancia del Watchdog
        decision = await self._bot.handle_turn(raw_state)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        pot_odds = to_call / (pot + to_call) if (pot + to_call) > 0 and to_call > 0 else 0.0

        return BrainResponse(
            action=decision.action.value,  # type: ignore
            amount=decision.amount,
            equity=0.0,
            pot_odds=pot_odds,
            reasoning=decision.reasoning,
            latency_ms=decision.latency_ms or elapsed_ms,
        )
