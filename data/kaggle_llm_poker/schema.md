# Chipzen LLM-Agent Heads-Up Poker Dataset — Schema

Complete hand histories of rated heads-up No-Limit Hold'em matches played on
[chipzen.ai](https://chipzen.ai) between 11 anonymized house LLM-authored poker
agents ("Agent 01" … "Agent 11"). Every match is bot-vs-bot, elimination
format (play until one seat busts), server-dealt with a verifiable fair-deal
proof. Snapshot window: earliest in-scope match **2026-06-22** (the house
agent account's first match) through 2026-08-19 23:59:59 UTC.

## Anonymization (a *task device*, not a security boundary)

The agent labels exist so the "Who's playing?" benchmark has ground-truth
identities. They are **not** a privacy guarantee, and the mapping is **fully
recoverable by an outsider**: the underlying bots are public — per-bot
volumes and win/loss records are one anonymous API call away, and the
platform's public match replays expose the **same hole cards with real bot
names**, giving an exact join back to every match in this dataset. Treat the
identification task as an **open-book learning exercise**, not a sealed
competition: the interesting question is what a *model* can learn from K
hands of behavior, not whether a determined human can look up the answer
(they can).

- Each of the 11 agents is a distinct bot; the label assignment is random and
  fixed across the whole dataset (the same agent label always refers to the
  same underlying bot). Bot names, ids, versions, owner ids, and ratings are
  removed.
- Match UUIDs are replaced with sequential ids `match_0001`… ordered by match
  start time. Within-day ordering is fully preserved by this id.
- Match timestamps are coarsened to a DATE (`started_date`, no time) and the
  exact `completed_at` is dropped — a courtesy that keeps casual exact-time
  joins out of the published files, nothing more (see above: the mapping is
  recoverable regardless). No hand-level or action-level timestamps are
  published.
- The per-hand SHA-256 audit hash chain (`hash`, `prev_hash`,
  `match_checksum`) present in the platform's raw export is **not** included:
  the chain's genesis value is `SHA-256(match_id)` and every hash preimage
  embeds the original match UUID, so publishing the chain would allow
  re-identification of matches (and thus agents) via the platform's public
  match pages. The hashes still exist server-side and verify against the
  original (private) records.
- The `fair_deal` proof (seed commitment, revealed seed, nonce, deck order)
  contains no identity data and is kept where the engine recorded it
  (`hands.fair_deal`, ~6% of hands — see below).

## Ratedness

The platform's export lines do not carry the `rated` flag. Ratedness was
determined per match via the platform match API (the `rated` field, i.e. the
`Match.rated` database column that gates Glicko-2 rating updates). Only
`rated = true`, non-tournament, completed heads-up matches with both seats
owned by the house LLM-agent account are included.

## Files

### `matches.parquet` — one row per match

| column | type | description |
|---|---|---|
| `match_id` | string | Sequential anonymized id, `match_0001`…, assigned in match-start order. Use it (not a timestamp) to order matches. |
| `started_date` | string | Match start **date** only, `YYYY-MM-DD` (UTC). Exact time deliberately removed — see Anonymization. |
| `hands` | int64 | Number of hands in the match. |
| `agent_seat0` | string | Agent label in seat 0. |
| `agent_seat1` | string | Agent label in seat 1. |
| `net_chips_seat0` | float | Net chips won (+) / lost (−) by seat 0 over the match. |
| `net_chips_seat1` | float | Net chips for seat 1 (= −seat0 in elimination HU). |
| `hands_won_seat0` | int64 | Hands won by seat 0. |
| `hands_won_seat1` | int64 | Hands won by seat 1. |
| `winner` | string | Agent label with positive net chips, or `draw`. Never null. |

*(There is no `completed_at` column — it was dropped, see Anonymization.)*

### `hands.parquet` — one row per hand

| column | type | description |
|---|---|---|
| `match_id` | string | Join key to `matches.parquet`. |
| `hand_number` | int | 1-based hand index within the match. |
| `agent_seat0`, `agent_seat1` | string | Agent labels (constant within a match). |
| `sb_seat` | Int64 | Seat that posted the small blind (button in heads-up; alternates). Nullable (Int64) to tolerate a rare missing blind post; effectively always 0/1. |
| `small_blind`, `big_blind` | int64 | Blind levels for this hand (blinds escalate during a match — see the drift caveat below). |
| `stack_seat0`, `stack_seat1` | int64 | Chip stacks at the start of the hand. |
| `hole_seat0`, `hole_seat1` | string | Hole cards, space-separated, e.g. `"9s Ts"` (both seats always revealed — this is a research export, not a live feed). |
| `board` | string | Community cards dealt, **space-separated 2-char tokens** e.g. `"9d 6c 5d Td Ad"` (same format as `hole_*`); None if the hand ended preflop with no board. Tokens are flop (3), turn, river in order. |
| `pot` | float | Final **contested** pot size (uncalled-bet refunds excluded). |
| `winner` | string | Agent label of the hand winner, `split` for split pots. Null only if a hand had no contested pot (not observed in this snapshot — 0 nulls). |
| `payout_seat0`, `payout_seat1` | float | Chips paid to each seat at hand end. Includes returned uncalled bets / all-in refunds, so `payout_seat0 + payout_seat1 >= pot` (strictly greater on ~27% of hands). |
| `showdown` | bool | True if the hand reached showdown (i.e. was **not** ended by a fold). Derived from the action sequence — the platform's raw `result.showdown` card-reveal array is populated on every hand in this research export, folds included, and is not a went-to-showdown flag. |
| `n_actions` | int64 | Number of action rows for this hand in `actions.parquet`. |
| `engine_version` | string | Server engine version that dealt the hand. |
| `fair_deal` | string (JSON) | Verifiable-deal proof `{scheme, seed_commitment, revealed_seed, nonce, deck_order, game_type}`. Present only on hands dealt by recent engine versions — **15,417 of 247,946 hands (~6%)**; None on the rest. Not "every hand". |

### `actions.parquet` — one row per action (long form)

| column | type | description |
|---|---|---|
| `match_id`, `hand_number` | string, int64 | Join keys. |
| `action_idx` | int64 | 0-based order of the action within the hand (includes the forced blind posts). |
| `phase` | string | `preflop` / `flop` / `turn` / `river`. |
| `seat` | int | Acting seat (0 or 1). |
| `agent` | string | Agent label of the acting seat. |
| `action_type` | string | One of `post_small_blind`, `post_big_blind`, `fold`, `check`, `call`, `raise` (the engine records opening bets and all-ins as `raise`). |
| `amount` | float | **Incremental** chips committed by this action (the delta actually paid, not "raise to"). 0 for fold/check. |
| `pot_after` | float | Running pot size after this action (cumulative sum of `amount` within the hand). |

### `checksums.txt`

SHA-256 checksums of the published files.

## Notes

- Heads-up seats: in each hand the `sb_seat` is the button and acts first
  preflop, last postflop.
- Matches are elimination format: play continues (with escalating blinds)
  until one seat has all the chips, so match length varies.
- **Elimination-format drift (important for modeling).** Because blinds
  escalate and matches end when a stack busts, later hands are not drawn from
  the same distribution as early hands. Across within-match progress quartiles
  the **mean big blind climbs ~102 → 121 → 158 → 207**, and per-agent **mean
  match length ranges 11.4 – 38.7 hands** (Agent 07 shortest at 11.4, Agent 04
  longest at 38.7; median match ~19 hands). A hand's position in its match is a
  confound: pooling hands across match phases mixes blind regimes, and an
  agent's average match length is itself an identity signal. Condition on
  `hand_number` / `big_blind` where that matters.
- `net_chips` at match level equals the starting stack transfer of the loser
  to the winner.
- All chips are play chips; no monetary value.

## License / source

Data generated by house-operated AI agents on the Chipzen platform
(chipzen.ai). No human gameplay and no personal data are included.
