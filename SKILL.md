---
name: computer-chess
description: Play chess through the computer-chess REST service. This is a dockerized GNU Chess server with a JSON API on port 5003 and a board viewer on port 5004. Use this skill when the user asks to start, join, play, watch, or check the status of a game on this API. This includes a game against GNU Chess itself, or against another API user (a person, another agent, or a script), or against a person playing through the board viewer. It also covers a specific move, the side to move, and the result of a game. Read this skill before you call the API by hand. It gives the exact endpoints, request and response shapes, and the turn-taking and end-of-game rules for correct play.
---

# Playing chess through computer-chess

This skill drives the computer-chess REST API. See that repository's
`README.md` for the full endpoint reference. This skill assumes the
container is already running and reachable. The default address is
`http://10.0.2.2:5003` for the API and `http://10.0.2.2:5004` for the
board viewer. If you received a different host or port, use it in
place of these defaults everywhere below.

Core facts to remember:

- **One game is active at a time.** A new game replaces any game in
  progress.
- **A side is one of three types.** `"api-user"` (an outside caller
  such as you, submitting moves through this API), `"web-user"` (a
  person playing through the board viewer, by clicking the board), or
  `"engine"` (GNU Chess). You always act as `"api-user"`, whichever
  color you play. Your opponent can be any of the three types. Do not
  assume it is the engine.
- **The API uses no authentication and no seat reservation.** Anyone
  who calls `POST /api/game/move` during a given color's turn moves for
  that color. There is no login and no player ID. "Playing white" means
  "you submit moves while `turn` is `white`".
- **The board itself is the only state that matters.** Trust the
  `state` object in each response, or a fresh `GET /api/game`, over
  anything you remember from earlier in the conversation. You cannot
  predict the engine's moves, or another person's moves, in advance.
- **Do not check the turn in a tight loop.** `state.turn` (from
  `GET /api/game`) names the side to move. If it is not your turn, do
  not call this endpoint back-to-back. Between checks, spend a few
  seconds on something useful: think ahead about your reply to likely
  opponent moves, or tell the user the current position. Then check
  again. See section 3.3.
- **Always narrate your thinking in the chat.** After you see an
  opponent move, and before you submit your own move, say something.
  Section 3.1 gives the exact points to do this at.

## 1. Starting a new game

```
POST /api/game
Content-Type: application/json

{"white": "api-user", "black": "engine", "level": 5}
```

- `white` and `black` are each `"api-user"`, `"web-user"`, or
  `"engine"` (see the core facts above for what each means).
- Both sides cannot be `"engine"`. Every other pairing is valid.
- If you are the one playing, pick your color and set the other side
  accordingly:
  - You vs. the engine, you as White: `{"white": "api-user", "black": "engine"}`
  - You vs. the engine, you as Black: `{"white": "engine", "black": "api-user"}`
  - You vs. another API user: `{"white": "api-user", "black": "api-user"}`
  - You vs. a person on the board viewer: set the other side to
    `"web-user"`, for example `{"white": "api-user", "black": "web-user"}`.
- `level` (optional, `1`-`10`, weakest to strongest, default `5`) sets
  the difficulty of the engine for this game. It only matters when a
  side is `"engine"`. Omit `level` to keep the last value.
  `GET /api/engine-levels` lists what each level means. If the user
  asks for an easier or harder opponent, or names a rough difficulty
  such as "beginner" or "hard", set `level` accordingly. Do not guess
  at move quality yourself. You can also change the level at any time,
  without a new game: `POST /api/game/level {"level": N}`.
- If `white` is `"engine"`, the engine plays its first move
  immediately. The response holds this move in `engine_move`. Read it
  before you do anything else. If you are Black, this is the move you
  respond to.

The response is `201` with `{"state": {...}, "engine_move": {...} | null}`.
Keep `state`: `state.turn` names the side to move next.

## 2. Joining a game already in progress

The API has no login or seat system, so "joining" means: check the
current state, find the open side, and start submitting moves for it.
You can join as White or as Black. The steps are the same for both,
only the color name changes.

1. Check whether a game exists, and what it looks like:
   ```
   GET /api/game
   ```
   A `404` means no game has started. Go to section 1 instead, and
   set yourself, `"api-user"`, as whichever color the user wants.

2. Read `state.players`, for example
   `{"white": "api-user", "black": "engine"}`. This names the type of
   each side. Find the color the user wants you to play, then confirm
   its type:
   - If that color's type is already `"api-user"` and no one else is
     acting for it, you can play it. Move on to step 3.
   - If that color's type is `"engine"` or `"web-user"`, you cannot
     take over that side. Tell the user, and offer to start a fresh
     game instead (section 1) with them set to your intended color.

3. Read `state.status` and `state.game_over`. If the game already
   ended, do not submit a move. Report the result instead (section 4)
   and offer a new game.

4. Read `state.turn`. This is the color to move now, not necessarily
   your color. If `state.turn` equals your color, go to section 3 and
   play your move. If not, wait for your turn (section 3.3).

A two-API-user game (`state.players` shows `"api-user"` for both
colors) needs one extra check: the state alone does not say which
outside caller owns which color, since there is no login. Ask the user
which color they want you to play, then submit moves only when
`state.turn` matches that color.

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
3. Check for an opponent move since your last turn: a new
   `engine_move`, or a new `move_log` entry from your opponent. If you
   find one, tell the user what you think it aims to do, in plain
   language. Do this every time, before you plan your own move.
4. Choose a legal move. Before you submit it, tell the user the
   theory behind it in the chat. Explain what it does for your
   position, and why you picked it over the alternatives. Give this
   explanation every time, not only when the move looks unusual.
5. Submit the move with curl:
   ```bash
   curl -X POST http://10.0.2.2:5003/api/game/move \
     -H 'Content-Type: application/json' \
     -d '{"move": "e2e4"}'
   ```
6. Repeat from step 1 until the game ends (section 4).

If the response to your move has a non-null `engine_move`, that is
the engine's reply. Treat it the same as any opponent move: narrate
it (step 3) at the start of your next loop, before you plan your
reply. In a two-API-user or a `"web-user"`-opponent game, wait for the
other side (section 3.3) instead, before your next loop. Narrate their
move (step 3) as soon as you see it in `move_log`.

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
  applies the engine's reply in this same call. Read it: this is the
  opponent's reply you narrate and respond to next (section 3.1, step 3).
- `state` is the full, current game state, after both moves above are
  applied. Always read `turn` and `status` from this object. Do not
  assume their values.
- `400` means the move was illegal, malformed, or it was not an
  `"api-user"` or `"web-user"` side's turn. Read the `error` field and
  correct your next call: for example, fetch legal moves again, or
  check the turn again.

### 3.3 Waiting for the other side

If it is not your turn and the other side is not `"engine"` (an
`"api-user"` or a `"web-user"` opponent), do not spin in a tight
`GET /api/game` loop. A tight loop wastes calls and gives no benefit.
Instead, each time you check and it is still not your turn, spend a
few seconds on something useful. Think ahead about a reply to a
likely opponent move. Note your plan, or tell the user you wait for
their move. Then check once more.

```
GET /api/game        # check, think for a few seconds, check again — not a rapid loop
```

Stop checking and react as soon as `state.turn` becomes your color, or
`state.game_over` becomes `true`. If you prefer not to poll at all,
use the board viewer's event stream at `GET http://10.0.2.2:5004/events`
(Server-Sent Events). This stream pushes a fresh state the instant the
game changes. It is JSON meant for the viewer page, and the API docs
do not list it as a control endpoint, but you can read it while you
wait.

### 3.4 Watching without playing

`GET http://10.0.2.2:5004/` is an HTML board. It updates live through
Server-Sent Events, with no manual refresh, for a user who wants to
watch. A person can also use this page to start a game, or to play a
`"web-user"` side by clicking the board, but that is a person acting
through the browser, not you. When you act on the game, always use the
REST API (port 5003) as described in this skill.

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
