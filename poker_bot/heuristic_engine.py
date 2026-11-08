import random
import threading
import time
from typing import List, Optional, Set, Tuple

from .card_evaluator import (
    RANK_MAP,
    SUIT_MAP,
    evaluate_hand,
    parse_card,
)
from .models import EquityMetrics, GameState, Street

ALL_CARDS_PARSED: List[Tuple[int, int]] = [
    (r_val, s_val) for r_val in RANK_MAP.values() for s_val in SUIT_MAP.values()
]


class HeuristicEngine:
    """
    Motor matemático puro (Stateless).
    Calcula Equity mediante simulación Monte Carlo acotada por tiempo,
    Pot Odds, Stack-to-Pot Ratio (SPR) y Chen Score.
    """

    def __init__(self, default_iterations: int = 10000, batch_size: int = 500) -> None:
        self.default_iterations = default_iterations
        self.batch_size = batch_size

    @staticmethod
    def calculate_pot_odds(to_call: int, current_pot: int) -> float:
        """
        Retorna el porcentaje de equity mínimo requerido para que un call sea EV >= 0.
        Fórmula: to_call / (current_pot + to_call)
        """
        if to_call <= 0:
            return 0.0
        total_pot = current_pot + to_call
        return to_call / total_pot if total_pot > 0 else 0.0

    @staticmethod
    def calculate_spr(effective_stack: int, current_pot: int) -> float:
        """Calcula el Stack-to-Pot Ratio para dimensionar compromiso con el bote."""
        return effective_stack / max(1, current_pot)

    def evaluate_preflop_chen_score(self, hole_cards: List[str]) -> float:
        """
        Fórmula de Bill Chen para fuerza de mano preflop inicial.
        Devuelve una puntuación normalizada entre 0.0 y 1.0.
        """
        if len(hole_cards) < 2:
            return 0.0
        c1, c2 = parse_card(hole_cards[0]), parse_card(hole_cards[1])
        r1, r2 = max(c1[0], c2[0]), min(c1[0], c2[0])
        s1, s2 = c1[1], c2[1]

        # Puntaje por carta más alta
        # 2:1, 3:1.5, ..., T:5, J:6, Q:7, K:8, A:10
        high_scores = {12: 10.0, 11: 8.0, 10: 7.0, 9: 6.0, 8: 5.0}
        score = high_scores.get(r1, (r1 + 2) / 2.0)

        # Si es pareja
        if r1 == r2:
            score = max(5.0, score * 2.0)
            if r1 == 3:  # 55
                score = 6.0
            return min(1.0, score / 20.0)

        # Si son del mismo palo (suited)
        if s1 == s2:
            score += 2.0

        # Gap penalty
        gap = r1 - r2 - 1
        if gap == 1:
            score -= 1.0
        elif gap == 2:
            score -= 2.0
        elif gap == 3:
            score -= 4.0
        elif gap >= 4:
            score -= 5.0

        # Straight bonus for connectores pequeños
        if gap <= 1 and r1 < 10:
            score += 1.0

        return max(0.0, min(1.0, score / 20.0))

    def estimate_range_weighted_monte_carlo(
        self,
        hole_cards: List[str],
        community_cards: List[str],
        num_active_opponents: int,
        opponent_vpip_filter: Optional[float],
        cancel_event: threading.Event,
        max_duration_sec: float = 2.5,
    ) -> Tuple[float, int]:
        """
        Simulación Monte Carlo en micro-lotes para respetar tiempo y cancelación.
        Retorna (equity, total_simulations).
        """
        hero = [parse_card(c) for c in hole_cards]
        board = [parse_card(c) for c in community_cards]
        known_cards = set(hero + board)
        num_opponents = max(1, num_active_opponents)

        deck = [c for c in ALL_CARDS_PARSED if c not in known_cards]
        deck_len = len(deck)

        cards_needed_board = 5 - len(board)
        cards_needed_total = cards_needed_board + (2 * num_opponents)

        if deck_len < cards_needed_total:
            return 0.5, 0

        wins = 0.0
        total_sims = 0
        start_time = time.perf_counter()

        while total_sims < self.default_iterations:
            if cancel_event.is_set():
                break
            if (time.perf_counter() - start_time) >= max_duration_sec:
                break

            for _ in range(self.batch_size):
                # Sample sin reemplazo
                sampled = random.sample(deck, cards_needed_total)
                sim_board = board + sampled[:cards_needed_board]
                
                hero_eval = evaluate_hand(hero + sim_board)

                # Evaluar oponentes
                idx = cards_needed_board
                opp_evals = []
                for _ in range(num_opponents):
                    opp_hole = sampled[idx:idx + 2]
                    idx += 2
                    opp_evals.append(evaluate_hand(opp_hole + sim_board))

                best_opp = max(opp_evals)

                if hero_eval > best_opp:
                    wins += 1.0
                elif hero_eval == best_opp:
                    # Empate (split pot)
                    ties = opp_evals.count(hero_eval) + 1
                    wins += 1.0 / ties

                total_sims += 1

        if total_sims == 0:
            # Fallback si no dio tiempo a completar un solo batch
            preflop_approx = self.evaluate_preflop_chen_score(hole_cards)
            return preflop_approx, 0

        return (wins / total_sims), total_sims

    def evaluate_state(
        self,
        state: GameState,
        target_opponent_vpip: Optional[float],
        cancel_event: threading.Event,
        time_budget_sec: float,
    ) -> EquityMetrics:
        """Fachada principal para obtener métricas matemáticas completas del estado actual."""
        active_opponents = max(1, len([p for p in state.players if p.is_active and p.player_id != state.hero_id]))
        
        # En preflop usamos combinación de Chen Score + Monte Carlo rápido
        # Postflop usamos Monte Carlo directo
        equity, sims = self.estimate_range_weighted_monte_carlo(
            hole_cards=state.hole_cards,
            community_cards=state.community_cards,
            num_active_opponents=active_opponents,
            opponent_vpip_filter=target_opponent_vpip,
            cancel_event=cancel_event,
            max_duration_sec=time_budget_sec,
        )

        pot_odds = self.calculate_pot_odds(state.to_call, state.pot)
        
        # Calcular stack efectivo
        hero_player = next((p for p in state.players if p.player_id == state.hero_id), None)
        hero_stack = hero_player.stack if hero_player else state.max_raise
        spr = self.calculate_spr(hero_stack, state.pot)

        # Expected Value básico del Call = (Equity * (Pot + ToCall)) - ToCall
        ev_call = (equity * (state.pot + state.to_call)) - state.to_call

        # Clasificación heurística
        if equity >= 0.75:
            category = "MONSTER"
        elif equity >= 0.58:
            category = "STRONG"
        elif equity >= 0.42:
            category = "MEDIUM"
        elif equity >= 0.28:
            category = "DRAW"
        else:
            category = "TRASH"

        return EquityMetrics(
            raw_equity=equity,
            pot_odds=pot_odds,
            spr=spr,
            expected_value_call=ev_call,
            simulations_run=sims,
            hand_strength_category=category,
        )
