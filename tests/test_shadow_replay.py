"""Tests para el motor de Shadow Replay y carga de Parquet."""
import asyncio
import os
import unittest

from poker_benchmark.parquet_loader import KaggleParquetLoader, DecisionSnapshot
from poker_benchmark.shadow_evaluator import ShadowReplayRunner


class TestShadowReplay(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.data_dir = "data/kaggle_llm_poker"

    def test_parquet_loader_reconstruction(self):
        loader = KaggleParquetLoader(data_dir=self.data_dir)
        snapshots = list(loader.iter_snapshots(max_matches=2, max_decisions=10))
        self.assertGreater(len(snapshots), 0)

        first = snapshots[0]
        self.assertIsInstance(first, DecisionSnapshot)
        self.assertIn(first.phase, ("preflop", "flop", "turn", "river"))
        self.assertIn("hole_cards", first.raw_game_state)
        self.assertEqual(len(first.raw_game_state["hole_cards"]), 2)
        
        # Verificar que en preflop la mesa no revele cartas futuras
        if first.phase == "preflop":
            self.assertEqual(len(first.raw_game_state["community_cards"]), 0)

    async def test_shadow_replay_runner_execution(self):
        loader = KaggleParquetLoader(data_dir=self.data_dir)
        snapshots = list(loader.iter_snapshots(max_matches=1, max_decisions=5))
        
        runner = ShadowReplayRunner(db_path="test_shadow_runner.db", fast_mode=True)
        report = await runner.run_benchmark(snapshots)
        
        self.assertEqual(report.total_decisions, len(snapshots))
        self.assertEqual(report.watchdog_fallbacks, 0)
        self.assertGreater(len(report.latencies_ms), 0)
        runner.cleanup()


if __name__ == "__main__":
    unittest.main()
