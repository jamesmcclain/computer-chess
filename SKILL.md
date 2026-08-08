---
name: computer-chess
description: Play chess through the computer-chess REST service. This is a dockerized GNU Chess server with a JSON API on port 5003 and a read-only board viewer on port 5004. Use this skill when the user asks to start, join, play, watch, or check the status of a game on this API. This includes a game against GNU Chess itself, a game against another API user (a person, another agent, or a script), a specific move, the side to move, or the result of a game. Read this skill before you call the API by hand. It gives the exact endpoints, request and response shapes, and the turn-taking and end-of-game rules for correct play.
---

# Playing chess through computer-chess

This skill drives the computer-chess REST API. See that repository's
`README.md` for the full endpoint reference. This skill assumes the
container is already running and reachable. The default address is
`http://10.0.2.2:5003` for the API and `http://10.0.2.2:5004` for the
read-only viewer. If you received a different host or port, use it in
place of these defaults everywhere below.

Core facts to remember:

- **One game is active at a time.** A new game replaces any game in
  progress.
- **The API uses no authentication and no seat reservation.** Anyone
  who calls `POST /api/game/move` during a given color's turn moves for
  that color. There is no login and no player ID. "Playing white" means
  "you submit moves while `turn` is `white`".
- **The board itself is the only state that matters.** Trust the
  `state` object in each response, or a fresh `GET /api/game`, over
  anything you remember from earlier in the conversation. You cannot
  predict GNU Chess's moves in advance.
- **Do not check the turn in a tight loop.** `state.turn` (from
  `GET /api/game`) names the side to move. If it is not your turn, do
  not call this endpoint back-to-back. Between checks, spend a few
  seconds on something useful: think ahead about your reply to likely
  opponent moves, or tell the user the current position. Then check
  again. See section 3.3.

## 1. Starting a new game

```
POST /api/game
Content-Type: application/json

{"white": "api-user", "black": "engine", "level": 5}
```

- `white` and `black` are each `"api-user"` (moves come from an
  outside caller: you, or another player) or `"engine"` (GNU Chess
  plays that side automatically).
- At least one side must be `"api-user"`. The API rejects two
  `"engine"` sides.
- If you are the one playing, pick your color and set the other side
  accordingly:
  - You vs. GNU Chess, you as White: `{"white": "api-user", "black": "engine"}`
  - You vs. GNU Chess, you as Black: `{"white": "engine", "black": "api-user"}`
  - You vs. another API user: `{"white": "api-user", "black": "api-user"}`
- `level` (optional, `1`-`10`, weakest to strongest, default `5`) sets
  the difficulty of GNU Chess for this game. Omit `level` to keep the
  last value. `GET /api/engine-levels` lists what each level means. If
  the user asks for an easier or harder opponent, or names a rough
  difficulty such as "beginner" or "hard", set `level` accordingly.
  Do not guess at move quality yourself. You can also change the level
  at any time, without a new game: `POST /api/game/level {"level": N}`.
- If `white` is `"engine"`, GNU Chess plays its first move
  immediately. The response holds this move in `engine_move`. Read it
  before you do anything else. If you are Black, this is the move you
  respond to.

The response is `201` with `{"state": {...}, "engine_move": {...} | null}`.
Keep `state`: `state.turn` names the side to move next.

## 2. Joining a game already in progress

The API has no login or seat system, so "joining" means: check the
current state, find what is needed, and act on it.

```
GET /api/game
```

- `404` means no game has started. Go to section 1 instead.
- Otherwise, check these fields:
  - `state.players` — for example, `{"white": "api-user", "black": "engine"}`.
    This names which color, or colors, expect outside moves.
  - `state.turn` — the side to move now.
  - `state.status` and `state.game_over` — whether the game already
    ended (see section 4), before you take any other action.
- If `state.players[state.turn]` is `"api-user"` and the game is
  `in_progress`, that side's turn is open: no one has submitted a move
  for it yet, so you can play it (see section 3).
- If both `white` and `black` are `"api-user"` (a two-API-user game —
  the other side does not need to be a person, it can be another
  agent, a script, or anything that calls the API), the state alone
  does not name which API user moves next. Ask the user which color
  they want you to play. Then submit moves only when `state.turn`
  matches that color.
- If it is the engine's turn, do not call `/api/game/move`. The API
  rejects it with a `400` ("it is the engine's turn"). Wait instead
  (section 3.3 gives the correct way to wait). GNU Chess replies
  inside the other side's move request, so the wait is short unless no
  one has moved yet.

## 3. Playing the game

### 3.1 The move loop

For each of your turns, repeat this loop:

1. Query legal moves with curl:
   ```bash
   curl http://10.0.2.2:5003/api/game/legal-moves
   ```
2. Query the board state if you need a reminder. This step is
   optional. Use it when some time has passed since you last checked,
   or after an opponent's move you have not seen yet:
   ```bash
   curl http://10.0.2.2:5003/api/game
   ```
3. Choose a legal move. Before you submit it, tell the user in the
   chat, in plain language, what you plan to play and why.
4. Submit the move with curl:
   ```bash
   curl -X POST http://10.0.2.2:5003/api/game/move \
     -H 'Content-Type: application/json' \
     -d '{"move": "e2e4"}'
   ```
5. Repeat from step 1 until the game ends (section 4).

If the response to your move has a non-null `engine_move`, that is
GNU Chess's reply. Read it and tell the user about it too, before you
start the next loop. In a two-API-user game, wait for the other side
(section 3.3) instead, before your next loop.

### 3.2 Move submission details

Legal moves (from step 1 above) have this form:

```
GET /api/game/legal-moves            # all legal moves for the side to move
GET /api/game/legal-moves?from=e2    # only moves that start on e2
```

Each entry has `uci` (for example, `"e2e4"`, or `"e7e8q"` for
promotion), `san` (for example, `"e4"`, `"Nf3"`, `"O-O"`), `from`,
`to`, and `promotion`.

Submit a move. UCI and SAN both work. Prefer UCI, because it has only
one meaning:

```
POST /api/game/move
Content-Type: application/json

{"move": "e2e4"}
```

Response:

```json
{
  "move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user"},
  "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine"} ,
  "state": {...}
}
```

- `move` echoes back what you submitted.
- `engine_move` is set (non-null) only when it becomes an `"engine"`
  side's turn immediately after your move. The API computes and
  applies GNU Chess's reply in this same call. Read it: this is the
  opponent's reply you respond to next.
- `state` is the full, current game state, after both moves above are
  applied. Always read `turn` and `status` from this object. Do not
  assume their values.
- `400` means the move was illegal, malformed, or it was not an
  API user's turn. Read the `error` field and correct your next call:
  for example, fetch legal moves again, or check the turn again.

### 3.3 Waiting for the other side (two-API-user games only)

If you are one side of a two-API-user (`"api-user"`/`"api-user"`)
game and it is not your turn, do not spin in a tight `GET /api/game`
loop. A tight loop wastes calls and gives no benefit. Instead, each
time you check and the turn is still not yours, spend a few seconds on
something useful, then check once more: think ahead about a reply to
a likely opponent move, note your plan, or tell the user you wait for
their move.

```
GET /api/game        # check, think for a few seconds, check again — not a rapid loop
```

Stop checking and react as soon as `state.turn` becomes your color, or
`state.game_over` becomes `true`. If you prefer not to poll at all,
use the board viewer's event stream at `GET http://10.0.2.2:5004/events`
(Server-Sent Events). This stream pushes a fresh state the instant the
game changes. It is read-only JSON, and the API docs do not list it as
a control endpoint, but you can read it while you wait.

### 3.4 Watching without playing

`GET http://10.0.2.2:5004/` is a read-only HTML board. It updates live
through Server-Sent Events, with no manual refresh, for a user who
wants only to watch. This page accepts no input, so do not rely on it
for anything except display.

## 4. Recognizing the end of a game

After every move, or every poll, check the `state` object's
`game_over` and `status` fields. Do not infer the end of the game from
the move history yourself.

- `game_over: false` means the game continues. `status` is
  `"in_progress"`.
- `game_over: true` means the game ended. `status` is one of:

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

  `state.winner` is `"white"`, `"black"`, or `null` (a draw, or no
  result yet). On checkmate or resignation, this field names the
  winner directly. Do not compute it yourself.

- Once `game_over` is `true`, `POST /api/game/move` starts to return
  `400` ("game is not in progress"). Treat this as confirmation only
  if you somehow missed the state check. Check `state` directly
  instead. Do not rely on the `400` as your main signal.
- To resign on behalf of a side, and end the game early:
  ```
  POST /api/game/resign
  {"player": "white"}
  ```
- When a game ends, report the result to the user in plain language,
  for example "Checkmate — black wins" or "Draw by stalemate", not the
  raw status string. Offer to start a new game (section 1) if the user
  wants to keep playing.
