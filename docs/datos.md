## Datos

Dos fuentes de datos independientes: la **SQLite del tracker** (lo que el bot
aprende mientras juega) y el **dataset Parquet** (lo que el replay usa para
simular). No se mezclan: el replay alimenta al bot, pero no vuelca en la SQLite
salvo que se arranque el writer (ver § 5).

### 1. SQLite del tracker

- **Archivo por defecto:** `pokerfight_tracker.db` (configurable en
  `create_bot(db_path=...)`).
- **Modo:** WAL, `synchronous=NORMAL`, `temp_store=MEMORY`, `foreign_keys=ON`.
- **Quién escribe:** `ExploitativeTracker.record_game_state_sync`, normalmente
  desde `background_writer_loop()` consumiendo una `asyncio.Queue`.
- **Quién lee:** `get_profiles_for_table()` a través de la vista
  `v_opponent_profiles`.

| Tabla | Grano | Contenido |
|---|---|---|
| `hands` | 1 fila por mano | `hand_id` (PK), mesa, ciegas, dealer, nº jugadores, board, bote final, `started_at`. |
| `hand_participants` | 1 por mano y jugador | `(hand_id, player_id)` PK, asiento, posición, stack inicial y flags: `vpip_flag`, `pfr_flag`, `three_bet_*`, `saw_flop_flag`, `went_to_showdown_flag`, `won_hand_flag`. |
| `hand_actions` | 1 por acción | `action_id` autoincremental, calle, `sequence_num`, jugador, tipo, importe, `pot_before`, `to_call_before`. |
| `player_stats_summary` | 1 por jugador | Contadores materializados: manos, VPIP, PFR, 3-bet, acciones postflop, folds, showdown. |

Índices: `(player_id, street)` sobre acciones y `(player_id)` sobre
participantes. La vista `v_opponent_profiles` deriva los porcentajes (fórmulas
en [`algoritmos.md § 8`](algoritmos.md#8-métricas-derivadas-del-tracker-sql)).

#### Cómo se rellenan los flags

`record_game_state_sync` hace *upsert* de la mano, `INSERT OR IGNORE` de los
participantes, inserta el histórico de acciones y, por cada acción preflop:

- `CALL` / `RAISE` / `ALL_IN` → `vpip_flag = 1`.
- `RAISE` / `ALL_IN` → `pfr_flag = 1`.

Después recalcula `player_stats_summary` para todos los participantes de la
mano. Los flags de 3-bet y showdown no se tocan (§ 5).

### 2. Dataset Parquet

- **Directorio:** `data/kaggle_llm_poker/`.
- **Esquema completo y semántica:** [`data/kaggle_llm_poker/schema.md`](../data/kaggle_llm_poker/schema.md).
- **Qué lee el proyecto:**
  - `matches.parquet`: `match_id`, `agent_seat0`, `agent_seat1`, `winner`
    (filtro por `target_agent` y límite `max_matches`).
  - `hands.parquet`: cartas, board, ciegas, stacks, `sb_seat`, ganador y pagos.
  - `actions.parquet`: secuencia de acciones con `phase`, `seat`, `agent`,
    `action_type`, `amount`, `action_idx`.

Contexto que sí importa para interpretar el informe:

- Son partidas **heads-up eliminatorias** entre 11 agentes LLM; las ciegas suben
  durante el match, así que las manos tardías no son comparables con las
  tempranas.
- Todas las cartas están reveladas en el fichero (es una exportación de
  investigación), pero el loader **solo expone al bot lo que sería visible**.
- `fair_deal` solo está presente en ~6 % de las manos; no se usa.

### 3. Reconstrucción de snapshots

Cada `DecisionSnapshot` contiene:

| Campo | Uso |
|---|---|
| `raw_game_state` | JSON exacto que recibe `WatchdogBotClient.handle_turn`. |
| `actual_agent_action`, `actual_agent_amount` | Acción real del agente LLM, para la matriz de acuerdo. |
| `opponent_hole_cards` | Se guarda aparte y **nunca** entra en el estado. |
| `hand_winner`, `payout_acting_seat` | Para evaluar *good folds* y *value raises*. |

Invariante de **no fuga de futuro**:

- `community_cards` = 0 cartas en preflop, 3 en flop, 4 en turn, 5 en river.
- `action_history` contiene solo acciones anteriores al punto de decisión.
- Las ciegas forzadas no generan snapshot, pero sí suman al bote y a
  `to_call`.
- El estado se emite **antes** de aplicar la acción que se va a predecir.

El algoritmo completo está en
[`algoritmos.md § 14`](algoritmos.md#14-reconstrucción-de-snapshots-shadow-replay).

### 4. Cómo leer el informe del benchmark

`python -m poker_benchmark` imprime cuatro bloques:

1. **Rendimiento y latencia.** Media, mediana, P95, P99 y máxima; y
   `timeouts / fallbacks`, que idealmente es 0.
2. **Matriz de acuerdo.** Filas = acción del agente LLM, columnas = acción del
   bot. Mide **coincidencia táctica**, no acierto: una fila `RAISE` con muchas
   entradas en `FOLD` no dice quién jugó mejor.
3. **Auditoría táctica.**
   - *Good fold*: el LLM pagó o subió, perdió la mano y el bot se retiró;
     `to_call` se contabiliza como "fichas salvadas" (teórico, no realizado).
   - *Value raise*: el bot subió y la mano la ganó el agente LLM evaluado.
4. **Perfiles SQLite.** VPIP, PFR, AF, fold % y arquetipo de cada rival. En la
   práctica son los priors, porque el replay no persiste (§ 5).

### 5. Límites conocidos

1. **El replay no escribe en SQLite.** `ShadowReplayRunner` encola estados pero
   nunca arranca `background_writer_loop()` ni llama a
   `record_game_state_sync()`. La cola crece sin consumidor y todos los perfiles
   del informe salen como `UNKNOWN` con los valores por defecto.
2. **Acciones duplicadas si se persiste.** `hand_actions` no tiene restricción
   única y `INSERT OR IGNORE` no puede ignorar nada; reenviar el mismo estado
   (un turno por snapshot) duplica filas y distorsiona AF y fold frequency.
   `hand_participants` sí está protegida por su PK.
3. **Métricas sin alimentar.** `three_bet_count`, `three_bet_opp_count`,
   `saw_flop_flag`, `went_to_showdown_flag`, `wtsd_count` y `wsd_count` no se
   actualizan nunca.
4. **Bases de datos del repo son artefactos.** `pokerfight_tracker.db`,
   `benchmark_shadow.db` y los `test_*.db` se generan al ejecutar; se pueden
   borrar sin perder código. Los `-wal` / `-shm` son parte de SQLite en WAL.
5. **Cada ejecución del benchmark borra su BD.** `ShadowReplayRunner.__init__`
   elimina `benchmark_shadow.db` si existe; el resultado no es acumulativo.
