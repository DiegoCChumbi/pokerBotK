"""Motor de evaluación de manos de Poker Texas Hold'em de 5 a 7 cartas (Pure Python).

Permite evaluar y comparar manos sin dependencias externas como treys o eval7,
garantizando compatibilidad universal con entornos estándar de Python.
"""
from itertools import combinations
from typing import List, Tuple

# Representación interna:
# Rangos: '2':0, ..., '9':7, 'T':8, 'J':9, 'Q':10, 'K':11, 'A':12
# Palos: 's':0, 'h':1, 'd':2, 'c':3
RANK_MAP = {r: i for i, r in enumerate("23456789TJQKA")}
SUIT_MAP = {"s": 0, "h": 1, "d": 2, "c": 3}

# Tipos de mano ordenados de menor a mayor
HIGH_CARD = 0
ONE_PAIR = 1
TWO_PAIR = 2
THREE_OF_A_KIND = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
FOUR_OF_A_KIND = 7
STRAIGHT_FLUSH = 8


def parse_card(card_str: str) -> Tuple[int, int]:
    """Convierte 'Ah' en (12, 1), 'Td' en (8, 2), etc."""
    r_char = card_str[0].upper()
    s_char = card_str[1].lower()
    return (RANK_MAP[r_char], SUIT_MAP[s_char])


def evaluate_5card(cards: List[Tuple[int, int]]) -> Tuple[int, List[int]]:
    """
    Evalúa exactamente 5 cartas y devuelve una tupla comparable:
    (hand_category, tie_breakers_en_orden_descendente)
    """
    ranks = [c[0] for c in cards]
    suits = [c[1] for c in cards]
    
    is_flush = len(set(suits)) == 1

    # Conteo de rangos
    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1

    # Detectar escalera
    unique_ranks = sorted(list(set(ranks)), reverse=True)
    is_straight = False
    straight_high = -1
    
    if len(unique_ranks) == 5:
        if unique_ranks[0] - unique_ranks[4] == 4:
            is_straight = True
            straight_high = unique_ranks[0]
        elif unique_ranks == [12, 3, 2, 1, 0]:  # A-2-3-4-5 (Wheel)
            is_straight = True
            straight_high = 3  # El 5 es la carta alta

    # Escalera de color / Real
    if is_flush and is_straight:
        return (STRAIGHT_FLUSH, [straight_high])

    # Ordenar por frecuencia y luego por rango
    # Por ej. Full House (K, K, K, 2, 2) -> [(3, 11), (2, 0)]
    counts_sorted = sorted([(count, rank) for rank, count in rank_counts.items()], reverse=True)
    freq_pattern = [item[0] for item in counts_sorted]
    ranks_pattern = [item[1] for item in counts_sorted]

    if freq_pattern == [4, 1]:
        return (FOUR_OF_A_KIND, ranks_pattern)
    
    if freq_pattern == [3, 2]:
        return (FULL_HOUSE, ranks_pattern)

    if is_flush:
        return (FLUSH, sorted(ranks, reverse=True))

    if is_straight:
        return (STRAIGHT, [straight_high])

    if freq_pattern == [3, 1, 1]:
        return (THREE_OF_A_KIND, ranks_pattern)

    if freq_pattern == [2, 2, 1]:
        return (TWO_PAIR, ranks_pattern)

    if freq_pattern == [2, 1, 1, 1]:
        return (ONE_PAIR, ranks_pattern)

    return (HIGH_CARD, ranks_pattern)


def evaluate_hand(cards: List[Tuple[int, int]]) -> Tuple[int, List[int]]:
    """Evalúa de 5 a 7 cartas eligiendo la mejor combinación posible de 5."""
    if len(cards) == 5:
        return evaluate_5card(cards)
    best_score = None
    for combo in combinations(cards, 5):
        score = evaluate_5card(list(combo))
        if best_score is None or score > best_score:
            best_score = score
    return best_score
