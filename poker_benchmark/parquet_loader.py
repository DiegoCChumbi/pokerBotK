from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
import pyarrow.parquet as pq


@dataclass(frozen=True)
class DecisionSnapshot:
    """Representa un punto exacto de decisión en una mano real del dataset."""
    match_id: str
    hand_number: int
    action_idx: int
    phase: str                          # 'preflop', 'flop', 'turn', 'river'
    acting_seat: int                    # 0 o 1
    acting_agent: str                   # 'Agent 01', etc.
    opponent_agent: str
    raw_game_state: Dict[str, Any]      # Payload para WatchdogBotClient.handle_turn()
    actual_agent_action: str            # 'fold', 'check', 'call', 'raise'
    actual_agent_amount: int            # Chips comprometidos por la acción real
    opponent_hole_cards: List[str]      # Cartas del oponente (ocultas para el bot en la decisión)
    hand_winner: str                    # Ganador real de la mano ('Agent 01', 'split', etc.)
    payout_acting_seat: float           # Pago real recibido por el agente al terminar la mano


class KaggleParquetLoader:
    """
    Carga y reconstruye progresivamente estados de mesa desde actions.parquet y hands.parquet
    sin filtración de cartas futuras.
    """

    def __init__(self, data_dir: str = "data/kaggle_llm_poker") -> None:
        self.data_dir = Path(data_dir)
        self.matches_path = self.data_dir / "matches.parquet"
        self.hands_path = self.data_dir / "hands.parquet"
        self.actions_path = self.data_dir / "actions.parquet"

        if not self.hands_path.exists() or not self.actions_path.exists():
            raise FileNotFoundError(f"Dataset Parquet no encontrado en {self.data_dir}")

    def iter_snapshots(
        self,
        max_matches: int = 10,
        max_decisions: Optional[int] = None,
        target_agent: Optional[str] = None,
    ) -> Iterator[DecisionSnapshot]:
        """
        Itera cronológicamente reconstruyendo cada momento de decisión en el que un bot
        debe elegir entre FOLD, CHECK, CALL o RAISE (ignora los posts forzados de ciegas).
        """
        # 1. Leer matches ordenados
        matches_table = pq.read_table(
            self.matches_path,
            columns=["match_id", "agent_seat0", "agent_seat1", "winner"]
        ).to_pylist()

        if target_agent:
            matches_table = [
                m for m in matches_table
                if m["agent_seat0"] == target_agent or m["agent_seat1"] == target_agent
            ]

        selected_matches = [m["match_id"] for m in matches_table[:max_matches]]
        if not selected_matches:
            return

        # 2. Leer hands para los matches seleccionados
        hands_filter = [("match_id", "in", selected_matches)]
        hands_table = pq.read_table(self.hands_path, filters=hands_filter).to_pylist()
        hands_dict = {(h["match_id"], h["hand_number"]): h for h in hands_table}

        # 3. Leer actions para los matches seleccionados
        actions_filter = [("match_id", "in", selected_matches)]
        actions_table = pq.read_table(self.actions_path, filters=actions_filter).to_pylist()

        # Agrupar acciones por mano
        actions_by_hand: Dict[tuple, List[dict]] = {}
        for act in actions_table:
            key = (act["match_id"], act["hand_number"])
            if key not in actions_by_hand:
                actions_by_hand[key] = []
            actions_by_hand[key].append(act)

        decisions_emitted = 0

        # Iterar en orden cronológico por match y hand
        for match_id in selected_matches:
            match_hands = [
                h for h in hands_table if h["match_id"] == match_id
            ]
            match_hands.sort(key=lambda x: x["hand_number"])

            for hand_meta in match_hands:
                hand_num = hand_meta["hand_number"]
                hand_key = (match_id, hand_num)
                actions = actions_by_hand.get(hand_key, [])
                actions.sort(key=lambda x: x["action_idx"])

                # Descomponer cartas
                hole0 = hand_meta["hole_seat0"].split() if hand_meta.get("hole_seat0") else []
                hole1 = hand_meta["hole_seat1"].split() if hand_meta.get("hole_seat1") else []
                board_tokens = hand_meta["board"].split() if hand_meta.get("board") else []

                sb_seat = int(hand_meta["sb_seat"]) if hand_meta.get("sb_seat") is not None else 0
                bb_seat = 1 - sb_seat

                small_blind = int(hand_meta.get("small_blind", 10))
                big_blind = int(hand_meta.get("big_blind", 20))

                # Seguimiento dinámico de inversiones y fichas
                starting_stacks = {
                    0: int(hand_meta.get("stack_seat0", 1000)),
                    1: int(hand_meta.get("stack_seat1", 1000)),
                }
                total_committed = {0: 0, 1: 0}
                street_committed = {0: 0, 1: 0}
                current_phase = "preflop"
                action_history_accumulator: List[dict] = []

                for act in actions:
                    phase = act["phase"].lower()
                    act_type = act["action_type"].lower()
                    seat = int(act["seat"])
                    opp_seat = 1 - seat
                    amount = int(act.get("amount", 0))

                    # Si cambió de calle, reiniciar apuestas de la calle
                    if phase != current_phase:
                        current_phase = phase
                        street_committed = {0: 0, 1: 0}

                    # Las acciones post_small_blind y post_big_blind no son decisiones opcionales
                    is_decision_point = act_type not in ("post_small_blind", "post_big_blind")

                    # Determinar cartas comunitarias visibles según la fase actual
                    if phase == "preflop":
                        visible_board = []
                    elif phase == "flop":
                        visible_board = board_tokens[:3]
                    elif phase == "turn":
                        visible_board = board_tokens[:4]
                    else:  # river
                        visible_board = board_tokens[:5]

                    acting_hole = hole0 if seat == 0 else hole1
                    opp_hole = hole1 if seat == 0 else hole0

                    pot_before = sum(total_committed.values())
                    to_call = max(0, street_committed[opp_seat] - street_committed[seat])

                    # Stacks restantes antes de actuar
                    cur_stack_acting = max(0, starting_stacks[seat] - total_committed[seat])
                    cur_stack_opp = max(0, starting_stacks[opp_seat] - total_committed[opp_seat])

                    # Mapear posiciones Heads-Up: SB es Button (Dealer)
                    pos_str = "BTN" if seat == sb_seat else "BB"
                    opp_pos_str = "BB" if seat == sb_seat else "BTN"

                    raw_state = {
                        "hand_id": f"{match_id}_h{hand_num}_a{act['action_idx']}",
                        "table_id": match_id,
                        "hero_id": act["agent"],
                        "street": phase.upper(),
                        "hole_cards": acting_hole,
                        "community_cards": visible_board,
                        "pot": pot_before,
                        "to_call": to_call,
                        "min_raise": max(big_blind, to_call * 2),
                        "max_raise": cur_stack_acting,
                        "small_blind": small_blind,
                        "big_blind": big_blind,
                        "dealer_seat": sb_seat,
                        "players": [
                            {
                                "player_id": act["agent"],
                                "seat": seat,
                                "stack": cur_stack_acting,
                                "current_bet": street_committed[seat],
                                "is_active": True,
                                "position": pos_str,
                            },
                            {
                                "player_id": hand_meta[f"agent_seat{opp_seat}"],
                                "seat": opp_seat,
                                "stack": cur_stack_opp,
                                "current_bet": street_committed[opp_seat],
                                "is_active": True,
                                "position": opp_pos_str,
                            },
                        ],
                        "action_history": list(action_history_accumulator),
                    }

                    if is_decision_point:
                        yield DecisionSnapshot(
                            match_id=match_id,
                            hand_number=hand_num,
                            action_idx=int(act["action_idx"]),
                            phase=phase,
                            acting_seat=seat,
                            acting_agent=act["agent"],
                            opponent_agent=hand_meta[f"agent_seat{opp_seat}"],
                            raw_game_state=raw_state,
                            actual_agent_action=act_type.upper(),
                            actual_agent_amount=amount,
                            opponent_hole_cards=opp_hole,
                            hand_winner=hand_meta.get("winner", "split"),
                            payout_acting_seat=float(hand_meta.get(f"payout_seat{seat}", 0.0)),
                        )
                        decisions_emitted += 1
                        if max_decisions and decisions_emitted >= max_decisions:
                            return

                    # Actualizar estado tras la acción
                    street_committed[seat] += amount
                    total_committed[seat] += amount
                    action_history_accumulator.append({
                        "player_id": act["agent"],
                        "street": phase.upper(),
                        "action_type": act_type.upper(),
                        "amount": amount,
                        "pot_before": pot_before,
                        "to_call_before": to_call,
                    })
