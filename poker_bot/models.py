from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Street(str, Enum):
    PREFLOP = "PREFLOP"
    FLOP = "FLOP"
    TURN = "TURN"
    RIVER = "RIVER"


class ActionType(str, Enum):
    FOLD = "FOLD"
    CHECK = "CHECK"
    CALL = "CALL"
    RAISE = "RAISE"
    ALL_IN = "ALL_IN"


class PlayerArchetype(str, Enum):
    UNKNOWN = "UNKNOWN"           # < 15 manos observadas (Jugar baseline GTO/Heurístico)
    NIT = "NIT"                   # VPIP < 16%, PFR < 12% (Rango ultra fuerte, foldea mucho)
    TAG = "TAG"                   # VPIP 17-25%, PFR 14-21% (Tight-Aggressive sólido)
    LAG = "LAG"                   # VPIP 26-38%, PFR 20-32% (Loose-Aggressive, amplios bluffs)
    CALLING_STATION = "STATION"   # VPIP > 35%, PFR < 12%, AF < 1.2 (No bluffear, value bet fino)
    MANIAC = "MANIAC"             # VPIP > 45%, PFR > 30%, AF > 3.0 (Inducir bluffs, pagar más amplio)


@dataclass(frozen=True)
class PlayerState:
    player_id: str
    seat: int
    stack: int
    current_bet: int
    is_active: bool               # Sigue en la mano (no ha hecho fold)
    is_all_in: bool
    position: str                 # 'SB', 'BB', 'UTG', 'MP', 'CO', 'BTN'


@dataclass(frozen=True)
class ActionEvent:
    player_id: str
    street: Street
    action_type: str
    amount: int
    pot_before: int
    to_call_before: int


@dataclass(frozen=True)
class GameState:
    hand_id: str
    table_id: str
    hero_id: str
    street: Street
    hole_cards: List[str]         # Ej: ["Ah", "Kd"]
    community_cards: List[str]    # Ej: ["7s", "8s", "2c"]
    pot: int
    to_call: int
    min_raise: int
    max_raise: int                # Generalmente el stack efectivo del Hero
    small_blind: int
    big_blind: int
    dealer_seat: int
    players: List[PlayerState]
    action_history: List[ActionEvent] = field(default_factory=list)


@dataclass(frozen=True)
class EquityMetrics:
    raw_equity: float             # 0.0 a 1.0 (Probabilidad de ganar/empatar en showdown)
    pot_odds: float               # to_call / (pot + to_call)
    spr: float                    # Stack-to-Pot Ratio (effective_stack / pot)
    expected_value_call: float    # EV matemático puro de hacer call
    simulations_run: int          # Cantidad de iteraciones Monte Carlo ejecutadas
    hand_strength_category: str   # Ej: 'MONSTER', 'STRONG', 'MEDIUM', 'DRAW', 'TRASH'


@dataclass(frozen=True)
class OpponentProfile:
    player_id: str
    hands_observed: int
    vpip: float                   # Default prior: 0.24
    pfr: float                    # Default prior: 0.16
    three_bet_pct: float          # Default prior: 0.06
    aggression_factor: float      # Default prior: 1.5
    postflop_fold_freq: float     # Default prior: 0.45
    archetype: PlayerArchetype
    confidence_weight: float      # 0.0 a 1.0 según tamaño de muestra (hands_observed / 50)


@dataclass(frozen=True)
class BotDecision:
    action: ActionType
    amount: int = 0
    reasoning: str = ""
    is_fallback: bool = False
    latency_ms: float = 0.0
