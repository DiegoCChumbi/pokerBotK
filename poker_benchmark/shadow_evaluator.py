import asyncio
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from poker_bot.exploitative_tracker import ExploitativeTracker
from poker_bot.heuristic_engine import HeuristicEngine
from poker_bot.inference_engine import InferenceEngine
from poker_bot.models import ActionType, BotDecision
from poker_bot.network_watchdog import WatchdogBotClient
from .parquet_loader import DecisionSnapshot, KaggleParquetLoader


@dataclass
class ShadowBenchmarkReport:
    total_decisions: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    watchdog_fallbacks: int = 0
    
    # Matriz de acuerdo: matriz[llm_action][bot_action] = count
    action_matrix: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    # Análisis táctico (Good Folds & Value Raises)
    good_folds_count: int = 0
    good_folds_saved_chips: int = 0
    value_raises_count: int = 0
    
    # Perfilado SQLite de rivales encontrados
    agent_profiles: Dict[str, Any] = field(default_factory=dict)

    def record_decision(
        self,
        snapshot: DecisionSnapshot,
        decision: BotDecision,
    ) -> None:
        self.total_decisions += 1
        self.latencies_ms.append(decision.latency_ms)
        if decision.is_fallback:
            self.watchdog_fallbacks += 1

        llm_act = snapshot.actual_agent_action
        bot_act = decision.action.value

        if llm_act not in self.action_matrix:
            self.action_matrix[llm_act] = {}
        self.action_matrix[llm_act][bot_act] = self.action_matrix[llm_act].get(bot_act, 0) + 1

        # Auditoría de Good Fold:
        # El LLM pagó o subió, pero perdió la mano (payout == 0 y ganador fue el rival).
        # Nuestro bot inteligentemente eligió FOLD, ahorrándose las fichas del to_call.
        acting_lost = (snapshot.hand_winner == snapshot.opponent_agent)
        if llm_act in ("CALL", "RAISE") and bot_act == "FOLD" and acting_lost:
            saved = snapshot.raw_game_state.get("to_call", 0)
            if saved > 0:
                self.good_folds_count += 1
                self.good_folds_saved_chips += saved

        # Auditoría de Value Raise:
        # Nuestro bot subió (RAISE) y el agente tenía la mano ganadora
        acting_won = (snapshot.hand_winner == snapshot.acting_agent)
        if bot_act == "RAISE" and acting_won:
            self.value_raises_count += 1

    def print_summary(self) -> None:
        print("\n" + "=" * 70)
        print("          REPORTE DE BENCHMARK: SHADOW REPLAY (MODO 2)")
        print("=" * 70)

        # 1. Latencia y SLA Watchdog
        if self.latencies_ms:
            p50 = statistics.median(self.latencies_ms)
            sorted_lat = sorted(self.latencies_ms)
            p95_idx = int(len(sorted_lat) * 0.95)
            p99_idx = int(len(sorted_lat) * 0.99)
            p95 = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]
            p99 = sorted_lat[min(p99_idx, len(sorted_lat) - 1)]
            avg_lat = statistics.mean(self.latencies_ms)
            max_lat = max(self.latencies_ms)
        else:
            p50 = p95 = p99 = avg_lat = max_lat = 0.0

        print("\n[1] RENDIMIENTO & LATENCIA (SLA Watchdog < 4500 ms):")
        print(f"  • Decisiones evaluadas:     {self.total_decisions}")
        print(f"  • Timeouts / Fallbacks:     {self.watchdog_fallbacks} (0% tolerado)")
        print(f"  • Latencia Media:           {avg_lat:.2f} ms")
        print(f"  • Latencia Mediana (P50):   {p50:.2f} ms")
        print(f"  • Latencia P95:             {p95:.2f} ms")
        print(f"  • Latencia P99:             {p99:.2f} ms")
        print(f"  • Latencia Máxima:          {max_lat:.2f} ms")

        # 2. Matriz de Acuerdo Táctico
        print("\n[2] MATRIZ DE ACUERDO TÁCTICO (LLM Dataset vs Nuestro Bot):")
        bot_actions = ["FOLD", "CHECK", "CALL", "RAISE"]
        header = f"  {'Acción LLM':<12} | " + " | ".join([f"{a:>7}" for a in bot_actions])
        print(header)
        print("  " + "-" * len(header))

        for llm_act, targets in sorted(self.action_matrix.items()):
            row_vals = [str(targets.get(a, 0)) for a in bot_actions]
            print(f"  {llm_act:<12} | " + " | ".join([f"{v:>7}" for v in row_vals]))

        # 3. Auditoría de Calidad Matemática
        print("\n[3] AUDITORÍA TÁCTICA & GESTIÓN DE RIESGO:")
        print(f"  • 'Good Folds' detectados:   {self.good_folds_count}")
        print(f"    (Manos donde el LLM pagó y perdió, pero nuestro Bot foldeó correctamente)")
        print(f"  • Fichas teóricas salvadas: {self.good_folds_saved_chips:,} chips")
        print(f"  • 'Value Raises' ganadores: {self.value_raises_count}")

        # 4. Perfiles SQLite descubiertos
        if self.agent_profiles:
            print("\n[4] PERFILADO EXPLOTATIVO SQLITE (Agentes rivales identificados):")
            print(f"  {'Agente LLM':<12} | {'VPIP':<8} | {'PFR':<8} | {'AF':<6} | {'Fold%':<8} | {'Arquetipo':<12}")
            print("  " + "-" * 66)
            for agent_name, prof in sorted(self.agent_profiles.items()):
                print(
                    f"  {agent_name:<12} | {prof.vpip*100:>6.1f}% | {prof.pfr*100:>6.1f}% | "
                    f"{prof.aggression_factor:>6.2f} | {prof.postflop_fold_freq*100:>6.1f}% | {prof.archetype.value:<12}"
                )
        print("=" * 70 + "\n")


class ShadowReplayRunner:
    """
    Ejecuta el bot sobre snapshots reales mientras alimenta el tracker en SQLite.
    """

    def __init__(
        self,
        db_path: str = "benchmark_shadow.db",
        fast_mode: bool = False,
    ) -> None:
        self.db_path = db_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        self.tracker = ExploitativeTracker(db_path=self.db_path)
        # En fast_mode usamos menos iteraciones Monte Carlo para acelerar benchmarks largos
        iterations = 1500 if fast_mode else 4000
        self.heuristic = HeuristicEngine(default_iterations=iterations, batch_size=300)
        self.inference = InferenceEngine(heuristic_engine=self.heuristic, tracker=self.tracker)
        self.bot = WatchdogBotClient(
            transport=None,
            inference_engine=self.inference,
            tracker=self.tracker,
            hard_timeout=4.50,
            soft_timeout=3.80,
        )

    async def run_benchmark(
        self,
        snapshots: List[DecisionSnapshot],
        progress_callback: Optional[callable] = None,
    ) -> ShadowBenchmarkReport:
        report = ShadowBenchmarkReport()
        agents_seen = set()

        for idx, snap in enumerate(snapshots):
            agents_seen.add(snap.opponent_agent)
            
            # Enviar al WatchdogBotClient exactamente como un turno real
            decision = await self.bot.handle_turn(snap.raw_game_state)
            report.record_decision(snap, decision)

            if progress_callback:
                progress_callback(idx + 1, len(snapshots), decision)

        # Extraer perfiles finales de los agentes vistos en SQLite
        if agents_seen:
            report.agent_profiles = self.tracker.get_profiles_for_table(list(agents_seen))

        self.bot.shutdown()
        return report

    def cleanup(self) -> None:
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
