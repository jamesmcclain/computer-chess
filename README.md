# computer-chess

computer-chess is a dockerized chess server. Two chess engines run
inside an Ubuntu container: GNU Chess and Stockfish, equally
supported everywhere an "engine" side is — either can play either
color, alone or against each other. A JSON REST API controls the
game. A side is an API user (an outside caller that submits moves
through the API), a web user (a person playing through the board
viewer), or an engine. A separate web page shows the current board,
and also lets a person start a game or play a web-user side.

Only one game is active at a time. A new game replaces the previous
game. No endpoint uses authentication.

## For agents: the `computer-chess/` skill

This README is the endpoint reference — every request field, every
response shape. It is not the place to learn how to *play* a game.

`computer-chess/` is an agent skill covering that:

```
computer-chess/
├── SKILL.md                     the move loop and the rules that matter
├── scripts/chess.py             a CLI wrapper over the endpoints below
└── references/                  loaded only when the topic comes up
    ├── setup.md                 starting and joining games
    ├── trainee.md               the api-trainee discipline
    ├── phone-a-friend.md        hints and position evaluations
    ├── endgame.md               results, resigning, the PGN transcript
    └── rest-api.md              calling the API without the script
```

`scripts/chess.py` is the recommended way to drive the API. It needs
only the standard library, makes the same calls documented below, and
prints a short digest instead of the raw JSON:

```bash
python3 computer-chess/scripts/chess.py turn --side white
python3 computer-chess/scripts/chess.py move --side white e2e4 \
  --chat "Good luck!" --tactical "..." --strategic "..."
```

The required `--side` is not decoration. Because no endpoint uses
authentication, a move sent during the wrong side's turn is accepted
and applied in a game where both sides are `api-user`. The script
checks whose turn it is and refuses. Anything calling these endpoints
directly has to make that check itself.

## Quick start

Build and run the image:

```bash
docker build -t computer-chess .
docker run -it --rm -p 5003:5003 -p 5004:5004 computer-chess
```

Or run `./run.sh` instead.

Then use these addresses:

- REST API: `http://localhost:5003/api` (see below).
- Board viewer: `http://localhost:5004/`.

## REST API (port 5003)

All request bodies and response bodies use JSON. `GET /api` returns this
same reference as JSON.

### `POST /api/game` — start a new game

```json
{"white": "api-user", "black": "engine", "level": 10}
```

`white` and `black` are each one of four types: `"api-user"` (an API
user that submits moves through this API), `"api-trainee"` (see
below), `"engine"` (GNU Chess or Stockfish — see `engine` below), or
`"web-user"` (a person playing through the board viewer on port 5004,
by clicking the board). Every combination is supported, including two
engines. When both sides are `"engine"`, the two engines play each
other. This game needs no further calls. It plays itself out in the
background, one paced move at a time, so it streams to the board
viewer like any other game.

`"api-trainee"` behaves exactly like `"api-user"` — same REST calls,
same responses — except it enforces a discipline on top: every move
must be preceded by a `POST /api/game/phone-a-friend` call, for as
long as that side still has any phone-a-friend budget left (see
`friend_level10_limit` etc. below), and must include both
`tactical_reasoning` and `strategic_reasoning` (see
`POST /api/game/move` below, where they're optional for every other
type). Skipping either forfeits the game immediately: the submitted
move is discarded — never applied to the board — and the game ends on
the spot with status `"forfeited"` and the other side declared the
winner. There is no warning and no second attempt; a trainee side gets
exactly one chance per move to follow the process.

`level` (optional, `0`-`20`, weakest to strongest — Stockfish's own
native "Skill Level" scale) sets the difficulty for both sides at
once. `white_level` and `black_level` (each optional, `0`-`20`) set
one side's difficulty on its own, and win over `level` for that side.
Use them to give the two engines in an engine-vs-engine game different
strengths. Omit a level to keep its last value (default `10`). See
`GET /api/engine-levels` and `POST /api/game/level` below.

`engine` (optional, `"gnuchess"` or `"stockfish"`) picks which engine
plays both `"engine"` sides at once. `white_engine` and `black_engine`
(each optional) pick one side's engine on its own, and win over
`engine` for that side. Use them to pit GNU Chess against Stockfish.
Omit an engine choice to keep its last value (default `"gnuchess"`).

`white_name` and `black_name` (each optional, up to 40 characters) set
that side's display name for this game. Names never carry over from a
previous game — every new game starts with neither side named; omit
either to leave that side without a name. See `POST /api/game/name`
below to set or change a name for a game already in progress.

`friend_level10_limit` and `friend_level20_limit` (each optional,
integers `0`-`50` or `-1` for unlimited, default `2` and `1`
respectively) set this game's "phone a friend" budget: how many
level-10 and level-20 engine hints an `"api-user"`/`"api-trainee"`
side may request over the course of the game, for *every* engine at
once. Each engine's
quota is tracked separately, not pooled — `friend_limits` (optional,
an object of the form `{engine_name: {tier: limit}}`, e.g.
`{"stockfish": {"10": 5}, "gnuchess": {"20": 0}}`) sets one or more
engines' budgets at one or both tiers specifically, and wins over the
generic fields for whichever engine/tier it names — so a
side can be given, say, 5 GNU Chess hints and 1 Stockfish
hint, independent of each other, and this scales to however many
engines the server supports. Any limit, generic or per-engine, can be
`-1` instead of a number, which makes that tier (for that engine, or
for every engine via the generic fields) unlimited for the game — the
query never fails for running out. Like the name fields above, none
of these are sticky — every new game gets the defaults shown above
unless overridden here,
and usage always resets to zero.

`friend_eval_limit` (optional, `0`-`50` or `-1` for unlimited, default
`1`) is a separate budget again, for the `"eval"` kind of
phone-a-friend query — a full-strength Stockfish assessment of who is
winning, rather than a move recommendation. It has no engine choice and
no tier, so it sits outside `friend_limits`' `{engine: {tier: limit}}`
grid and is set on its own. It resets per game like the rest. See
`POST /api/game/phone-a-friend` below.

If `white` is `"engine"` and `black` is not, that engine plays its
opening move immediately. The response returns this move as
`engine_move`.

Response: `201` with `{"state": {...}, "engine_move": {...} | null}`.

### `GET /api/game/legal-moves` — legal moves for the side to move

The optional query parameter `from=e2` limits the result to moves that
start on that square.

`format` (optional, `compact` or `full`, default `compact`) picks the
shape of `moves`. The default returns one space-separated string of UCI
moves — each is directly usable as the `move` field of
`POST /api/game/move`, and the whole response is roughly a tenth the
size of the `full` form:

```json
{"moves": "e2e4 e2e3 g1f3 b1c3 ...", "count": 20}
```

`format=full` returns the expanded form, where `from`, `to` and
`promotion` restate parts of `uci` and `san` gives the same move in
standard algebraic notation:

```json
{"moves": [{"uci": "e2e4", "san": "e4", "from": "e2", "to": "e4", "promotion": null}, ...], "count": 20}
```

### `GET /api/engine-levels` — the difficulty scale and engine names

```json
{"min": 0, "max": 20, "default": 10, "engines": ["gnuchess", "stockfish"]}
```

Both engines share one difficulty scale, `0` (weakest) to `20`
(strongest) — Stockfish's own native "Skill Level" UCI option, applied
to it directly. GNU Chess has no such option, so its difficulty is
approximated the standard way for a UCI engine without one: the same
0-20 level is used to derive a search-depth cap, with a time limit as
a safety net rather than the primary lever. In practice, the same
level plays noticeably stronger on Stockfish than on GNU Chess — the
scale is shared so a level number always means "weaker" or "stronger"
in the same direction on both engines, not that the two engines are
strength-matched at the same number.

### `POST /api/game/level` — change the difficulty of an engine side

```json
{"level": 16, "color": "black"}
```

Difficulty is set per side, not per game, so an engine-vs-engine game
can have two different strengths. Omit `color` to set both sides at
once. This is all that matters for a game with only one `"engine"`
side. This endpoint works with or without a game in progress. The new
level applies to that side's next move. Response:
`{"levels": {"white": 10, "black": 16}}`.

### `POST /api/game/engine` — change which engine plays an engine side

```json
{"engine": "stockfish", "color": "black"}
```

Which engine (`"gnuchess"` or `"stockfish"`) plays an `"engine"` side
is set per side, not per game, so an engine-vs-engine game can pit the
two engines against each other. Omit `color` to set both sides at
once. This endpoint works with or without a game in progress. The new
engine applies to that side's next move. Response:
`{"engines": {"white": "gnuchess", "black": "stockfish"}}`.

### `POST /api/game/name` — set or clear a side's display name

```json
{"color": "white", "name": "Deep Purple"}
```

`color` is `"white"` or `"black"`. `name` is up to 40 characters,
trimmed rather than rejected if longer. An empty `name` clears it back
to showing just that side's type. This name is shown in the board
viewer and stamped onto that side's `move_log` entries from then on
(see `name` under `GET /api/game` below). It applies only to the
current game — a new game always starts with neither side named (see
`POST /api/game` above), regardless of what was set here previously.
Use this to set a name after a game has already started — for
example, an API user joining a game they did not start can set a name
here, since the `white_name`/`black_name` fields on `POST /api/game`
only apply when that game is created. Works with or without a game
running. Response: `{"player_names": {"white": "Deep Purple", "black": null}}`.

### `GET /api/eval-qualities` — the eval bar's speed/accuracy trade-offs

```json
{
  "default": "balanced",
  "qualities": [
    {"id": "off", "label": "Off", "description": "No eval bar. Stockfish does no extra work for it."},
    {"id": "fast", "label": "Fast", "description": "Updates almost instantly. The assessment is shallow and can be noisy."},
    {"id": "balanced", "label": "Balanced", "description": "A good default: updates quickly and is accurate enough for most positions."},
    {"id": "deep", "label": "Deep", "description": "Slower to update, especially during a fast engine-vs-engine game. The most accurate assessment."}
  ]
}
```

The eval bar is a live Stockfish read on who is winning, shown as a
vertical bar in the board viewer — it is not part of the JSON API's
`GET /api/game` response (see below). It runs on its own dedicated
Stockfish process, always at full strength — entirely separate from any
engine playing a side of the game or answering a
`POST /api/game/phone-a-friend` query, so it never shares a Skill Level
setting or slows down a move. `quality` (see
`POST /api/game/eval-quality` below) trades update latency against
accuracy; each entry's `description` is meant to be shown directly to a
person choosing between them, not just read in this reference.

### `POST /api/game/eval-quality` — set the eval bar's quality

```json
{"quality": "fast"}
```

`quality` is one of the `id`s from `GET /api/eval-qualities` above.
`"off"` turns the eval bar off entirely — no extra Stockfish work is
done. Sticky, like `POST /api/game/level`/`POST /api/game/engine`:
applies from now on regardless of whether a game is running, and
survives to the next game. Response: `{"eval_quality": "fast"}`.

### `GET /api/game` — current state

This endpoint returns the position, the side to move, the game status,
and more. Check the `turn` field to find the side to move.

Responses are trimmed for size by default, since every byte here is
paid for twice — once on the wire, and again in the context window of
whatever is reading it. Two fields are left out: the 8x8 `board` grid,
which re-encodes the position already given by `fen` and `board_ascii`,
and `move_log`, whose replacement is the O(1) `last_move`.
`phone_a_friend` is sent in a compact form (below). Add `?verbose=1` to
this or any other endpoint that returns a state to get the `board` grid
and the expanded `phone_a_friend` breakdown back.

```json
{
  "started": true,
  "status": "in_progress",
  "game_over": false,
  "winner": null,
  "turn": "white",
  "in_check": false,
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "board_ascii": "r n b q k b n r\n...",
  "players": {"white": "api-user", "black": "engine"},
  "player_names": {"white": "Deep Purple", "black": null},
  "engine_levels": {"white": 10, "black": 10},
  "engine_names": {"white": "gnuchess", "black": "stockfish"},
  "phone_a_friend": {
    "white": {"gnuchess": "2/1", "stockfish": "2/1", "stockfish_eval": "1"},
    "black": {"gnuchess": "2/1", "stockfish": "2/1", "stockfish_eval": "1"}
  },
  "fullmove_number": 1,
  "halfmove_clock": 0,
  "last_move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"}
}
```

`status` is one of: `not_started`, `in_progress`, `checkmate`,
`stalemate`, `draw_insufficient_material`, `draw_75_moves`,
`draw_5fold_repetition`, `draw_claimable_50_moves`,
`draw_claimable_threefold_repetition`, `resigned`, `aborted`,
`forfeited` (an `"api-trainee"` side skipped a required
phone-a-friend call or reasoning field — see `POST /api/game` and
`POST /api/game/move`).

`engine_names` shows which engine (`"gnuchess"` or `"stockfish"`)
plays each `"engine"` side — see `POST /api/game/engine` above. Its
entry for a non-`"engine"` side has no meaning.

`phone_a_friend` shows how many hints each side has left, broken out
per engine — GNU Chess hints and Stockfish hints draw on independent
quotas, not a shared one — plus `stockfish_eval`, the separate budget
for the `"eval"` kind of query (see
`POST /api/game/phone-a-friend` below). Each engine value is the count
*remaining* at each tier, joined by a slash in tier order
(`"level_10/level_20"`), where `-1` means unlimited. `stockfish_eval`
has a single tier, so it is one bare number. So `"2/1"` is two level-10 hints and one level-20
hint still available, and `"0/0"` means that engine is exhausted. See
`POST /api/game/phone-a-friend` below. Only an `"api-user"`/
`"api-trainee"` side can use it, but the field is always present,
whatever the usage and whoever is playing, so anyone reading the state
can see the budget — an `"api-trainee"` side in particular must check
it before every move.

`?verbose=1` replaces this with the expanded form, which additionally
carries the `limits` set at `POST /api/game` time and each side's `used`
counts:

```json
"phone_a_friend": {
  "limits": {
    "gnuchess": {"level_10": 2, "level_20": 1},
    "stockfish": {"level_10": 2, "level_20": 1},
    "stockfish_eval": {"eval": 1}
  },
  "white": {
    "gnuchess": {"used": {"level_10": 0, "level_20": 0}, "remaining": {"level_10": 2, "level_20": 1}},
    "stockfish": {"used": {"level_10": 0, "level_20": 0}, "remaining": {"level_10": 2, "level_20": 1}},
    "stockfish_eval": {"used": {"eval": 0}, "remaining": {"eval": 1}}
  },
  "black": {"...": "same shape"}
}
```

There is no `eval` field in this JSON API response — the eval bar (see
`GET /api/eval-qualities` and `POST /api/game/eval-quality` above) is a
board-viewer-only feature for spectators.

`last_move` is the most recent entry from the game's internal move
log, or `null` before any move has been made. Its `name` is that
side's display name at the time of the move, or `null` if none was
set (see `POST /api/game/name` above). Its `chat` is present only if
that move carried a chat line (see `POST /api/game/move` below).
There is no standalone chat channel — every chat line belongs to a
move. This endpoint, `POST /api/game/move`, `POST
/api/game/phone-a-friend`, and `GET /api/game/wait` all return only
this one entry, not the full move log, so their response size stays
constant no matter how long the game runs — safe to call once per
move in a loop.

The five fields that cannot change while a game runs — `started`,
`players`, `player_names`, `engine_levels` and `engine_names` — are
returned by this endpoint and by `POST /api/game`, the two calls whose
job is to establish where things stand. They are left out of the
per-move responses (`POST /api/game/move`,
`POST /api/game/phone-a-friend`, `GET /api/game/wait`,
`POST /api/game/resign`, `POST /api/game/abort`), where repeating them
every turn would say nothing new. `?verbose=1` restores them. The board viewer (port 5004) is the one place the
full move log is exposed, since it renders the whole game's history
and chat.

If no game has started, this endpoint returns `404`.

CAUTION: Do not poll this endpoint in a tight loop to check the turn.
`GET /api/game/wait` (below) blocks until it is your turn, which is
simpler and faster than polling. If you do poll, space out requests
by at least two seconds. A person at the board viewer gets updates
over the SSE stream on port 5004 (below) instead, the instant the
game changes.

### `GET /api/game/wait` — block until it is your turn

```
GET /api/game/wait?color=white&timeout=25
```

`color` (required) is the side to wait for. `timeout` (optional
seconds, default 25, capped at 55) bounds how long the request can
block. It returns immediately, without blocking, if it is already
`color`'s turn, the game has ended, or no game has started.

The response tells you which of those happened, so you do not have to
work it out from the state yourself. When there is something to act on
— it is now `color`'s turn, or the game ended — you get the state:

```json
{"changed": true, "state": {...}}
```

When the timeout simply expired with the position unchanged, you get a
minimal response instead, because sending a full state to report that
nothing happened is the most wasteful thing this API can do — and a
caller waiting on a slow human opponent may collect several in a row:

```json
{"changed": false, "turn": "black", "game_over": false}
```

Branch on `changed`, and call again when it is `false`.

### `POST /api/game/move` — submit a move

```json
{"move": "e2e4", "chat": "Good luck!", "tactical_reasoning": "no immediate tactics", "strategic_reasoning": "e4 grabs the center"}
```

This endpoint accepts UCI notation (`e2e4`, `e7e8q` for promotion) or
SAN (`e4`, `Nf3`, `O-O`). The move applies to the side with the current
turn. The caller does not name the color, because only one side can
move at a time. If it becomes an engine's turn next, the server
computes and applies its reply immediately.

`chat` (optional, up to 240 characters, trimmed rather than rejected
if longer) attaches a short chat line to this move. There is no
separate delivery step, and no standalone chat channel — this is the
only way to send chat. The chat is stamped onto this move's
`move_log` entry, along with the mover's current display name. The
opponent sees both the next time they read the game state — for
example, the response to their own next move. A person watching the
board viewer sees it there too, next to the move.

`tactical_reasoning` and `strategic_reasoning` (up to 1000 characters
each, also trimmed rather than rejected if longer; optional for
`"api-user"`/`"web-user"`, **required** for `"api-trainee"` — see
below) are private notes on why this move was chosen —
`tactical_reasoning` for concrete, move-local calculation (captures,
checks, threats), `strategic_reasoning` for the longer-term plan
behind it. Unlike `chat`, neither is ever returned by this or any
other endpoint while the game is in progress. Both are kept
server-side only, for example for later review by whoever is
operating the server. The one exception is `GET /api/game/transcript`
(below): once the game has ended, both are folded into that game's
transcript, since there is no longer any ongoing advantage to protect.

```json
{"move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"},
 "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine", "name": "Stockfish"},
 "state": {...}}
```

This endpoint returns `400` for an illegal or unparseable move, for a
move submitted during the engine's turn, or when no game is in
progress.

**`"api-trainee"` forfeits.** If the side to move is `"api-trainee"`
and it either didn't call `POST /api/game/phone-a-friend` before this
move (while it still had budget left) or omitted
`tactical_reasoning`/`strategic_reasoning`, the move is discarded —
never parsed, never applied to the board — and the game ends
immediately: the other side wins. The response shape is different in
this case:

```json
{"forfeited": true, "by": "white", "reasons": ["no phone-a-friend call before this move, despite having queries left"], "state": {...}}
```

Check for the `"forfeited"` key rather than assuming the ordinary
`{"move", "engine_move", "state"}` shape whenever the mover is
`"api-trainee"`.

### `POST /api/game/phone-a-friend` — ask an engine for help

```json
{"level": 20, "engine": "stockfish"}
{"kind": "eval"}
```

For the `"api-user"`/`"api-trainee"` side to move only. Asks for help
with the current position without submitting a move: the board is
unchanged, your turn does not end, and this is not a substitute for
`POST /api/game/move` — you still submit your own move afterward,
whether or not you act on the answer. For an `"api-trainee"` side, a
successful call here — either kind, any level, any engine — satisfies
that side's phone-a-friend requirement for the move it's about to
submit (see the forfeit rule above); required before every move for as
long as any budget remains.

`kind` (optional, `"move"` or `"eval"`, default `"move"`) picks what
you are asking for. The two draw on separate budgets, because they
answer different questions and spending one should not cost you the
other.

#### `kind: "move"` — what should I play?

`level` is `10` or `20` — these are the only two tiers offered. Each
has its own budget for the game, per engine — set at `POST /api/game`
time (`friend_level10_limit`/`friend_level20_limit` for every engine
at once, or the per-engine `friend_limits` field; default `2` and `1`
respectively) and tracked separately per side, so in a two-API-user
game each caller gets their own budget. A budget can be set to `-1`
for unlimited — then this call never fails for running out of
queries at that tier for that engine. `engine` (optional,
`"gnuchess"` or `"stockfish"`) picks which engine to ask; defaults to
`"gnuchess"` if omitted. GNU Chess hints and Stockfish hints draw on
independent quotas, not a shared one — a side can use
both.

```json
{"advice": {"kind": "move", "level": 20, "engine": "stockfish", "uci": "g1f3", "san": "Nf3", "color": "white", "used": 1, "limit": 1, "remaining": 0},
 "state": {...}}
```

#### `kind: "eval"` — who is winning?

```json
{"kind": "eval"}
```

Asks Stockfish how the current position stands, rather than what to
play. `level` and `engine` do not apply: there is only one tier, and
the answer always comes from Stockfish.

This is the strongest assessment the server can produce. It runs on
the eval bar's dedicated Stockfish process, whose Skill Level is never
lowered from Stockfish's own default, for a full 5 seconds — the same
search budget as a level-20 move hint. Neither side's configured
difficulty affects it. Nor does the eval bar's quality setting: `off`
(see `POST /api/game/eval-quality`) turns off the *spectators'* bar and
has no bearing on whether a player may spend one of their own queries.

```json
{"advice": {"kind": "eval", "engine": "stockfish", "color": "white",
            "score_cp": 34, "mate": null, "pov": "white",
            "eval": "+0.34", "favors": "white",
            "used": 1, "limit": 1, "remaining": 0},
 "state": {...}}
```

`score_cp` is centipawns and `mate` is the number of moves to a forced
mate (`null` when there is none) — and **both are from white's point of
view, whichever side asked**, matching the eval bar and the transcript.
So `-2.50` means black is ahead by about two and a half pawns even when
black is the one who asked. `eval` is the same reading preformatted
(`"+0.34"`, `"#3"`, `"#-2"`), and `favors` is `"white"`, `"black"` or
`"equal"` outright, so the sign cannot be misread. Exactly one of
`score_cp` and `mate` is non-`null`.

Its budget is `friend_eval_limit` at `POST /api/game` time (default
`1`, or `-1` for unlimited), tracked per side, and reported under
`stockfish_eval` in `state.phone_a_friend` rather than beside the
per-engine tiers, since it is not one of them.

#### Errors and budget visibility

Returns `400` if it is not your turn, your side is not `"api-user"`/
`"api-trainee"`, `kind` is not `"move"` or `"eval"`, `level` is not
`10` or `20` on a `"move"` query, `engine` is not a valid engine name,
or you have no queries left in the budget being drawn on. A query that
fails for any of these reasons — including an engine that cannot
analyze the position — costs you nothing.

Current budget and usage for both sides, across both engines and the
`stockfish_eval` budget, is always visible in `state.phone_a_friend`
(see `GET /api/game` above), whether or not you've called this
endpoint yet.

### `POST /api/game/resign` — resign

```json
{"player": "white"}
```

This endpoint ends the game. The API records the other side as the
winner.

### `POST /api/game/abort` — end the game with no winner

No request body. Immediately ends the current game (status `aborted`,
`winner: null`) regardless of player types — unlike `POST /api/game/resign`,
no `player` side is needed, so this also works for an engine-vs-engine
game with no `"web-user"`/`"api-user"` side at all. An engine-vs-engine
game's background autoplay stops within one already-in-flight move of
this call. Returns `400` if no game has started or the current game
has already ended.

### `GET /api/game/transcript` — download a PGN transcript

Only once the game has ended (any status but `not_started`/
`in_progress`). Returns a [PGN (Portable Game
Notation)](https://en.wikipedia.org/wiki/Portable_Game_Notation)
transcript of the game — the standard plain-text chess format read by
lichess.org, chess.com, and most chess software. Response is the raw
PGN text (`Content-Type: application/x-chess-pgn`), not JSON, with a
`Content-Disposition` header so a browser downloads it as a `.pgn`
file rather than displaying it.

Metadata (players, result, engine names and levels where relevant, and
how the game ended) is in the PGN tag pairs at the top. Every move's
`chat` (see `POST /api/game/move` above), any private
`tactical_reasoning`/`strategic_reasoning` recorded for it, and the
eval bar's own read of the resulting position (if the eval bar was on)
are folded in as a PGN comment on that move — chat and reasoning are
otherwise never returned by any endpoint, and the eval bar's live read
is never returned by the JSON API at all (see `GET /api/game` above),
but once the game is over there is no ongoing advantage left to
protect, so the transcript includes whatever read was captured for
each move at the time; a move with the eval bar off, or a read still
pending when a later move superseded it, has no `Eval:` comment. Scores are
pawns from white's point of view (`+0.34` = white better by about a
third of a pawn, `-1.20` = black better by a bit over a pawn), or
`#N`/`#-N` for a forced mate in N by white/black. For example:

```
[Event "computer-chess"]
[Site "?"]
[Date "2026.08.08"]
[Round "-"]
[White "API user"]
[Black "Stockfish"]
[Result "1-0"]
[WhiteType "api-user"]
[BlackType "engine"]
[BlackEngine "stockfish"]
[BlackEngineLevel "10"]
[Termination "checkmate"]

1. e4 {Chat: Good luck! / Tactical: no immediate tactics / Strategic: e4 grabs the center / Eval: +0.34} e5 2. Qh5 Nc6
3. Bc4 Nf6 4. Qxf7# {Chat: gg / Eval: #3} 1-0
```

`include` (optional, `all` or `moves`, default `all`) controls those
per-move comments. The default is the complete annotated transcript
shown above — the reasoning is withheld for the entire game and folded
in here, so dropping it silently would defeat the point of collecting
it. `include=moves` returns bare movetext with the same tag pairs, for
a caller that wants only the moves and does not want a long,
heavily-annotated game's comments (up to 240 + 1000 + 1000 characters
per ply) landing in its context:

```
1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0
```

The board viewer's own "Download transcript" button (port 5004) always
serves the complete annotated transcript and accepts no `include`
parameter — that file is going to disk for a person to keep, so there
is nothing to gain by trimming it.

Returns `400` if no game has started, or the current game is still
in progress.

## Board viewer (port 5004)

`GET /` returns an HTML page. The page shows the current board and
updates live. The browser receives updates through Server-Sent Events
(`GET /events`) the instant the game changes, instead of on a fixed
timer. The page keeps one open connection and repaints only the
squares that changed, so there is no flash or reload.

`GET /state` is also available for a single fetch. The page uses
`GET /state` as a fallback when SSE is not available.

**Downloading a transcript.** Once the game ends, and before a new
one (if any) is started, a "Download transcript (PGN)" button appears
above the start-game form. It downloads the just-finished game from
`GET /game/transcript` (the same underlying call as
`GET /api/game/transcript` above) and disappears again the moment a
new game actually starts.

**Starting a game.** When no game is in progress, including right
after a game finishes, the page shows a form to start one. A person
picks a type for White and a type for Black:

- `api-user` — moves come from the REST API (an agent, or curl).
- `engine` — GNU Chess or Stockfish plays this side (see below).
- `web-user` — the person at this page plays this side, by clicking
  the board (see below).

Each side that is `engine` gets its own engine dropdown (GNU Chess or
Stockfish) and its own difficulty dropdown, so an engine-vs-engine
game can pit two different engines and/or strengths against each
other. Whenever either side is set to `api-user`, the form also shows
two "phone a friend" inputs — the level-20 and level-10 query limits
for this game, applied to both engines at once (see
`POST /api/game/phone-a-friend` above; per-engine budgets can only be
set independently through the REST API), defaulting to `1` and `2`.
This form supports every combination the API supports:

- Two API users.
- An API user against an engine.
- A web user against an engine.
- A web user against an API user.
- Two engines, the same one or different ones. This game plays itself
  out, one paced move at a time, with no further input needed.

**Playing as a web user.** When it is a `web-user` side's turn, the
page lets that person click a piece. Then the person clicks a
highlighted square to move there. If the move is a promotion, the page
shows a small picker for the piece to promote to.

**Names and chat.** A players bar above the board shows each side's
display name, type, and — for an `"engine"` side — which engine it is
and its difficulty level, or — for an `"api-user"` side — its
remaining "phone a friend" budget at each level, per engine. Set a
name with `POST /api/game/name`. The side to move is highlighted. Any move that
carried `chat` (see `POST /api/game/move`) shows up in a chat panel
below the board, next to that move — there is no standalone chat
channel. While it is a `web-user` side's own turn, an input box under
the panel lets that person type a message; it is attached
automatically to whichever move they submit next.

**Resign or restart.** While a game is in progress, a button appears
above the board. If a person is behind either side (at least one
side is `web-user`), it reads "Resign" and ends the game as that
side, the same as `POST /api/game/resign`. If no side is `web-user`
(an api-user/engine or engine-vs-engine game, for example), it reads
"Restart" instead, and ends the game immediately with no winner, the
same as `POST /api/game/abort` — an engine-vs-engine match actually
stops on the spot rather than continuing to play in the background
while a new game's settings are chosen. Either way, the start form
reappears automatically, same as after any other game-ending result.

**Last-move arrow.** After a move, a semi-transparent arrow points
from its start square to its end square, on top of the piece that
moved. This happens for a move by any side, whether that side is a
web user, an API user, or an engine. It updates on every new move,
fades on its own after 60 seconds of no further move, and clears
when a new game starts.

The page's own `/game/start`, `/game/move`, `/game/legal-moves`,
`/game/resign`, `/game/abort`, `/game/chat`, and `/game/eval-quality`
routes back these features. They call the same `ChessGame` object as the REST API
(port 5003), so they enforce the same rules. They exist only so the
page's own JS can act on the game from its own origin. `api.py` (port
5003) stays the reference for programmatic play.

**Eval bar.** A vertical bar next to the board shows the eval bar's
live Stockfish assessment — White's share of the bar grows as White's
position improves, with a numeric read (`+1.3`, or `M4` for mate in 4)
underneath. It runs on its own dedicated Stockfish process, entirely
separate from any engine playing a side of the game or answering a
phone-a-friend query (see `POST /api/game/eval-quality` above), so it
never affects, or is affected by, actual gameplay. A control next to
the board/piece style pickers lets a person choose the eval bar's
speed/accuracy trade-off (see `GET /api/eval-qualities` above) —
Off, Fast, Balanced (the default), or Deep — each with a plain-language
description of the trade-off, so the choice does not require reading
this reference. The setting is server-side and sticky, like engine
level/choice, so it is shared by everyone watching, not a per-browser
preference.

**Appearance.** The page includes controls for the board style and the
piece style.

- Board style: choose one matched square set. A matched set pairs a
  dark texture with a light texture, for example Mahogany with Ash. Or,
  use split mode to choose the dark square and the light square on
  their own.
- Piece style: choose one matched set for both sides. Or, use split
  mode to choose a different set for White and a different set for
  Black.

The options come from `GET /api/catalogue`. This endpoint reads
`static/chess/boards/squares_catalogue.json` and
`static/chess/pieces/pieces_catalogue.json`. See `static/README.md` for
more information.

Each browser remembers its own choice in `localStorage`. The choice is
a display preference, not game state. As a result, two people can watch
the same game with different styles.

If a piece image or the whole `static/chess/` catalogue is missing, the
page falls back to Unicode chess glyphs on a flat background. The game
logic in this repository does not depend on this art.

JS sizes the board with a `ResizeObserver` on its container. The board
scales to the largest square that fits the browser window. As a
result, the board scales smoothly as the window changes size, instead
of jumping between fixed sizes. The board stays on its own
GPU-composited layer (`will-change` plus a null 3D transform), so the
resize stays smooth. This board does not need a full WebGL or three.js
setup, because it is a flat 2D grid of images.

## How it works

- [`python-chess`](https://python-chess.readthedocs.io/) is the source
  of truth for the board, the move rules, and SAN/UCI parsing.
- GNU Chess and Stockfish are the two `"engine"` choices (see `engine`
  under `POST /api/game` above), equally supported and chosen per
  side. The server talks to whichever is assigned to a side over the
  UCI protocol (`gnuchess --uci` / `stockfish`) through `python-chess`'s
  engine interface. Both engines share one 0-20 difficulty scale —
  Stockfish's own native "Skill Level" option, applied directly to it;
  approximated for GNU Chess via a derived search-depth cap, since it
  has no such option of its own (see `GET /api/engine-levels` above).
- `server.py` runs the REST API and the viewer as two Flask apps in one
  process. Both apps share one in-memory `ChessGame` object (`game.py`),
  guarded by a lock. The server needs no database, because it supports
  only one game at a time.

## Files

```
Dockerfile      Ubuntu, gnuchess, stockfish, and the Python/Flask/python-chess image
run.sh          Convenience script to build and run the image
server.py       Entry point: starts the API (port 5003) and the viewer (port 5004)
game.py         Game state, rules, and the GNU Chess / Stockfish (UCI) integration
api.py          REST API routes
viewer.py       Read-only board viewer routes, page, and appearance catalogue
static/chess/   Board-square and piece-set art for the viewer (see its README.md)
```
