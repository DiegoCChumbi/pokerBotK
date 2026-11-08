import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from .exploitative_tracker import ExploitativeTracker
from .heuristic_engine import HeuristicEngine
from .models import (
    ActionType,
    BotDecision,
    EquityMetrics,
    GameState,
    OpponentProfile,
    PlayerArchetype,
    Street,
)


class InferenceEngine:
    """
    Orquestador de Teoría de Juegos.
    Combina el Equity matemático (Módulo 2) con el perfil explotativo (Módulo 3)
    para ajustar dinámicamente los umbrales de Fold, Call, Value Bet y Bluff.
    """

    def __init__(
        self,
        heuristic_engine: HeuristicEngine,
        tracker: ExploitativeTracker,
    ) -> None:
        self.heuristic = heuristic_engine
        self.tracker = tracker

    async def decide_action(
        self,
        state: GameState,
        executor: ThreadPoolExecutor,
        cancel_event: threading.Event,
        compute_budget_sec: float,
    ) -> BotDecision:
        """Flujo de decisión principal respetando el presupuesto de tiempo."""
        loop = asyncio.get_running_loop()

        # 1. Obtener perfiles de los rivales en la mesa
        opponent_ids = [p.player_id for p in state.players if p.player_id != state.hero_id]
        profiles = self.tracker.get_profiles_for_table(opponent_ids)

        # 2. Identificar al rival clave (el agresor reciente o el de mayor VPIP)
        target_profile = self._identify_primary_opponent(state, profiles)

        # 3. Ejecutar simulación Monte Carlo en el ThreadPoolExecutor
        target_vpip = target_profile.vpip if target_profile else None
        metrics: EquityMetrics = await loop.run_in_executor(
            executor,
            self.heuristic.evaluate_state,
            state,
            target_vpip,
            cancel_event,
            compute_budget_sec,
        )

        # 4. Ajustes explotativos según arquetipo rival
        req_call_equity, val_bet_threshold, bluff_freq = self.compute_exploitative_adjustments(
            state, metrics, target_profile
        )

        # 5. Tomar decisión
        decision = self._resolve_action(
            state=state,
            metrics=metrics,
            target_profile=target_profile,
            req_call_equity=req_call_equity,
            val_bet_threshold=val_bet_threshold,
            bluff_freq=bluff_freq,
        )
        return decision

    def _identify_primary_opponent(
        self, state: GameState, profiles: Dict[str, OpponentProfile]
    ) -> OpponentProfile:
        """Determina cuál es el rival principal en la mano actual."""
        # Si alguien apostó recientemente, ese es el agresor principal
        for act in reversed(state.action_history):
            if act.player_id != state.hero_id and act.action_type in ("BET", "RAISE", "ALL_IN"):
                if act.player_id in profiles:
                    return profiles[act.player_id]

        # De lo contrario, el oponente activo con mayor stack o el primero
        active_opponents = [p for p in state.players if p.is_active and p.player_id != state.hero_id]
        if active_opponents:
            lead_id = active_opponents[0].player_id
            return profiles.get(lead_id, OpponentProfile(
                player_id=lead_id,
                hands_observed=0,
                vpip=0.24,
                pfr=0.16,
                three_bet_pct=0.06,
                aggression_factor=1.5,
                postflop_fold_freq=0.45,
                archetype=PlayerArchetype.UNKNOWN,
                confidence_weight=0.0,
            ))

        # Default fallback
        return OpponentProfile(
            player_id="unknown",
            hands_observed=0,
            vpip=0.24,
            pfr=0.16,
            three_bet_pct=0.06,
            aggression_factor=1.5,
            postflop_fold_freq=0.45,
            archetype=PlayerArchetype.UNKNOWN,
            confidence_weight=0.0,
        )

    def compute_exploitative_adjustments(
        self,
        state: GameState,
        metrics: EquityMetrics,
        target_profile: OpponentProfile,
    ) -> Tuple[float, float, float]:
        """
        Retorna:
        - required_call_equity: Pot odds modificadas por agresividad y rango del oponente.
        - value_bet_threshold: Equity mínima requerida para apostar por valor.
        - bluff_frequency: Probabilidad o propensión al bluff según el fold del rival.
        """
        base_pot_odds = metrics.pot_odds
        arch = target_profile.archetype
        weight = target_profile.confidence_weight

        # Ajuste base
        req_call = base_pot_odds
        val_bet_thresh = 0.58
        bluff_freq = 0.20

        if arch == PlayerArchetype.NIT:
            # Nit apuesta solo con cañones: exigir más equity para pagar (+10%), pero bluffear más fuerte
            req_call += (0.10 * weight)
            val_bet_thresh += (0.05 * weight)
            bluff_freq += (0.35 * weight)

        elif arch == PlayerArchetype.CALLING_STATION:
            # Nunca foldean: pagar con menos margen, value betear fino (+45%), NUNCA bluffear
            req_call -= (0.05 * weight)
            val_bet_thresh -= (0.10 * weight)
            bluff_freq = 0.0

        elif arch == PlayerArchetype.MANIAC:
            # Farolean constantemente: pagar más amplio, inducir bluffs
            req_call -= (0.08 * weight)
            val_bet_thresh -= (0.04 * weight)
            bluff_freq = 0.05

        elif arch == PlayerArchetype.TAG:
            # Juego sólido estándar
            val_bet_thresh = 0.55
            bluff_freq = 0.15

        return max(0.05, req_call), val_bet_thresh, bluff_freq

    def _resolve_action(
        self,
        state: GameState,
        metrics: EquityMetrics,
        target_profile: OpponentProfile,
        req_call_equity: float,
        val_bet_threshold: float,
        bluff_freq: float,
    ) -> BotDecision:
        """Determina la acción legal y dimensiona el sizing óptimo."""
        equity = metrics.raw_equity
        can_check = (state.to_call <= 0)

        # 1. Oportunidad de All-in / Monster Hand / Compromiso de Stack
        if (metrics.spr < 1.8 and equity >= 0.65) or equity >= 0.85:
            # Si el bote ya es grande o la mano es un monstruo
            raise_size = state.max_raise
            if raise_size > state.to_call and raise_size >= state.min_raise:
                return BotDecision(
                    action=ActionType.RAISE,
                    amount=raise_size,
                    reasoning=f"MONSTER_ALLIN (Eq: {equity:.2f}, SPR: {metrics.spr:.2f})",
                )

        # 2. Oportunidad de Value Bet o Raise
        if equity >= val_bet_threshold:
            sizing = self.calculate_optimal_bet_sizing(state, metrics, target_profile, is_value=True)
            if sizing >= state.min_raise and sizing <= state.max_raise:
                return BotDecision(
                    action=ActionType.RAISE,
                    amount=sizing,
                    reasoning=f"VALUE_BET (Eq: {equity:.2f} >= Thresh: {val_bet_threshold:.2f})",
                )
            if not can_check:
                return BotDecision(
                    action=ActionType.CALL,
                    amount=state.to_call,
                    reasoning=f"VALUE_CALL_CAPPED (Eq: {equity:.2f})",
                )

        # 3. Oportunidad de Semi-Bluff o Bluff contra rivales que foldean mucho
        if can_check and equity >= 0.35 and bluff_freq >= 0.30:
            sizing = self.calculate_optimal_bet_sizing(state, metrics, target_profile, is_value=False)
            if sizing >= state.min_raise and sizing <= state.max_raise:
                return BotDecision(
                    action=ActionType.RAISE,
                    amount=sizing,
                    reasoning=f"EXPLOITATIVE_BLUFF (Target: {target_profile.archetype.value})",
                )

        # 4. Decisión frente a apuesta del rival (to_call > 0)
        if not can_check:
            if equity >= req_call_equity:
                return BotDecision(
                    action=ActionType.CALL,
                    amount=state.to_call,
                    reasoning=f"CALL_EV_POSITIVE (Eq: {equity:.2f} >= Req: {req_call_equity:.2f})",
                )
            return BotDecision(
                action=ActionType.FOLD,
                amount=0,
                reasoning=f"FOLD_EV_NEGATIVE (Eq: {equity:.2f} < Req: {req_call_equity:.2f})",
            )

        # 5. Check gratuito por defecto
        return BotDecision(
            action=ActionType.CHECK,
            amount=0,
            reasoning=f"STANDARD_CHECK (Eq: {equity:.2f})",
        )

    def calculate_optimal_bet_sizing(
        self,
        state: GameState,
        metrics: EquityMetrics,
        target_profile: OpponentProfile,
        is_value: bool,
    ) -> int:
        """Calcula el dimensionamiento de apuesta respetando los límites de la mesa."""
        pot = max(1, state.pot)

        if state.street == Street.PREFLOP:
            # 3x BB estándar preflop
            target_amount = state.big_blind * 3 + state.to_call
        else:
            if is_value:
                # Contra Calling Stations cobramos 75%-90% del pot; estándar 60%
                fraction = 0.80 if target_profile.archetype == PlayerArchetype.CALLING_STATION else 0.60
            else:
                # Bluff económico: 40% del pot contra Nits
                fraction = 0.40
            target_amount = state.to_call + int(pot * fraction)

        # Clampear entre min_raise y max_raise
        clamped = max(state.min_raise, min(target_amount, state.max_raise))
        return clamped
