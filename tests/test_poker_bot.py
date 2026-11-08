"""Suite de verificación para los 4 módulos del bot de poker."""
import asyncio
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from poker_bot.card_evaluator import evaluate_hand, parse_card
from poker_bot.exploitative_tracker import ExploitativeTracker
from poker_bot.heuristic_engine import HeuristicEngine
from poker_bot.inference_engine import InferenceEngine
from poker_bot.models import (
    ActionType,
    GameState,
    PlayerArchetype,
    PlayerState,
    Street,
)
from poker_bot.network_watchdog import WatchdogBotClient


class TestPokerEvaluator(unittest.TestCase):
    def test_hand_evaluations(self):
        # Full house vs Flush
        full_house = [parse_card(c) for c in ["Ah", "As", "Ac", "Kd", "Kh"]]
        flush = [parse_card(c) for c in ["2h", "5h", "9h", "Jh", "Qh"]]
        self.assertGreater(evaluate_hand(full_house), evaluate_hand(flush))

        # Wheel Straight (A-2-3-4-5) vs High Card
        wheel = [parse_card(c) for c in ["Ah", "2d", "3s", "4c", "5h"]]
        high_card = [parse_card(c) for c in ["Ah", "Kd", "9s", "7c", "2s"]]
        self.assertGreater(evaluate_hand(wheel), evaluate_hand(high_card))


class TestHeuristicEngine(unittest.TestCase):
    def setUp(self):
        self.engine = HeuristicEngine(default_iterations=3000, batch_size=300)

    def test_pot_odds(self):
        # to_call = 50, pot = 150 -> pot_odds = 50 / 200 = 0.25 (25%)
        odds = self.engine.calculate_pot_odds(50, 150)
        self.assertAlmostEqual(odds, 0.25, places=3)

    def test_chen_score(self):
        score_aa = self.engine.evaluate_preflop_chen_score(["Ah", "As"])
        score_72 = self.engine.evaluate_preflop_chen_score(["7h", "2c"])
        self.assertGreater(score_aa, score_72)


class TestExploitativeTracker(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_tracker.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.tracker = ExploitativeTracker(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_profiling_and_archetypes(self):
        # Simular 15 manos de un Calling Station (alto VPIP, 0 PFR)
        raw_payload = {
            "hand_id": "hand_test_1",
            "table_id": "tbl_1",
            "hero_id": "hero_1",
            "street": "PREFLOP",
            "hole_cards": ["Ah", "Kd"],
            "community_cards": [],
            "pot": 100,
            "to_call": 20,
            "min_raise": 40,
            "max_raise": 500,
            "players": [
                {"player_id": "rival_station", "seat": 1, "stack": 500, "current_bet": 20, "is_active": True, "position": "BB"}
            ],
            "action_history": [
                {"player_id": "rival_station", "street": "PREFLOP", "action_type": "CALL", "amount": 20, "pot_before": 80, "to_call_before": 20}
            ]
        }
        # Crear 15 manos registradas
        client = WatchdogBotClient(None, None, self.tracker)
        for i in range(15):
            raw_payload["hand_id"] = f"hand_test_{i}"
            state = client.parse_game_state(raw_payload)
            self.tracker.record_game_state_sync(state)

        profiles = self.tracker.get_profiles_for_table(["rival_station"])
        self.assertIn("rival_station", profiles)
        prof = profiles["rival_station"]
        self.assertEqual(prof.hands_observed, 15)
        self.assertGreater(prof.vpip, 0.40)
        self.assertEqual(prof.archetype, PlayerArchetype.CALLING_STATION)


class TestWatchdogClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db_path = "test_watchdog.db"
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.tracker = ExploitativeTracker(db_path=self.db_path)
        self.heuristic = HeuristicEngine(default_iterations=1000, batch_size=200)
        self.inference = InferenceEngine(self.heuristic, self.tracker)

    async def asyncTearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_watchdog_timeout_fallback(self):
        # Configuramos un hard timeout muy corto (0.05s) y forzamos demora en el inference engine
        client = WatchdogBotClient(
            transport=None,
            inference_engine=self.inference,
            tracker=self.tracker,
            hard_timeout=0.05,
            soft_timeout=0.03,
        )

        async def slow_decide(*args, **kwargs):
            await asyncio.sleep(0.20)
            return None

        # Inyectar corrutina lenta
        self.inference.decide_action = slow_decide

        payload = {
            "hand_id": "hand_to_test",
            "hero_id": "hero",
            "to_call": 50,  # Requiere pagar 50 -> Fallback debe ser FOLD
            "pot": 100,
            "hole_cards": ["2c", "7d"],
            "players": [{"player_id": "hero", "seat": 0, "stack": 500, "current_bet": 0, "is_active": True, "position": "SB"}]
        }

        decision = await client.handle_turn(payload)
        self.assertTrue(decision.is_fallback)
        self.assertEqual(decision.action, ActionType.FOLD)
        self.assertIn("TIMEOUT", decision.reasoning)
        client.shutdown()

    async def test_watchdog_free_check_fallback(self):
        client = WatchdogBotClient(
            transport=None,
            inference_engine=self.inference,
            tracker=self.tracker,
            hard_timeout=0.05,
            soft_timeout=0.03,
        )

        async def slow_decide(*args, **kwargs):
            await asyncio.sleep(0.20)
            return None

        self.inference.decide_action = slow_decide

        payload = {
            "hand_id": "hand_to_test_check",
            "hero_id": "hero",
            "to_call": 0,  # Gratis -> Fallback debe ser CHECK
            "pot": 100,
            "hole_cards": ["2c", "7d"],
            "players": [{"player_id": "hero", "seat": 0, "stack": 500, "current_bet": 0, "is_active": True, "position": "BB"}]
        }

        decision = await client.handle_turn(payload)
        self.assertTrue(decision.is_fallback)
        self.assertEqual(decision.action, ActionType.CHECK)
        client.shutdown()


if __name__ == "__main__":
    unittest.main()
