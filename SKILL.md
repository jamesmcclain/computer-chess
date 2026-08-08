---
name: computer-chess
description: Play chess through the gnuchess-api REST service (a dockerized GNU Chess server exposing a JSON API on port 5003 and a read-only board viewer on port 5004). Use this skill whenever the user asks to start, join, play, watch, or check the status of a game on this API — including playing against GNU Chess itself, playing as one side of a game against another outside player (which may itself be a person, another agent, or a script), submitting a specific move, checking whose turn it is, or figuring out whether/how a game ended. Always consult this skill before calling the API by hand; it documents the exact endpoints, request/response shapes, and the turn-taking and end-of-game rules an agent needs to play correctly.
---

# Playing chess via gnuchess-api

This skill drives the gnuchess-api REST API (see that repo's `README.md`
for full endpoint reference). It assumes the container is already running
and reachable — default `http://10.0.2.2:5003` for the API and
`http://10.0.2.2:5004` for the read-only viewer. If a different host/port
was given, substitute it everywhere below.

Core facts to hold onto:

- **One game exists at a time.** Starting a new game replaces any game in
  progress.
- **No authentication and no seat reservation.** Anyone who calls
  `POST /api/game/move` when it's a given color's turn moves for that
  color. There's no login or player ID — "playing white" just means
  "submitting moves while `turn` is `white`."
- **The board itself is the only state that matters.** Always trust the
  `state` object in each response (or a fresh `GET /api/game`) over
  anything you remember from earlier in the conversation — GNU Chess's
  moves, in particular, you cannot predict in advance.
- **Don't check whose turn it is in a tight loop.** `state.turn` (from
  `GET /api/game`) tells you whose move it is, but if it's not your turn,
  don't hammer that endpoint back-to-back waiting for it to change.
  Between checks, spend a few seconds doing something useful — thinking
  ahead about your reply to likely opponent moves, narrating the position
  to the user, planning strategy — then check again. See section 3.3.

## 1. Starting a new game

```
POST /api/game
Content-Type: application/json

{"white": "human", "black": "engine", "level": 5}
```

- `white` and `black` are each `"human"` (moves come from an outside
  caller — you, or another player) or `"engine"` (GNU Chess plays that
  side automatically).
- At least one side must be `"human"`. Two `"engine"` sides is rejected.
- If you (the agent) are the one playing, decide which color you want and
  set the *other* side accordingly:
  - You vs. GNU Chess, you as White: `{"white": "human", "black": "engine"}`
  - You vs. GNU Chess, you as Black: `{"white": "engine", "black": "human"}`
  - You vs. another outside player: `{"white": "human", "black": "human"}`
- `level` (optional, `1`-`10`, weakest to strongest, default `5`) sets
  GNU Chess's difficulty for this game — omit it to keep whatever level
  was last set. `GET /api/engine-levels` lists what each level maps to.
  If the user asks for an easier or harder opponent (or names a rough
  difficulty like "beginner" or "hard"), set `level` accordingly rather
  than guessing at move quality yourself. You can also change it anytime
  without starting a new game: `POST /api/game/level {"level": N}`.
- If `white` is `"engine"`, GNU Chess's first move is played immediately;
  the response's `engine_move` field holds it. Read it before doing
  anything else — if you're Black, that's the move you're responding to.

Response is `201` with `{"state": {...}, "engine_move": {...} | null}`.
Keep the `state` around; `state.turn` tells you who moves next.

## 2. Joining a game already in progress

Because there's no auth or seat system, "joining" just means: look at the
current state, figure out what's needed, and act accordingly.

```
GET /api/game
```

- `404` means no game has been started — go to step 1 instead.
- Otherwise inspect:
  - `state.players` — e.g. `{"white": "human", "black": "engine"}`. This
    tells you which color(s) expect outside moves.
  - `state.turn` — whose move it currently is.
  - `state.status` / `state.game_over` — whether the game already ended
    (see section 4) before you do anything else.
- If `state.players[state.turn]` is `"human"` and the game is
  `in_progress`, that's your cue: it's an outside-player side's turn and
  nobody has submitted a move for it yet, so you can play it (via
  section 3).
- If both `white` and `black` are `"human"` (a two-outside-player game —
  the other side needn't be a person; it could be another agent, a
  script, anything calling the API), there is no way to tell *which*
  outside player is expected to move next beyond "whoever's turn it is"
  — coordinate with the user about which color they want you to play,
  then only submit moves when `state.turn` matches that color.
- If it's the engine's turn, don't call `/api/game/move` — it will be
  rejected with a 400 ("it is the engine's turn"). Just wait (see
  section 3.3 for how to do that without hammering the API) — GNU Chess
  replies synchronously inside the *other* side's move request, so this
  should be brief unless nobody has moved yet.

## 3. Playing the game

### 3.1 The move loop

For each of your turns, repeat this loop:

1. **Query legal moves with curl:**
   ```bash
   curl http://10.0.2.2:5003/api/game/legal-moves
   ```
2. **Query the board state if you need a reminder** (optional — useful if
   it's been a while since you last looked, or after an opponent's move
   you haven't seen yet):
   ```bash
   curl http://10.0.2.2:5003/api/game
   ```
3. **Choose a legal move and explain it in the chat** — briefly say what
   you're playing and why, in plain language, before submitting it.
4. **Submit the move with curl:**
   ```bash
   curl -X POST http://10.0.2.2:5003/api/game/move \
     -H 'Content-Type: application/json' \
     -d '{"move": "e2e4"}'
   ```
5. **Repeat** from step 1 until the game is over (section 4).

If the response to your move includes a non-null `engine_move`, that's
GNU Chess's reply — read and narrate it too before starting your next
loop iteration. In a two-outside-player game, wait for the other side
(section 3.3) before your next loop iteration instead.

### 3.2 Move submission details

Legal moves (from step 1 above) look like:

```
GET /api/game/legal-moves            # all legal moves for the side to move
GET /api/game/legal-moves?from=e2    # only moves starting on e2
```

Each entry has `uci` (e.g. `"e2e4"`, `"e7e8q"` for promotion), `san`
(e.g. `"e4"`, `"Nf3"`, `"O-O"`), `from`, `to`, `promotion`.

Submit a move (UCI or SAN both work — prefer UCI, it's unambiguous):

```
POST /api/game/move
Content-Type: application/json

{"move": "e2e4"}
```

Response:

```json
{
  "move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "human"},
  "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine"} ,
  "state": {...}
}
```

- `move` is what you just submitted, echoed back.
- `engine_move` is set (non-null) only if it immediately became an
  `"engine"` side's turn afterward — GNU Chess's reply is computed and
  applied synchronously in this same call. Read it: that's the opponent's
  reply you need to react to next.
- `state` is the full, current game state after both of the above have
  been applied — always re-derive `turn`/`status` from here, don't
  assume.
- `400` means the move was illegal, malformed, or it wasn't an
  outside-player side's turn — read the `error` field and correct course
  (e.g. re-fetch legal moves, or re-check whose turn it is).

### 3.3 Waiting for the other side (two-outside-player games only)

If you're one side of a two-outside-player (`"human"`/`"human"`) game and
it's not your turn, don't just spin in a tight `GET /api/game` loop —
that burns calls for no benefit and looks like you're stuck. Instead,
each time you check and it's still not your turn, spend a few seconds
doing something useful before checking again: think ahead about how
you'd respond to a couple of likely opponent replies, jot down your plan,
or just tell the user you're waiting on their move — then check once
more:

```
GET /api/game        # check, think for a few seconds, check again — not a rapid loop
```

Stop checking and react as soon as `state.turn` becomes your color, or
`state.game_over` becomes `true`. (If you'd rather not poll at all, the
board viewer's event stream at `GET http://10.0.2.2:5004/events` —
Server-Sent Events — pushes a fresh state the instant the game changes;
it's read-only JSON and not documented as a control API, but nothing
stops you from reading it while waiting.)

### 3.4 Watching without playing

`GET http://10.0.2.2:5004/` is a read-only HTML board (updates live via
Server-Sent Events, no manual refresh needed) if the user just wants to
watch — it accepts no input, so don't rely on it for anything except
display.

## 4. Recognizing the end of a game

After every move (or poll), check the `state` object's `game_over` and
`status` fields — don't infer game-over from the move history yourself.

- `game_over: false` → still playing; `status` will be `"in_progress"`.
- `game_over: true` → the game has ended. `status` will be one of:

  | `status`                                | Meaning                                   |
  |------------------------------------------|--------------------------------------------|
  | `checkmate`                               | Side to move is mated                       |
  | `stalemate`                               | Draw — side to move has no legal moves      |
  | `draw_insufficient_material`              | Draw — neither side can mate                |
  | `draw_75_moves`                           | Draw — 75-move rule (automatic)             |
  | `draw_5fold_repetition`                   | Draw — fivefold repetition (automatic)      |
  | `draw_claimable_50_moves`                 | Draw claimable (50-move rule reached)       |
  | `draw_claimable_threefold_repetition`     | Draw claimable (threefold repetition)       |
  | `resigned`                                | A side resigned                             |

  `state.winner` is `"white"`, `"black"`, or `null` (draws, or no result
  yet). On checkmate/resignation this names the winner directly — no need
  to compute it yourself.

- Once `game_over` is `true`, `POST /api/game/move` will start returning
  `400` ("game is not in progress") — treat that as confirmation the game
  is over if you somehow missed the state check, but don't rely on it as
  your primary signal; check `state` proactively instead.
- To resign on behalf of a side (ending the game early):
  ```
  POST /api/game/resign
  {"player": "white"}
  ```
- When a game ends, report the result to the user in plain language
  (e.g. "Checkmate — black wins" or "Draw by stalemate") rather than just
  the raw status string, and offer to start a new game (step 1) if they
  want to keep playing.
