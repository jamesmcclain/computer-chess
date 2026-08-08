---
name: computer-chess
description: Play chess through the computer-chess REST service, a dockerized GNU Chess server with a JSON API on port 5003 and a board viewer on port 5004. Use this skill when the user wants to start, join, play, watch, or check a game — against GNU Chess, another API user, or a person on the board viewer. It also covers a specific move, the side to move, and the game result. Read this skill before you call the API by hand. It gives the exact endpoints, request and response shapes, and the turn-taking and end-of-game rules for correct play.
---

# Playing chess through computer-chess

This skill drives the computer-chess REST API. See the repository's
`README.md` for the full endpoint reference.

Assume the container is already running and reachable. Default
addresses: `http://10.0.2.2:5003` for the API, `http://10.0.2.2:5004`
for the board viewer. If you got a different host or port, use it in
place of these defaults everywhere below.

Core facts to remember:

- You play one and only one side of a game (black or white) never
  both in the same game.
- **`for` loops are strictly prohibited, in the shell and in Python
  alike.** Make one API call per move. Stop and think about that move
  before you submit it. A loop skips the thinking step and can play
  out a whole game blind, with no narration and no real decisions.
  See section 4.1.
- **Once told to start or join a game, keep playing until it ends.**
  Submit moves and wait for turns (section 4.3) one at a time, on
  your own, without a stop for user input between moves. Stop only if
  the user explicitly tells you to stop. `state.game_over` tells you
  when the game has ended (section 5).
- **One game is active at a time.** A new game replaces any game in
  progress.
- **A side has one of three types.** `"api-user"` (an outside caller
  like you, sending moves through this API), `"web-user"` (a person
  who clicks the board in the viewer), or `"engine"` (GNU Chess). You
  always act as `"api-user"`, whichever color you play. Your opponent
  can be any of the three types — do not assume it is the engine.
- **The API has no authentication and no seat reservation.** Whoever
  calls `POST /api/game/move` during a color's turn moves for that
  color. There is no login and no player ID. "Playing white" means
  you submit moves while `turn` is `white`.
- **The board state is the only state that matters.** Trust the
  `state` object in each response, or a fresh `GET /api/game` call,
  over anything you remember from earlier in the conversation. You
  cannot predict the engine's moves, or another person's moves, in
  advance.
- **Do not poll the turn in a tight loop.** `state.turn` (from
  `GET /api/game`) names the side to move. If it is not your turn,
  use `GET /api/game/wait` instead of polling — it blocks until your
  turn comes up. See section 4.3. This is separate from the `for`-loop
  ban above. That rule bans a code loop that submits moves. This rule
  bans a code loop that checks the same state over and over.  Use
  of `sleep` is utterly forbidden.
- **Always narrate your thinking to the user.** Say something after
  you see an opponent's move, and again before you submit your own
  move. Section 4.1 gives the exact points to do this at. This is
  separate from in-game chat (next bullet): narration is what you
  tell the person in this conversation. Narrating is not the same as
  stopping — keep narrating and playing without a pause for user
  input, per the second bullet above.
- **`chat` and `reasoning` are optional at the API level, but this
  skill requires both on every move.** Send one of each with every
  `POST /api/game/move` call. `chat` is for banter and trash talk
  only — never put strategy in it, since your opponent and anyone at
  the board viewer read it. `reasoning` is where your strategy and
  analysis go — it stays private while the game is in progress.
  Display name (also in section 2) stays optional.
- **You can "phone a friend" for a move recommendation, since you are
  always `"api-user"`.** This asks GNU Chess for its move choice in
  the current position, without submitting that move or ending your
  turn. Each game gives you a small budget of level-10 and level-5
  queries — 1 and 2 by default. See section 4.5.
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
- If you are playing, pick your color and set the other side:
  - You vs. the engine, you as White: `{"white": "api-user", "black": "engine"}`
  - You vs. the engine, you as Black: `{"white": "engine", "black": "api-user"}`
  - You vs. another API user: `{"white": "api-user", "black": "api-user"}`
  - You vs. a person on the board viewer: set the other side to
    `"web-user"`, for example `{"white": "api-user", "black": "web-user"}`.
- `level` (optional, `1`-`10`, weakest to strongest, default `5`) sets
  the difficulty for both sides at once. It matters only for a side
  that is `"engine"`. `white_level` and `black_level` (each optional,
  `1`-`10`) set one side's difficulty alone, and win over `level` for
  that side — use them for two engines at different strengths (see
  below). Omit a level to keep its last value. `GET /api/engine-levels`
  lists what each level means. If the user asks for an easier or
  harder opponent, or names a rough difficulty such as "beginner" or
  "hard", set the level to match. Do not guess at move quality
  yourself. You can also change a level at any time, without a new
  game: `POST /api/game/level {"level": N, "color": "white"}` (omit
  `"color"` to set both sides).
- If `white` is `"engine"` and `black` is not, the engine plays its
  first move at once. The response holds this move in `engine_move`.
  Read it before you do anything else — if you are Black, this is the
  move you respond to.
- `white_name`/`black_name` (each optional) set that side's display
  name for this game. Names never carry over from the last game —
  every new game starts with no names set. See section 2 to learn
  what a name does, and how to set or change one later, even for a
  game you did not start.
- `friend_level5_limit`/`friend_level10_limit` (each optional,
  integers, default `2` and `1`) set this game's "phone a friend"
  budget for whichever side ends up `"api-user"` — see section 4.5.
  Like the name fields, these do not carry over: every new game gets
  the defaults unless you set them here, and usage always starts at
  zero. Raise them if the user wants more hints, or set either to `0`
  to turn that tier off.
- **Two engines can play each other, for a user who wants to watch
  instead of play.** Set both `white` and `black` to `"engine"`, with
  different `white_level`/`black_level` if you want. Neither side
  calls `POST /api/game/move`. Do not start this setup if the user, or
  you, want to play. Section 4 does not apply to this kind of game. It
  plays itself out in the background, at its own pace. Watch it with
  `GET /api/game` polling (section 4.3) or the board viewer's event
  stream, like any other game.

The response is `201` with `{"state": {...}, "engine_move": {...} | null}`.
Keep `state`: `state.turn` names the side to move next.

## 2. Setting your name, chatting, and recording your reasoning

Display name is optional and cosmetic — skip it unless the user asks
for one. `chat` and `reasoning` are different: the API marks both
optional, but this skill requires you to send both with every move.
They serve opposite purposes — see below. None of the three affects
move legality or turn order.

**Display name.** Set one with:

```
POST /api/game/name
Content-Type: application/json

{"color": "white", "name": "Deep Purple"}
```

- `color` is `"white"` or `"black"` — whichever side you are playing.
- `name` can be up to 40 characters. Longer text is cut short, not
  rejected. Send an empty string to clear the name — the viewer then
  shows only your type (`"api-user"`).
- This works whether or not a game is running, and takes effect at
  once. Use it to set your name before your first move in a game you
  join (section 3). `white_name`/`black_name` on `POST /api/game`
  (section 1) sets a name only when you create that game.
- Once set, your name shows in the board viewer and on each of your
  move-log entries (see `name` in section 4.2). It does *not* carry
  over to the next game. Set it again each time — with
  `white_name`/`black_name` at game start, or with this endpoint
  after.

**Chat attached to a move.** Attach a short line to a move with the
`chat` field on `POST /api/game/move` (section 4.2). Send one with
every move — this skill requires it, even though the API accepts a
move without one. There is no standalone chat channel — every chat
line goes out with a move:

```json
{"move": "e2e4", "chat": "Good luck!"}
```

- `chat` is for banter and trash talk only. Never put strategy, a
  plan, or analysis in it — your opponent and anyone at the board
  viewer read it right away. Put strategy in `reasoning` instead
  (below).
- `chat` can be up to 240 characters. Longer text is cut short.
- There is no separate inbox. The API stamps your chat onto that
  move's entry in `move_log`. Your opponent sees it the next time
  they read the game state: in the response to their own next move,
  or in a plain `GET /api/game` call. A person at the board viewer
  sees it in a chat panel, next to the move.
- To read a chat line from your opponent, check their latest
  `move_log` entry for a `chat` field. This is the same place you
  check to see what move they made (section 4.1, step 3).
- For a line not tied to a move — a greeting, or "gg" at the end —
  attach it to a move you submit anyway. Use your first move, or your
  last move before you resign. You cannot send chat without a move.

**Private reasoning.** `reasoning` (up to 1000 characters, also cut
short, not rejected) is a second field on `POST /api/game/move`,
alongside `chat`. Send one with every move — this skill requires it,
even though the API accepts a move without one:

```json
{"move": "e2e4", "reasoning": "e4 grabs the center and opens lines for the bishop and queen."}
```

- `reasoning` holds your strategy: the analysis and plan behind the
  move. This is the opposite of `chat` — keep strategy out of `chat`,
  and keep banter out of `reasoning`.
- Unlike `chat`, no endpoint returns `reasoning` while the game is in
  progress. The server keeps it alone — not shown to your opponent,
  anyone at the board viewer, or even back to you on a later read.
  One exception: once the game ends, it goes into that game's PGN
  transcript (section 5.1), since no advantage is left to protect.
- This is not the plain-language explanation you give the user before
  each move (section 4.1, step 4). Give both — one does not replace
  the other. Narration talks to the user in this conversation.
  `reasoning` is a permanent record kept on the server.

## 3. Joining a game already in progress

The API has no login or seat system. "Joining" means: check the
current state, find the open side, and start submitting moves for it.
You can join as White or as Black — the steps are the same, only the
color name changes.

1. Check whether a game exists, and what it looks like:
   ```
   GET /api/game
   ```
   A `404` means no game has started. Go to section 1, and set
   yourself, `"api-user"`, as whichever color the user wants.

2. Read `state.players`, for example
   `{"white": "api-user", "black": "engine"}` — this names the type of
   each side. Find the color the user wants, then check its type:
   - If the type is already `"api-user"` and no one else acts for it,
     you can play it. Go to step 3.
   - If the type is `"engine"` or `"web-user"`, you cannot take over
     that side. Tell the user, and offer to start a new game instead
     (section 1) with your color set correctly.
   - If both colors show `"engine"`, this is a watch-only game between
     two engines (see the note in section 1). No color is joinable.
     Tell the user, and offer a new game instead.

3. Read `state.status` and `state.game_over`. If the game already
   ended, do not submit a move — report the result instead (section 5),
   and offer a new game.

4. Read `state.turn` — the color to move now, not necessarily your
   color. If it equals your color, go to section 4 and play your
   move. If not, wait for your turn (section 4.3).

A two-API-user game (`state.players` shows `"api-user"` for both
colors) needs one more check: the state does not say which caller
owns which color, since there is no login. Ask the user which color
they want you to play, and submit moves only when `state.turn`
matches that color.

## 4. Playing the game

### 4.1 The move loop

This is a loop you perform yourself, turn by turn — never a `for`
loop or any other code loop. Make one tool call at a time, and think
about each move before you submit it. Do not write a script that
plays several moves in a row without a stop for thought between them.

Repeat this loop for each of your turns:

1. Query legal moves with curl:
   ```bash
   curl http://10.0.2.2:5003/api/game/legal-moves
   ```
2. Query the board state if you need a reminder. This step is
   optional — use it when time has passed since your last check, or
   after an opponent's move you have not seen:
   ```bash
   curl http://10.0.2.2:5003/api/game
   ```
3. Check for an opponent move since your last turn: a new
   `engine_move`, or a new `move_log` entry from your opponent. If you
   find one, tell the user what you think it does, in plain language,
   before you plan your own move. Also check that entry for a `chat`
   field (section 2) and react to it if present.
4. Choose a legal move. Before you submit it, tell the user the idea
   behind it: what it does for your position, and why you picked it
   over the alternatives. Give this explanation every time, not only
   for unusual moves. Also write your `reasoning` (your strategy —
   section 2) and a `chat` line (banter only, no strategy — section
   2). This skill requires both on every move.
5. Submit the move with curl, `chat` and `reasoning` included:
   ```bash
   curl -X POST http://10.0.2.2:5003/api/game/move \
     -H 'Content-Type: application/json' \
     -d '{"move": "e2e4", "chat": "Good luck!", "reasoning": "e4 grabs the center and opens lines for the bishop and queen."}'
   ```
6. Repeat from step 1 until the game ends (section 5). Do this on
   your own, one turn at a time, with no stop for user input between
   moves. Stop only if the user explicitly tells you to stop.

A non-null `engine_move` in the response is the engine's reply. Treat
it like any opponent move: narrate it (step 3) at the start of your
next loop, before you plan your reply. In a two-API-user or
`"web-user"`-opponent game, wait for the other side instead (section
4.3), then narrate their move (step 3) as soon as you see it in
`move_log`.

### 4.2 Move submission details

Legal moves (from step 1 above) have this form:

```
GET /api/game/legal-moves            # all legal moves for the side to move
GET /api/game/legal-moves?from=e2    # only moves that start on e2
```

Each entry has `uci` (for example, `"e2e4"`, or `"e7e8q"` for
promotion), `san` (for example, `"e4"`, `"Nf3"`, `"O-O"`), `from`,
`to`, and `promotion`.

Submit a move. UCI and SAN both work — use UCI, since it has only one
meaning. `chat` and `reasoning` (section 2) attach a chat line and a
private note. The API marks both optional, but this skill requires
you to send both, every time: `chat` for banter, `reasoning` for your
strategy.

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

- `move` echoes back what you submitted, plus `name` (your display
  name, or `null` if unset — section 2) and `chat` (only if you sent
  one).
- `engine_move` is set (non-null) only when it becomes an `"engine"`
  side's turn right after your move. The API computes and applies the
  engine's reply in this same call. This is the opponent's reply —
  narrate and respond to it next (section 4.1, step 3).
- `state` is the full game state after both moves apply. Always read
  `turn` and `status` from it. Do not assume their values. It also
  carries `player_names`: `{"white": name_or_null, "black": name_or_null}`
  (section 2).
- `400` means the move was illegal or malformed, or it was not an
  `"api-user"`/`"web-user"` turn. Read the `error` field, then correct
  your next call — for example, fetch legal moves again, or check the
  turn again.

### 4.3 Waiting for the other side

If it is not your turn, wait for your opponent (an `"api-user"` or
`"web-user"`, not the engine) with one blocking call, instead of a
poll loop. Make this call yourself, on your own, as the next step in
the loop — this is not a stop for user input. Tell the user you are
waiting, then keep going once your turn comes up:

```
GET /api/game/wait?color=white&timeout=25
```

- `color` is your color. This call blocks until it becomes that
  color's turn, the game ends, or `timeout` seconds pass (optional,
  default 25, capped at 55) — whichever comes first. It returns
  `{"state": {...}}`.
- It returns at once, with no wait, if it is already your turn, the
  game already ended, or no game has started.
- A timeout looks the same as any other return. Check `state.turn`
  and `state.game_over` yourself. If neither changed, call it again.
- This call blocks your whole turn — you cannot do anything else in
  the conversation until it returns. Tell the user you are waiting
  for their move before you make this call.

To keep the conversation moving while you wait, poll `GET /api/game`
instead, with a real pause between calls. Make each check its own
tool call — never a `for` loop that polls in code:

```
GET /api/game        # check, think for a few seconds, check again — not a rapid loop
```

Each time you check and it is still not your turn, spend a few
seconds on something useful. Think ahead about a likely opponent
reply, note your plan, or tell the user you are waiting. Then check
again.

Either way, stop as soon as `state.turn` becomes your color, or
`state.game_over` becomes `true`. A person at the board viewer sees
updates at once, over Server-Sent Events at
`GET http://10.0.2.2:5004/events`. That stream serves the viewer page
and is not a control endpoint, but you can read it too if you prefer
it to the options above.

### 4.4 Watching without playing

`GET http://10.0.2.2:5004/` is an HTML board for a user who wants to
watch. It updates live over Server-Sent Events, with no manual
refresh. A person there can also:

- start a game
- play a `"web-user"` side by clicking the board
- type a chat line to go out with their next move
- end the game with a Resign or Restart button

That is a person acting through the browser, not you. Always use the
REST API (port 5003) when you act on the game.

### 4.5 Phoning a friend

You can ask GNU Chess for its move choice in the current position,
without submitting that move. Use it as a hint for a hard decision,
not as a substitute for choosing and submitting your own move
(section 4.1, steps 4-5). It is available only to you, the
`"api-user"` side, and only on your own turn.

```
POST /api/game/phone-a-friend
Content-Type: application/json

{"level": 10}
```

- `level` is `5` or `10` — no other value. Level 10 searches deeper
  and gives a stronger recommendation. Level 5 is faster and weaker.
  Pick level 10 for a critical, hard-to-read position. Level 5 is
  enough for a routine check.
- Each level has its own budget for the whole game, set at game start
  (`friend_level5_limit`/`friend_level10_limit`, section 1 — default
  `2` for level 5, `1` for level 10). Each side has its own budget,
  so in a two-`"api-user"` game your budget does not depend on your
  opponent's.
- Calling this does **not** change the board, end your turn, or count
  as your move. You must still submit your own move with
  `POST /api/game/move` (section 4.2) after, whether or not you take
  the suggestion.

Response:

```json
{
  "advice": {"level": 10, "uci": "g1f3", "san": "Nf3", "color": "white", "used": 1, "limit": 1, "remaining": 0},
  "state": {...}
}
```

- `advice.uci`/`advice.san` is the recommended move, in both
  notations (section 4.2 explains the difference). `advice.used`,
  `advice.limit`, and `advice.remaining` give the budget used, the
  total, and what remains.
- `state` is the full, current game state — unchanged by this call,
  except `state.phone_a_friend`. That field always shows both sides'
  budget and usage at both levels, whether or not you have called
  this endpoint yet:
  ```json
  "phone_a_friend": {
    "limits": {"level_5": 2, "level_10": 1},
    "white": {"used": {"level_5": 0, "level_10": 1}, "remaining": {"level_5": 2, "level_10": 0}},
    "black": {"used": {"level_5": 0, "level_10": 0}, "remaining": {"level_5": 2, "level_10": 1}}
  }
  ```
- `400` means one of these:
  - it was not your turn
  - your side was not `"api-user"` (this will not happen — you are
    always `"api-user"`)
  - `level` was not `5` or `10`
  - you had no queries left at that level

  Read the `error` field. If you are out of budget at one level, try
  the other level, if you still have queries there, or decide on your
  own.
- Using this is optional. Call it only when it will truly help, in a
  hard or unclear position — not on every move, since your budget is
  small by design. When you use it, mention to the user that you
  asked for a hint, as part of your normal narration (the "always
  narrate" core fact).

## 5. Recognizing the end of a game

After every move, or every poll, check the `state` object's
`game_over` and `status` fields. Do not guess the end of the game from
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
  winner directly — do not compute it yourself.

- Once `game_over` is `true`, `POST /api/game/move` starts to return
  `400` ("game is not in progress"). Use this only as backup, if you
  missed the state check. Check `state` directly — do not rely on the
  `400` as your main signal.
- To resign on behalf of a side, and end the game early:
  ```
  POST /api/game/resign
  {"player": "white"}
  ```
- When a game ends, report the result to the user in plain language —
  for example "Checkmate — black wins" or "Draw by stalemate" — not
  the raw status string. Offer to start a new game (section 1) if the
  user wants to keep playing.

### 5.1 Downloading a transcript

Once the game has ended, and only then, a PGN (Portable Game
Notation) transcript is available. PGN is the standard plain-text
chess format read by lichess.org, chess.com, and most chess software:

```
GET /api/game/transcript
```

- Returns raw PGN text, not JSON: metadata as tag pairs at the top
  (players, result, engine levels where relevant, how the game
  ended), then the move list.
- Every move's `chat` (section 2) and any private `reasoning` you
  recorded for it (also section 2) appear as a comment on that move.
  This is the only place `reasoning` is ever exposed — once the game
  is over, no advantage is left to protect.
- `400` means no game has started, or the current game is still in
  progress. This only works once `state.game_over` is `true`.
- Use this endpoint when the user asks for a copy of the game, a way
  to review it later, or their own private reasoning written out.
  Fetch it and share the contents, or save it to a file such as
  `game.pgn`, if you have file access.
