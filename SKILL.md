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
  `GET /api/game`) names the side to move. If it is not your turn,
  prefer `GET /api/game/wait` over polling — it blocks until your turn
  comes up. See section 4.3.
- **Always narrate your thinking to the user.** After you see an
  opponent move, and before you submit your own move, say something.
  Section 4.1 gives the exact points to do this at. This is separate
  from in-game chat (next bullet) — it is what you tell the person you
  are talking to, in this conversation.
- **You can set a display name, attach a short chat message to a
  move, and record private reasoning.** All three are optional. The
  name and chat are visible to your opponent and anyone watching the
  board viewer. Reasoning is not shared with anyone while the game is
  in progress (see the transcript exception in section 2). There is
  no standalone chat channel — chat always rides along with a move.
  See section 2.
- **You can "phone a friend" for a move recommendation, since you are
  always `"api-user"`.** This asks GNU Chess what it would play in the
  current position, without submitting that move or ending your turn.
  Each game gives you a small, separate budget of level-10 and
  level-5 queries — by default 1 and 2. See section 4.5.
- **Once the game ends, a PGN transcript is available.** It folds in
  every move's chat and reasoning. See section 5.1.

## 1. Starting a new game

```
POST /api/game
Content-Type: application/json

{"white": "api-user", "black": "engine", "level": 5}
```

- `white` and `black` are each `"api-user"`, `"web-user"`, or
  `"engine"` (see the core facts above for what each means). Every
  pairing is valid, including two engines — see the note below.
- If you are the one playing, pick your color and set the other side
  accordingly:
  - You vs. the engine, you as White: `{"white": "api-user", "black": "engine"}`
  - You vs. the engine, you as Black: `{"white": "engine", "black": "api-user"}`
  - You vs. another API user: `{"white": "api-user", "black": "api-user"}`
  - You vs. a person on the board viewer: set the other side to
    `"web-user"`, for example `{"white": "api-user", "black": "web-user"}`.
- `level` (optional, `1`-`10`, weakest to strongest, default `5`) sets
  the difficulty for both sides at once. It only matters for a side
  that is `"engine"`. `white_level` and `black_level` (each optional,
  `1`-`10`) set one side's difficulty on its own, and win over `level`
  for that side. Use them for two engines at different strengths (see
  below). Omit a level to keep its last value. `GET /api/engine-levels`
  lists what each level means. If the user asks for an easier or
  harder opponent, set the level accordingly. The same applies if they
  name a rough difficulty, such as "beginner" or "hard". Do not guess
  at move quality yourself. You can also change a level at any time,
  without a new game: `POST /api/game/level {"level": N, "color": "white"}`
  (omit `"color"` to set both sides).
- If `white` is `"engine"` and `black` is not, the engine plays its
  first move immediately. The response holds this move in
  `engine_move`. Read it before you do anything else. If you are
  Black, this is the move you respond to.
- `white_name`/`black_name` (each optional) set that side's display
  name for this game. See section 2 for what a name does and how to
  set or change one later, including for a game you did not start.
- `friend_level5_limit`/`friend_level10_limit` (each optional,
  integers, default `2` and `1`) set this game's "phone a friend"
  budget for whichever side ends up `"api-user"` — see section 4.5.
  Unlike `level`/the name fields above, these are not sticky: every
  new game gets the defaults unless you set them here, and usage
  always starts at zero. Raise them if the user wants more hints
  available, or set either to `0` to turn that tier off entirely.
- **Two engines watching each other is supported, for a user who wants
  to watch a game rather than play one.** Set both `white` and `black`
  to `"engine"`, optionally with different `white_level` and
  `black_level`. Neither side will ever call `POST /api/game/move`. Do
  not start this setup if the user, or you, want to play instead.
  Nothing in section 4 applies to a game like this. The game plays
  itself out in the background at its own pace. Watch it with
  `GET /api/game` polling (section 4.3) or the board viewer's event
  stream, the same as any other game.

The response is `201` with `{"state": {...}, "engine_move": {...} | null}`.
Keep `state`: `state.turn` names the side to move next.

## 2. Setting your name, chatting, and recording your reasoning

All three features below are optional and cosmetic, with no effect on
move legality or turn order. Skip whichever ones the user has not
asked for.

**Display name.** Set one with:

```
POST /api/game/name
Content-Type: application/json

{"color": "white", "name": "Deep Purple"}
```

- `color` is `"white"` or `"black"` — whichever side you are playing.
- `name` is up to 40 characters. Longer text is cut short rather than
  rejected. Send an empty string to clear a name back to showing just
  your type (`"api-user"`).
- This works whether or not a game is running, and takes effect right
  away. Use it to set your name before your first move in a game you
  are joining (section 3). `white_name`/`black_name` on
  `POST /api/game` (section 1) only sets a name when that game is
  created.
- Once set, your name is shown in the board viewer and stamped on
  each of your move-log entries (see `name` in section 4.2). It stays
  set for later games too, until changed again.

**Chat attached to a move.** Attach a short line to a move with the
`chat` field on `POST /api/game/move` (section 4.2). There is no
standalone chat channel — every chat line rides along with a move:

```json
{"move": "e2e4", "chat": "Good luck!"}
```

- `chat` is up to 240 characters. Longer text is cut short. Leave it
  out for a normal move with no chat attached.
- There is no separate inbox. The API stamps your chat onto that
  move's entry in `move_log`. Your opponent sees it the next time
  they read the game state — the response to their own next move, or
  a plain `GET /api/game`. If your opponent is a person at the board
  viewer, they see it there in a chat panel, next to the move.
- To read a chat line from your opponent, check the latest `move_log`
  entry that is theirs for a `chat` field. This is the same place you
  already look to see what move they made (section 4.1, step 3).
- If you want to say something not really about the move — a
  greeting before the game starts, "gg" once it's decided — attach it
  to whatever move you're submitting anyway (your first move, or your
  last one before resigning). There's no way to send chat without
  submitting a move alongside it.

**Private reasoning.** `reasoning` (optional, up to 1000 characters,
also cut short rather than rejected) is a second field on
`POST /api/game/move`, alongside `chat`:

```json
{"move": "e2e4", "reasoning": "e4 grabs the center and opens lines for the bishop and queen."}
```

- Unlike `chat`, `reasoning` is never returned by any endpoint while
  the game is in progress. It is kept only on the server. It is not
  shown to your opponent, anyone at the board viewer, or even back to
  you on a later read — with one exception: once the game ends, it is
  folded into that game's PGN transcript (section 5.1), since there
  is no longer any ongoing advantage to protect. Use it if the user
  wants their move-by-move thinking recorded for later review. This
  is separate from what you say to them directly (the "narrate your
  thinking" core fact above), and separate from `chat`, which your
  opponent does see immediately.
- This is unrelated to the plain-language explanation you already
  give the user before each move (section 4.1, step 4). Giving one
  does not excuse skipping the other.

## 3. Joining a game already in progress

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
   - If both colors show `"engine"`, this is a watch-only game between
     two engines (see the note in section 1). Neither color is
     joinable. Tell the user, and offer to start a fresh game instead.

3. Read `state.status` and `state.game_over`. If the game already
   ended, do not submit a move. Report the result instead (section 5)
   and offer a new game.

4. Read `state.turn`. This is the color to move now, not necessarily
   your color. If `state.turn` equals your color, go to section 4 and
   play your move. If not, wait for your turn (section 4.3).

A two-API-user game (`state.players` shows `"api-user"` for both
colors) needs one extra check: the state alone does not say which
outside caller owns which color, since there is no login. Ask the user
which color they want you to play, then submit moves only when
`state.turn` matches that color.

## 4. Playing the game

### 4.1 The move loop

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
   language. Do this every time, before you plan your own move. Also
   check that entry for a `chat` field (section 2). If it has one,
   treat it as a chat line from your opponent and react to it too.
4. Choose a legal move. Before you submit it, tell the user the
   theory behind it: explain what it does for your position, and why
   you picked it over the alternatives. Give this explanation every
   time, not only when the move looks unusual. Optionally, also give
   the move a short `chat` (section 2): a greeting, a comment on the
   position, anything brief.
5. Submit the move with curl:
   ```bash
   curl -X POST http://10.0.2.2:5003/api/game/move \
     -H 'Content-Type: application/json' \
     -d '{"move": "e2e4", "chat": "Good luck!"}'
   ```
6. Repeat from step 1 until the game ends (section 5).

If the response to your move has a non-null `engine_move`, that is
the engine's reply. Treat it the same as any opponent move: narrate
it (step 3) at the start of your next loop, before you plan your
reply. In a two-API-user or a `"web-user"`-opponent game, wait for the
other side (section 4.3) instead, before your next loop. Narrate their
move (step 3) as soon as you see it in `move_log`.

### 4.2 Move submission details

Legal moves (from step 1 above) have this form:

```
GET /api/game/legal-moves            # all legal moves for the side to move
GET /api/game/legal-moves?from=e2    # only moves that start on e2
```

Each entry has `uci` (for example, `"e2e4"`, or `"e7e8q"` for
promotion), `san` (for example, `"e4"`, `"Nf3"`, `"O-O"`), `from`,
`to`, and `promotion`.

Submit a move. UCI and SAN both work. Prefer UCI, because it has only
one meaning. `chat` and `reasoning` (both optional, section 2)
attach a chat line and a private note, respectively:

```
POST /api/game/move
Content-Type: application/json

{"move": "e2e4", "chat": "Good luck!", "reasoning": "e4 grabs the center"}
```

Response:

```json
{
  "move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"},
  "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine", "name": "GNU Chess"} ,
  "state": {...}
}
```

- `move` echoes back what you submitted, with `name` (your current
  display name, or `null` if you have not set one — section 2) and
  `chat` (only present if you sent one) added.
- `engine_move` is set (non-null) only when it becomes an `"engine"`
  side's turn immediately after your move. The API computes and
  applies the engine's reply in this same call. Read it: this is the
  opponent's reply you narrate and respond to next (section 4.1, step 3).
- `state` is the full, current game state, after both moves above are
  applied. Always read `turn` and `status` from this object. Do not
  assume their values. It also carries `player_names`, the current
  `{"white": name_or_null, "black": name_or_null}` (section 2).
- `400` means the move was illegal, malformed, or it was not an
  `"api-user"` or `"web-user"` side's turn. Read the `error` field and
  correct your next call: for example, fetch legal moves again, or
  check the turn again.

### 4.3 Waiting for the other side

If it is not your turn, wait for your opponent (an `"api-user"` or
`"web-user"`, not the engine) with one blocking call instead of a
poll loop:

```
GET /api/game/wait?color=white&timeout=25
```

- `color` is your color. This call blocks until it becomes that
  color's turn, the game ends, or `timeout` seconds pass (optional,
  default 25, capped at 55) — whichever comes first. It returns
  `{"state": {...}}`.
- It returns immediately, with no wait at all, if it is already your
  turn, the game has already ended, or no game has started.
- A timeout looks the same as any other return. Check `state.turn`
  and `state.game_over` yourself. If neither changed, call it again.
- While this call is blocked, you cannot do anything else in this
  conversation. Tell the user you are waiting for their opponent's
  move before you make this call. Nothing else will happen until it
  returns.

To keep the conversation moving while you wait, instead poll
`GET /api/game`, with a real pause between calls:

```
GET /api/game        # check, think for a few seconds, check again — not a rapid loop
```

Each time you check and it is still not your turn, spend a few
seconds on something useful. Think ahead about a reply to a likely
opponent move. Note your plan, or tell the user you wait for their
move. Then check once more.

Either way, stop as soon as `state.turn` becomes your color, or
`state.game_over` becomes `true`. A person watching the board viewer
sees updates instantly, over Server-Sent Events at
`GET http://10.0.2.2:5004/events`. That stream is meant for the
viewer page, not listed as a control endpoint. You can read it too,
if you prefer it to either option above.

### 4.4 Watching without playing

`GET http://10.0.2.2:5004/` is an HTML board. It updates live through
Server-Sent Events, with no manual refresh, for a user who wants to
watch. A person there can also start a game, play a `"web-user"` side
by clicking the board, type a chat line to go out with their next
move, or end the game early with a Resign or Restart button — but
that is a person acting through the browser, not you. When you act on
the game, always use the REST API (port 5003) as described in this
skill.

### 4.5 Phoning a friend

You can ask GNU Chess what it would play in the current position,
without submitting that move — a hint for a hard decision, not a
substitute for choosing and submitting your own move (section 4.1,
step 4-5). This is only available to you, the `"api-user"` side, and
only on your own turn.

```
POST /api/game/phone-a-friend
Content-Type: application/json

{"level": 10}
```

- `level` is `5` or `10` — no other values. Level 10 searches deeper
  and gives a stronger recommendation; level 5 is quicker and weaker.
  Pick 10 for a critical, hard-to-read position; 5 is enough for a
  routine check.
- Each level has its own budget for the whole game, set when the game
  was started (`friend_level5_limit`/`friend_level10_limit`, section
  1; default `2` for level 5 and `1` for level 10). Budgets are
  tracked separately per side, so in a two-`"api-user"` game your
  budget is independent of your opponent's.
- Calling this does **not** change the board, does **not** end your
  turn, and does **not** count as your move. You must still submit a
  move yourself via `POST /api/game/move` (section 4.2) afterward,
  whether or not you take the suggestion.

Response:

```json
{
  "advice": {"level": 10, "uci": "g1f3", "san": "Nf3", "color": "white", "used": 1, "limit": 1, "remaining": 0},
  "state": {...}
}
```

- `advice.uci`/`advice.san` is the recommended move, in both
  notations (section 4.2 explains the difference). `advice.used`,
  `advice.limit`, and `advice.remaining` tell you how much of that
  level's budget you've now used, its total, and what's left.
- `state` is the full, current game state — unchanged by this call
  except for `state.phone_a_friend`, which always shows both sides'
  budget and usage at both levels, whether or not you've called this
  endpoint yet:
  ```json
  "phone_a_friend": {
    "limits": {"level_5": 2, "level_10": 1},
    "white": {"used": {"level_5": 0, "level_10": 1}, "remaining": {"level_5": 2, "level_10": 0}},
    "black": {"used": {"level_5": 0, "level_10": 0}, "remaining": {"level_5": 2, "level_10": 1}}
  }
  ```
- `400` means it was not your turn, your side is not `"api-user"`
  (should not happen — you always are), `level` was not `5` or `10`,
  or you have no queries left at that level. Read the `error` field.
  If you are out of budget at one level, either try the other level
  (if you still have queries there) or just decide on your own.
- Using this is optional. Only call it when it would actually help —
  a genuinely hard or unclear position — not on every move; your
  budget is small by design. When you do use it and then move, feel
  free to mention to the user that you asked for a hint, as part of
  your normal narration (the "always narrate" core fact above).

## 5. Recognizing the end of a game

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

### 5.1 Downloading a transcript

Once the game has ended (and only then), a PGN (Portable Game
Notation) transcript is available — the standard plain-text chess
format read by lichess.org, chess.com, and most chess software:

```
GET /api/game/transcript
```

- Returns the raw PGN text (not JSON) — metadata as tag pairs at the
  top (players, result, engine levels where relevant, how the game
  ended), then the move list.
- Every move's `chat` (section 2) and any private `reasoning` you
  recorded for it (also section 2) are folded in as a comment on that
  move. This is the one place `reasoning` is ever exposed — once the
  game is over there's no ongoing advantage left to protect.
- `400` means no game has started, or the current game is still in
  progress — this only works after `state.game_over` is `true`.
- If the user asks for a copy of the game, a way to review it later,
  or to see their own private reasoning written out, this is the
  endpoint. Fetch it and share the contents (or save it to a file
  named something like `game.pgn`, if you have file access).
