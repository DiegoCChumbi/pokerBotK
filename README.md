# PokerBotK

Bot de **No-Limit Texas Hold'em heads-up** con equity por Monte Carlo, perfilado
explotativo de rivales en SQLite y un *watchdog* que garantiza respuesta antes de
4.5 s. Incluye dos formas de probarlo: una **TUI manual** y un **shadow replay**
sobre un dataset real de partidas entre agentes LLM.

## Componentes

| Paquete | Rol |
|---|---|
| `poker_bot/` | El bot: evaluación de manos, equity, pot odds, perfilado de rivales, decisión y watchdog. |
| `poker_tui/` | Cockpit manual (Textual): introduces el estado de la mesa y consulta la decisión. |
| `poker_benchmark/` | Shadow replay sobre Parquet: latencias, matriz de acuerdo y perfiles descubiertos. |
| `data/kaggle_llm_poker/` | Dataset de partidas heads-up entre 11 agentes LLM (esquema en `data/kaggle_llm_poker/schema.md`). |

## Requisitos

- Python 3.10+
- `textual` (solo para la TUI)
- `pyarrow` (solo para el benchmark)
- SQLite viene con la librería estándar

```bash
pip install textual pyarrow
```

## Arranque rápido

```bash
# 1. Inicializar el bot con transporte mock (no juega solo, solo comprueba el cableado)
python -m poker_bot.main

# 2. Cockpit manual: introduce cartas, pot, stack, to_call y posición
python -m poker_tui            # backend demo (no necesita el motor)
python -m poker_tui --live     # motor real
./run_cockpit.sh --live        # equivalente

# 3. Shadow replay sobre el dataset (100 decisiones en 15 matches)
python -m poker_benchmark --decisions 100 --matches 15
python -m poker_benchmark --agent "Agent 04" --fast
```

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/arquitectura.md`](docs/arquitectura.md) | Componentes, contratos de entrada/salida, flujo de un turno, los dos simuladores y el SLA. |
| [`docs/algoritmos.md`](docs/algoritmos.md) | Catálogo de algoritmos: dónde se usan, para qué, entradas/salidas, umbrales y límites. |
| [`docs/datos.md`](docs/datos.md) | Esquema SQLite del tracker, dataset Parquet, invariantes y cómo leer el informe. |

## Estructura

```
poker_bot/
  models.py                 # dataclasses del dominio (GameState, BotDecision, ...)
  card_evaluator.py         # evaluación de manos 5-7 cartas
  heuristic_engine.py       # Monte Carlo, pot odds, SPR, Chen score
  exploitative_tracker.py   # SQLite + perfilado de rivales
  inference_engine.py       # ajustes explotativos + decisión + sizing
  network_watchdog.py       # transporte, parseo de estado y timeouts
  main.py                   # factoría create_bot()
poker_tui/
  app.py                    # PokerCockpitApp
  backend_bridge.py         # DummyBrainBackend y LivePokerBotBridge
  widgets.py
poker_benchmark/
  parquet_loader.py         # reconstrucción de snapshots sin fuga de cartas
  shadow_evaluator.py       # ejecución del bot + informe
data/kaggle_llm_poker/      # dataset (matches/hands/actions .parquet)
```

## Límites conocidos

- El transporte real no existe: `LocalApiMockTransport` solo registra en log.
- El benchmark **no persiste** las manos en SQLite (el writer en background nunca
  se arranca), así que los perfiles del informe son los priors por defecto.
- El Monte Carlo reparte manos aleatorias a los rivales; no usa rangos.

Detalle y justificación en [`docs/arquitectura.md`](docs/arquitectura.md#9-decisiones-y-límites-conocidos)
y [`docs/algoritmos.md`](docs/algoritmos.md#c-cabos-sueltos-y-discrepancias).
