import argparse
import asyncio
import sys
import time

from .parquet_loader import KaggleParquetLoader
from .shadow_evaluator import ShadowReplayRunner


async def main_async() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Modo 2: Shadow Replay & Evaluación Táctica con Dataset Kaggle"
    )
    parser.add_argument(
        "--decisions",
        type=int,
        default=100,
        help="Número total de puntos de decisión reales a evaluar (default: 100)",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=15,
        help="Número de matches a explorar (default: 15)",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        help="Filtrar por un agente LLM específico (ej. 'Agent 01', 'Agent 04')",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Modo rápido con menor número de iteraciones Monte Carlo",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/kaggle_llm_poker",
        help="Directorio donde se encuentran los archivos .parquet",
    )

    args = parser.parse_args()

    print(f"\n[+] Cargando dataset Parquet desde '{args.data_dir}'...")
    t0 = time.perf_counter()
    loader = KaggleParquetLoader(data_dir=args.data_dir)

    print(f"[+] Reconstruyendo estados de mesa cronológicos (Max Decisions: {args.decisions})...")
    snapshots = list(loader.iter_snapshots(
        max_matches=args.matches,
        max_decisions=args.decisions,
        target_agent=args.agent,
    ))
    load_time = time.perf_counter() - t0
    print(f"[+] Se reconstruyeron {len(snapshots)} snapshots de decisión en {load_time:.2f}s.")

    if not snapshots:
        print("[!] No se encontraron snapshots de decisión con los filtros especificados.")
        sys.exit(1)

    print(f"[+] Iniciando Shadow Replay con WatchdogBotClient (Fast Mode: {args.fast})...")
    runner = ShadowReplayRunner(fast_mode=args.fast)

    last_print = 0.0

    def progress(current: int, total: int, decision) -> None:
        nonlocal last_print
        now = time.perf_counter()
        if now - last_print >= 0.5 or current == total:
            last_print = now
            pct = (current / total) * 100
            sys.stdout.write(
                f"\rProgreso: [{current}/{total}] ({pct:.1f}%) | Última decisión: {decision.action.value} ({decision.latency_ms:.1f}ms)   "
            )
            sys.stdout.flush()

    report = await runner.run_benchmark(snapshots, progress_callback=progress)
    print()  # Salto de línea

    report.print_summary()
    runner.cleanup()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
