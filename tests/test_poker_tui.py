"""Tests unitarios para la arquitectura de la TUI de PokerFight."""
import asyncio
import unittest

from poker_tui.backend_bridge import DummyBrainBackend, LivePokerBotBridge
from poker_tui.widgets import CardInput, VALID_RANKS, VALID_SUITS


class TestTuiWidgetsAndValidation(unittest.TestCase):
    def test_valid_ranks_and_suits(self):
        self.assertIn("A", VALID_RANKS)
        self.assertIn("T", VALID_RANKS)
        self.assertIn("2", VALID_RANKS)
        self.assertIn("s", VALID_SUITS)
        self.assertIn("h", VALID_SUITS)
        self.assertIn("d", VALID_SUITS)
        self.assertIn("c", VALID_SUITS)

    def test_card_normalization_helper(self):
        from poker_tui.widgets import format_card_visual
        from poker_tui.backend_bridge import canonicalize_card

        # En la UI: '3t' es 3 de Trébol y debe renderizar 3♣
        self.assertEqual(format_card_visual("3t"), "[bold white]3♣[/bold white]")
        # Para el motor: '3t' se traduce al canónico '3c' (Clubs)
        self.assertEqual(canonicalize_card("3t"), "3c")

        # En la UI: '3c' es 3 de Corazones y debe renderizar 3♥
        self.assertEqual(format_card_visual("3c"), "[bold red]3♥[/bold red]")
        # Para el motor: '3c' se traduce al canónico '3h' (Hearts)
        self.assertEqual(canonicalize_card("3c"), "3h")

        # En la UI: 'ae' es As de Espadas y debe renderizar A♠
        self.assertEqual(format_card_visual("ae"), "[bold white]A♠[/bold white]")
        self.assertEqual(canonicalize_card("ae"), "As")

        # En la UI: 'qd' es Reina de Diamantes y debe renderizar Q♦
        self.assertEqual(format_card_visual("qd"), "[bold red]Q♦[/bold red]")
        self.assertEqual(canonicalize_card("qd"), "Qd")


class TestDummyBrainBackend(unittest.IsolatedAsyncioTestCase):
    async def test_dummy_raise_state(self):
        backend = DummyBrainBackend(simulated_delay_sec=0.01)
        payload = {
            "hand": ["As", "Kd"],
            "board": ["Ah", "7c", "2s"],
            "pot": 200,
            "to_call": 50,
            "active": 4,
            "pos": "CO",
        }
        resp = await backend.evaluate_table_state(payload)
        self.assertEqual(resp.action, "RAISE")
        self.assertGreater(resp.amount, 50)
        self.assertGreater(resp.equity, 0.70)
        self.assertAlmostEqual(resp.pot_odds, 50 / 250, places=2)

    async def test_dummy_fold_state(self):
        backend = DummyBrainBackend(simulated_delay_sec=0.01)
        payload = {
            "hand": ["2c", "7d"],
            "board": ["As", "Kh", "Qd"],
            "pot": 100,
            "to_call": 80,
            "active": 3,
            "pos": "SB",
        }
        resp = await backend.evaluate_table_state(payload)
        self.assertEqual(resp.action, "FOLD")
        self.assertEqual(resp.amount, 0)

    async def test_dummy_call_check_state(self):
        backend = DummyBrainBackend(simulated_delay_sec=0.01)
        payload = {
            "hand": ["8s", "9s"],
            "board": [],
            "pot": 100,
            "to_call": 0,
            "active": 6,
            "pos": "BTN",
        }
        resp = await backend.evaluate_table_state(payload)
        self.assertEqual(resp.action, "CHECK")
        self.assertEqual(resp.amount, 0)


class TestLivePokerBotBridge(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_integration_with_watchdog_bot(self):
        bridge = LivePokerBotBridge(db_path="test_bridge.db")
        payload = {
            "hand": ["Ah", "As"],
            "board": ["2c", "7d", "9h"],
            "pot": 120,
            "to_call": 30,
            "active": 4,
            "pos": "BTN",
        }
        resp = await bridge.evaluate_table_state(payload)
        self.assertIn(resp.action, ("RAISE", "CALL", "ALL_IN", "CHECK", "FOLD"))
        self.assertGreater(resp.latency_ms, 0)
        # Limpiar db temporal si existe
        import os
        if os.path.exists("test_bridge.db"):
            os.remove("test_bridge.db")


if __name__ == "__main__":
    unittest.main()
