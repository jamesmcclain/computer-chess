# computer-chess

computer-chess is a dockerized chess server. Two chess engines run
inside an Ubuntu container: GNU Chess and Stockfish. The server
supports both engines equally for every "engine" side. Either engine
can play either color, alone or against the other engine. A JSON
REST API controls the game. A side is one of three things: an API
user (an outside caller that submits moves through the API), a web
user (a person who plays through the board viewer), or an engine. A
separate web page shows the current board. This page also lets a
person start a game or play a web-user side.

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
only the Python standard library. It makes the same calls documented
below, and prints a short digest instead of the raw JSON:

```bash
python3 computer-chess/scripts/chess.py turn --side white
python3 computer-chess/scripts/chess.py join --side white --name "Deep Purple"
python3 computer-chess/scripts/chess.py move --side white e2e4 \
  --chat "Good luck!" --tactical "..." --strategic "..."
```

`chess.py --help` lists every subcommand. Together, the subcommands
cover the endpoints below: starting and joining games, reading the
position, moving, hints, waiting, renaming, changing difficulty,
resigning, aborting, and the transcript. `turn --side COLOR` prints
the five remaining hint budgets in plain language: L10 GNU Chess, L20
GNU Chess, L10 Stockfish, L20 Stockfish, and Stockfish Eval. `-1`
prints as `unlimited`.

The required `--side` is not decoration. No endpoint uses
authentication. As a result, in a game where both sides are
`api-user`, the server accepts and applies a move sent during the
wrong side's turn. The script checks whose turn it is and refuses a
move on the wrong turn. A caller of these endpoints outside the
script must make that same check.

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

`white` and `black` are each one of five types: `"api-user"` (an API
user that submits moves through this API), `"api-trainee"` (see
below), `"engine"` (GNU Chess or Stockfish — see `engine` below),
`"web-user"` (a person who plays through the board viewer on port
5004, by clicking the board), or `"centaur"` (see below). Every
combination is supported, including two engines. When both sides are
`"engine"`, the two engines play each other, and the game needs no
further calls. The game plays itself out in the background, one
paced move at a time, so it streams to the board viewer like any
other game.

`"api-trainee"` behaves exactly like `"api-user"` — same REST calls,
same responses — but it enforces one extra discipline. Every move
must follow a `POST /api/game/phone-a-friend` call, for as long as
that side still has phone-a-friend budget left (see
`friend_level10_limit` and `friend_level20_limit` below). Every move
must also include both `tactical_reasoning` and
`strategic_reasoning` (see `POST /api/game/move` below, where both
fields are optional for every other type). If a trainee side skips
either requirement, the game forfeits at once: the server discards
the submitted move, never applies it to the board, and ends the game
on the spot. Status becomes `"forfeited"` and the other side wins.
There is no warning and no second attempt. A trainee side gets
exactly one chance per move to follow the process.

`"centaur"` also requires both `tactical_reasoning` and
`strategic_reasoning` on every move, but it never moves the board
directly. An API caller for a `"centaur"` side can only *suggest* a
move, through `POST /api/game/suggest`, not play one — see that
endpoint below. A person at the board viewer (port 5004) then either
accepts the suggestion as it stands, or plays a different legal move
instead. By design, `POST /api/game/move` always fails during a
`"centaur"` side's turn. Unlike `"api-trainee"`, the server rejects a
suggestion that is missing either reasoning field (`400`, nothing
stored, retry freely) instead of a forfeit. Nothing has committed to
the board yet, so there is no wasted turn to punish.

`level` (optional, `0`-`20`, weakest to strongest — Stockfish's own
native "Skill Level" scale) sets the difficulty for both sides at
once. `white_level` and `black_level` (each optional, `0`-`20`) set
one side's difficulty on its own, and win over `level` for that side.
Use them to give the two engines different strengths in an
engine-vs-engine game. Omit a level to keep its last value (default
`10`). See `GET /api/engine-levels` and `POST /api/game/level`
below.

`engine` (optional, `"gnuchess"` or `"stockfish"`) picks which engine
plays both `"engine"` sides at once. `white_engine` and
`black_engine` (each optional) pick one side's engine on its own, and
win over `engine` for that side. Use them to pit GNU Chess against
Stockfish. Omit an engine choice to keep its last value (default
`"gnuchess"`).

`white_name` and `black_name` (each optional, up to 40 characters)
set that side's display name for this game. Names never carry over
from a previous game. Every new game starts with neither side named.
Omit either name to leave that side without a name. See
`POST /api/game/name` below to set or change a name for a game
already in progress.

`friend_level10_limit` and `friend_level20_limit` (each optional,
integers `0`-`50` or `-1` for unlimited, default `2` and `1`
respectively) set this game's "phone a friend" budget. This budget
is how many level-10 and level-20 engine hints an
`"api-user"`/`"api-trainee"`/`"centaur"` side can request over the
game, for *every* engine at once. Each engine's quota is tracked separately,
not pooled. `friend_limits` (optional, an object of the form
`{engine_name: {tier: limit}}`, for example
`{"stockfish": {"10": 5}, "gnuchess": {"20": 0}}`) sets one or more
engines' budgets at one or both tiers specifically. This field wins
over the generic fields for whichever engine and tier it names. For
example, one side can get 5 GNU Chess hints and 1 Stockfish hint,
independent of each other. This pattern scales to however many
engines the server supports. Any limit, generic or per-engine, can
be `-1` instead of a number. `-1` makes that tier unlimited for the
game, for that engine or for every engine through the generic
fields, and the query never fails for running out. Like the name
fields above, none of these fields are sticky. Every new game gets
the defaults shown above unless overridden here, and usage always
resets to zero.

`friend_eval_limit` (optional, `0`-`50` or `-1` for unlimited,
default `1`) is a separate budget, for the `"eval"` kind of
phone-a-friend query. This kind gives a full-strength Stockfish
assessment of who is winning, rather than a move recommendation. It
has no engine choice and no tier, so it sits outside
`friend_limits`' `{engine: {tier: limit}}` grid and is set on its
own. It resets per game like the rest. See
`POST /api/game/phone-a-friend` below.

If `white` is `"engine"` and `black` is not, that engine plays its
opening move immediately. The response returns this move as
`engine_move`.

Response: `201` with `{"state": {...}, "engine_move": {...} | null}`.

### `GET /api/game/analysis` — derived tactical facts

```
GET /api/game/analysis                # for the side to move
GET /api/game/analysis?color=black    # for a named color
```

Reports what the current position holds. A caller does not have to
derive this from the FEN and risk an error:

```json
{
  "color": "white",
  "in_check": false,
  "checkers": [],
  "hanging": {
    "yours": [{"square": "h4", "piece": "Q", "attackers": ["d8"], "defenders": [], "risk": "undefended"}],
    "theirs": [{"square": "f7", "piece": "P", "attackers": ["c4", "h5"], "defenders": ["e8"], "risk": "more attackers than defenders"}]
  },
  "pins": [{"square": "c6", "piece": "N", "color": "black"}],
  "captures": ["c4f7", "h5e5"],
  "checks": ["h5f7", "c4f7"]
}
```

- `hanging.yours` is material the side named by `color` can lose.
  `hanging.theirs` is material it can win. `risk` is one of
  `undefended`, `attacked by a cheaper piece`, or
  `more attackers than defenders`.
- `pins` reports absolute pins against the king, on both sides. The
  report omits a relative pin, for example a pin against a queen,
  because that piece remains legally free to move.
- `captures` and `checks` are the legal moves of each kind for the
  side to move. Both are empty when `color` names the other side.

**`hanging` is a one-ply heuristic, not a static exchange
evaluation.** It counts the direct attackers and defenders of a
square. It does not resolve the full capture sequence. It does not
see x-ray attacks or batteries behind a first attacker. It does not
know whether a defender is itself pinned and unable to recapture.
Treat each entry as a square worth a second look, never as a
verdict.

Returns `404` if no game has started.

### `GET /api/game/legal-moves` — legal moves for the side to move

The optional query parameter `from=e2` limits the result to moves that
start on that square.

`format` (optional, `compact` or `full`, default `compact`) picks the
shape of `moves`. The default returns one space-separated string of
UCI moves. Each move works directly as the `move` field of
`POST /api/game/move`. The whole response is roughly a tenth the
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
(strongest). For Stockfish, the scale applies directly to its own
native "Skill Level" UCI option. GNU Chess has no such option. The
server approximates its difficulty the standard way for a UCI engine
without one. It derives a search-depth cap from the same 0-20 level.
A time limit acts as a safety net, rather than the primary lever. In
practice, the same level plays noticeably stronger on Stockfish than
on GNU Chess. The scale is shared so that a level number always
means "weaker" or "stronger" in the same direction on both engines.
The two engines are not strength-matched at the same number.

### `POST /api/game/level` — change the difficulty of an engine side

```json
{"level": 16, "color": "black"}
```

Difficulty is set per side, not per game, so an engine-vs-engine
game can have two different strengths. Omit `color` to set both
sides at once. This is all that matters for a game with only one
`"engine"` side. This endpoint works with or without a game in
progress. The new level applies to that side's next move. Response:
`{"levels": {"white": 10, "black": 16}}`.

### `POST /api/game/engine` — change which engine plays an engine side

```json
{"engine": "stockfish", "color": "black"}
```

The choice of engine (`"gnuchess"` or `"stockfish"`) for an
`"engine"` side is set per side, not per game, so an engine-vs-engine
game can pit the two engines against each other. Omit `color` to set
both sides at once. This endpoint works with or without a game in
progress. The new engine applies to that side's next move. Response:
`{"engines": {"white": "gnuchess", "black": "stockfish"}}`.

### `POST /api/game/name` — set or clear a side's display name

```json
{"color": "white", "name": "Deep Purple"}
```

`color` is `"white"` or `"black"`. `name` is up to 40 characters,
trimmed rather than rejected if longer. An empty `name` clears it
back to showing that side's type alone. The board viewer shows this
name, and the server stamps it onto that side's `move_log` entries
from then on (see `name` under `GET /api/game` below). The name
applies only to the current game. A new game always starts with
neither side named (see `POST /api/game` above), regardless of what
was set here before. Use this endpoint to set a name after a game
has already started. For example, an API user can join a game they
did not start, and set a name here. The
`white_name`/`black_name` fields on `POST /api/game` apply only
when that game is created. This endpoint works with or without a
game
running. Response:
`{"player_names": {"white": "Deep Purple", "black": null}}`.

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
vertical bar in the board viewer. It is not part of the JSON API's
`GET /api/game` response (see below). It runs on its own dedicated
Stockfish process, always at full strength. This process is
entirely separate from any engine that plays a side of the game or
answers a `POST /api/game/phone-a-friend` query. As a result, it
never shares a Skill Level setting, and never slows down a move.
`quality` (see
`POST /api/game/eval-quality` below) trades update latency against
accuracy. Each entry's `description` is meant for direct display to
a person who is choosing between them, not only for this reference.

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

This endpoint returns the position, the side to move, the game
status, and more. Check the `turn` field to find the side to move.

Responses are trimmed for size by default. Every byte here has a
cost twice: once on the wire, and again in the context window of the
reader. Two fields are left out: the 8x8 `board` grid, which
re-encodes the position already given by `fen` and `board_ascii`,
and `move_log`, whose replacement is the O(1) `last_move`.
`phone_a_friend` is sent in a compact form (below). Add
`?verbose=1` to this or any other endpoint that returns a state, to
get the `board` grid and the expanded `phone_a_friend` breakdown
back.

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
  "last_move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"},
  "pending_suggestion": {"white": null, "black": null}
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
per engine. GNU Chess hints and Stockfish hints draw on independent
quotas, not a shared one. The field also carries `stockfish_eval`,
the separate budget for the `"eval"` kind of query (see
`POST /api/game/phone-a-friend` below). Each engine value is the
count *remaining* at each tier, joined by a slash in tier order
(`"level_10/level_20"`), where `-1` means unlimited. `stockfish_eval`
has a single tier, so it is one bare number. For example, `"2/1"` is
two level-10 hints and one level-20 hint still available. `"0/0"`
means that engine is exhausted. The bundled `chess.py` expands these
compact values into five labeled budgets for agents. See
`POST /api/game/phone-a-friend` below. Only an `"api-user"`/
`"api-trainee"`/`"centaur"` side can use this budget. The field is always
present, whatever the usage and whoever is playing, so anyone who
reads the state can see it. An `"api-trainee"` side in particular
must check it before every move.

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

This JSON API response has no `eval` field. The eval bar (see
`GET /api/eval-qualities` and `POST /api/game/eval-quality` above) is
a board-viewer-only feature for spectators.

`last_move` is the most recent entry from the game's internal move
log, or `null` before any move. Its `name` is that side's display
name at the time of the move, or `null` if none was set (see
`POST /api/game/name` above). Its `chat` field is present only if
that move carried a chat line (see `POST /api/game/move` below).
There is no standalone chat channel. Every chat line belongs to a
move. This endpoint, `POST /api/game/move`,
`POST /api/game/phone-a-friend`, and `GET /api/game/wait` all return
only this one entry, not the full move log. As a result, their
response size stays constant no matter how long the game runs, so
you can safely call them once per move in a loop.

`pending_suggestion` holds each side's not-yet-played `"centaur"`
suggestion. This field is `null` unless that side is `"centaur"` and
has an unplayed suggestion — see `POST /api/game/suggest` below. It
carries `uci`, `san`, `tactical_reasoning`, `strategic_reasoning`,
and optionally `chat`, the same shape a `POST /api/game/suggest`
response returns. Unlike `phone_a_friend`, `?verbose=1` never gates
this field. At most one entry is ever non-`null`, so there is
nothing to trim.

Five fields cannot change while a game runs: `started`, `players`,
`player_names`, `engine_levels`, and `engine_names`. This endpoint
and `POST /api/game` return these fields, because their job is to
establish where things stand. The per-move responses
(`POST /api/game/move`, `POST /api/game/phone-a-friend`,
`GET /api/game/wait`, `POST /api/game/resign`,
`POST /api/game/abort`) leave these fields out, because repeating
them every turn says nothing new. `?verbose=1` restores them.
The board viewer (port 5004) is the one place that exposes the full
move log, because it renders the whole game's history and chat.

If no game has started, this endpoint returns `404`.

CAUTION: Do not poll this endpoint in a tight loop to check the
turn. `GET /api/game/wait` (below) blocks until it is your turn,
which is simpler and faster than polling. If you do poll, space out
requests by at least two seconds. A person at the board viewer gets
updates a different way: over the SSE stream on port 5004 (below),
the instant the game changes.

### `GET /api/game/wait` — block until it is your turn

```
GET /api/game/wait?color=white&timeout=25
```

`color` (required) is the side to wait for. `timeout` (optional
seconds, default 25, capped at 55) bounds how long the request can
block. The endpoint returns immediately, without blocking, if it is
already `color`'s turn, if the game has ended, or if no game has
started.

The response tells you which of those happened, so you do not have
to work it out from the state yourself. When there is something to
act on — it is now `color`'s turn, or the game ended — you get the
state:

```json
{"changed": true, "state": {...}}
```

When the timeout expires with the position unchanged, you get a
minimal response instead. Sending a full state to report that
nothing happened is the most wasteful thing this API can do. A
caller who waits on a slow human opponent can collect several of
these responses in a row:

```json
{"changed": false, "turn": "black", "game_over": false}
```

Branch on `changed`, and call again when it is `false`.

### `POST /api/game/move` — submit a move

```json
{"move": "e2e4", "chat": "Good luck!", "tactical_reasoning": "no immediate tactics", "strategic_reasoning": "e4 grabs the center"}
```

This endpoint accepts UCI notation (`e2e4`, `e7e8q` for promotion) or
SAN (`e4`, `Nf3`, `O-O`). The move applies to the side with the
current turn. The caller does not name the color, because only one
side can move at a time. If it becomes an engine's turn next, the
server computes and applies its reply at once.

`chat` (optional, up to 240 characters, trimmed rather than rejected
if longer) attaches a short chat line to this move. There is no
separate delivery step, and no standalone chat channel. This is the
only way to send chat. The server stamps the chat onto this move's
`move_log` entry, along with the mover's current display name. The
opponent sees both the next time they read the game state, for
example in the response to their own next move. A person who
watches the board viewer sees it there too, next to the move.

`tactical_reasoning` and `strategic_reasoning` (up to 1000
characters each, also trimmed rather than rejected if longer,
optional for `"api-user"`/`"web-user"`, **required** for
`"api-trainee"` — see below) are private notes on why this move was
chosen. `tactical_reasoning` covers concrete, move-local calculation
(captures, checks, threats). `strategic_reasoning` covers the
longer-term plan behind the move. Unlike `chat`, neither field is
ever returned by this or any other endpoint while the game is in
progress. Both stay server-side only, for example for later review
by whoever operates the server. The one exception is
`GET /api/game/transcript` (below): once the game has ended, both
fields fold into that game's transcript, because there is no longer
an ongoing advantage to protect.

```json
{"move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"},
 "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine", "name": "Stockfish"},
 "state": {...}}
```

This endpoint returns `400` for any of these reasons:

- The move is illegal or unparseable.
- The move was submitted during the engine's turn.
- The move was submitted during a `"centaur"` side's turn (use
  `POST /api/game/suggest` instead — see below).
- No game is in progress.

**`"api-trainee"` forfeits.** The side to move can be
`"api-trainee"`. If it did not call
`POST /api/game/phone-a-friend` before this move, while it still had
budget left, or if it omitted
`tactical_reasoning`/`strategic_reasoning`, the server discards the
move. The server never parses it and never applies it to the board.
The game ends at once, and the other side wins. The response shape
differs in this case:

```json
{"forfeited": true, "by": "white", "reasons": ["no phone-a-friend call before this move, despite having queries left"], "state": {...}}
```

Check for the `"forfeited"` key rather than assuming the ordinary
`{"move", "engine_move", "state"}` shape whenever the mover is
`"api-trainee"`.

### `POST /api/game/suggest` — suggest a move (`"centaur"` only)

```json
{"move": "e2e4", "tactical_reasoning": "no immediate tactics", "strategic_reasoning": "e4 grabs the center", "chat": "Good luck!"}
```

This endpoint works only for a `"centaur"` side to move. It returns
`400` if it is not that side's turn, or if the side is not
`"centaur"`. Unlike `POST /api/game/move`, this endpoint never
touches the board. The server only checks the move for legality,
then stores it as this side's `pending_suggestion` (see
`GET /api/game` above), and replaces whatever was suggested before.
A person at the board viewer (port 5004) then either accepts the
suggestion as it stands, or plays a different legal move instead. By
design, `POST /api/game/move` always fails during a `"centaur"`
side's turn. There is no way to finalize a move for this side
through the REST API.

`tactical_reasoning` and `strategic_reasoning` (same limits as
`POST /api/game/move`) are **both required** here. If either is
missing, the server returns `400` with nothing stored, so you can
retry with both fields filled in. This is a plain rejection, not a
forfeit like `"api-trainee"`'s, because nothing has committed to the
board yet, so there is no wasted turn to punish. `chat` (optional)
travels with the stored suggestion for the person at the board to
read before deciding.

```json
{"suggestion": {"uci": "e2e4", "san": "e4", "by": "centaur", "name": "Deep Purple", "tactical_reasoning": "no immediate tactics", "strategic_reasoning": "e4 grabs the center", "chat": "Good luck!"},
 "state": {...}}
```

Calling this endpoint again before the suggestion is played replaces
it. There is no queue, and no error for suggesting more than once.

### `POST /api/game/phone-a-friend` — ask an engine for help

```json
{"level": 20, "engine": "stockfish"}
{"kind": "eval"}
```

This endpoint works only for the `"api-user"`/`"api-trainee"`/
`"centaur"` side to move. It asks for help with the current position without a move: the
board stays unchanged, and your turn does not end. This call is not
a substitute for `POST /api/game/move`. You still submit your own
move afterward, whether or not you act on the answer. For an
`"api-trainee"` side, a successful call here, of either kind, any
level, any engine, satisfies that side's phone-a-friend requirement
for the move it is about to submit (see the forfeit rule above). The
call is required before every move, for as long as any budget
remains.

`kind` (optional, `"move"` or `"eval"`, default `"move"`) picks what
you are asking for. The two kinds draw on separate budgets. They
answer different questions, so spending one does not cost you the
other.

#### `kind: "move"` — which move to play

`level` is `10` or `20`. These are the only two tiers offered. Each
tier has its own budget for the game, per engine. The budget is set
at `POST /api/game` time (`friend_level10_limit` and
`friend_level20_limit` for every engine at once, or the per-engine
`friend_limits` field, default `2` and `1` respectively) and tracked
separately per side, so each caller in a two-API-user game gets its
own budget. A budget can be set to `-1` for unlimited. Then this
call never fails for running out of queries at that tier for that
engine. `engine` (optional, `"gnuchess"` or `"stockfish"`) picks
which engine to ask, and defaults to `"gnuchess"` if omitted. GNU
Chess hints and Stockfish hints draw on independent quotas, not a
shared one, so a side can use both.

```json
{"advice": {"kind": "move", "level": 20, "engine": "stockfish", "uci": "g1f3", "san": "Nf3", "color": "white", "used": 1, "limit": 1, "remaining": 0},
 "state": {...}}
```

#### `kind: "eval"` — who is winning?

```json
{"kind": "eval"}
```

This query asks Stockfish how the current position stands, rather
than what to play. `level` and `engine` do not apply: there is only
one tier, and the answer always comes from Stockfish.

This is the strongest assessment the server can produce. It runs on
the eval bar's dedicated Stockfish process for a full 5 seconds, the
same search budget as a level-20 move hint. This process never
lowers its Skill Level from Stockfish's own default. Neither side's
configured difficulty affects it. The eval bar's quality setting
does not affect it either: `off` (see `POST /api/game/eval-quality`)
turns off the *spectators'* bar only, and has no bearing on whether
a player can spend one of their own queries.

```json
{"advice": {"kind": "eval", "engine": "stockfish", "color": "white",
            "score_cp": 34, "mate": null, "pov": "white",
            "eval": "+0.34", "favors": "white",
            "used": 1, "limit": 1, "remaining": 0},
 "state": {...}}
```

`score_cp` is centipawns, and `mate` is the number of moves to a
forced mate (`null` when there is none). **Both values are from
white's point of view, whichever side asked**, matching the eval bar
and the transcript. So `-2.50` means black is ahead by about two and
a half pawns, even when black is the one who asked. `eval` is the
same reading preformatted (`"+0.34"`, `"#3"`, `"#-2"`). `favors` is
`"white"`, `"black"`, or `"equal"` outright, so the sign cannot be
misread. Exactly one of `score_cp` and `mate` is non-`null`.

Its budget is `friend_eval_limit` at `POST /api/game` time (default
`1`, or `-1` for unlimited), tracked per side. This budget is
reported under `stockfish_eval` in `state.phone_a_friend`, apart
from the per-engine tiers, because it is not one of them.

#### Errors and budget visibility

Returns `400` for any of these reasons:

- It is not your turn.
- Your side is not `"api-user"`/`"api-trainee"`/`"centaur"`.
- `kind` is not `"move"` or `"eval"`.
- `level` is not `10` or `20` on a `"move"` query.
- `engine` is not a valid engine name.
- You have no queries left in the budget drawn on.
- The engine cannot analyze the position.

A query that fails for any of these reasons costs you nothing.

The current budget and usage for both sides, across both engines and
the `stockfish_eval` budget, is always visible in
`state.phone_a_friend` (see `GET /api/game` above). This is true
whether or not you have called this endpoint yet.

### `POST /api/game/resign` — resign

```json
{"player": "white"}
```

This endpoint ends the game. The API records the other side as the
winner.

### `POST /api/game/abort` — end the game with no winner

No request body. This endpoint ends the current game at once (status
`aborted`, `winner: null`) regardless of player types. Unlike
`POST /api/game/resign`, it needs no `player` side, so it also works
for an engine-vs-engine game with no `"web-user"`/`"api-user"` side
at all. An engine-vs-engine game's background autoplay stops within
one already-in-flight move of this call. Returns `400` if no game
has started, or if the current game has already ended.

### `GET /api/game/transcript` — download a PGN transcript

This endpoint works only once the game has ended (any status but
`not_started`/`in_progress`). It returns a
[PGN (Portable Game Notation)](https://en.wikipedia.org/wiki/Portable_Game_Notation)
transcript of the game, the standard plain-text chess format read by
lichess.org, chess.com, and most chess software. The response is the
raw PGN text (`Content-Type: application/x-chess-pgn`), not JSON,
with a `Content-Disposition` header, so a browser downloads it as a
`.pgn` file rather than displaying it.

Metadata (players, result, engine names and levels where relevant,
and how the game ended) is in the PGN tag pairs at the top. Chat and
reasoning are otherwise never returned by any endpoint, and the eval
bar's live read is never returned by the JSON API at all (see
`GET /api/game` above). But once the game is over, there is no
ongoing advantage left to protect. Three things fold into a PGN
comment on each move:

- The move's `chat` (see `POST /api/game/move` above).
- Any private `tactical_reasoning`/`strategic_reasoning` recorded
  for it.
- The eval bar's own read of the resulting position, if the eval
  bar was on.

The transcript includes whatever read was captured for each move at
the time. A move with
the eval bar off, or a read still pending when a later move
superseded it, has no `Eval:` comment. Scores are pawns from white's
point of view (`+0.34` means white is better by about a third of a
pawn, `-1.20` means black is better by a bit over a pawn), or
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

One further tag appears only when there is something to report:

```
[EvalBarReads "3 (after ply 1, 2, 2)"]
```

It counts reads of the board viewer's eval bar, during this game, by
a client that did not look like a browser. See "Board viewer (port
5004)" below for what this means, and what it does not prove. The
tag is absent from a game with no such reads.

`include` (optional, `all` or `moves`, default `all`) controls those
per-move comments. The default is the complete annotated transcript
shown above. The server withholds the reasoning for the entire game,
then folds it in here. Dropping it silently defeats the point of
collecting it. `include=moves` returns bare movetext with the
same tag pairs, for a caller that wants only the moves. This form
avoids putting a long, heavily-annotated game's comments (up to 240
+ 1000 + 1000 characters per ply) into that caller's context:

```
1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0
```

The board viewer's own "Download transcript" button (port 5004)
always serves the complete annotated transcript, and accepts no
`include` parameter. That file goes to disk for a person to keep, so
there is nothing to gain by trimming it.

Returns `400` if no game has started, or if the current game is
still in progress.

## Board viewer (port 5004)

`GET /` returns an HTML page. The page shows the current board and
updates live. The browser receives updates through Server-Sent
Events (`GET /events`) the instant the game changes, instead of on a
fixed timer. The page keeps one open connection, and repaints only
the squares that changed, so there is no flash or reload.

`GET /state` is also available for a single fetch. The page uses
`GET /state` as a fallback when SSE is not available.

**These two routes carry the eval bar reading.** Both call the game
state with its `eval` field included, which no endpoint on port 5003
ever returns:

```json
"eval": {"score_cp": 31, "mate": null, "pov": "white", "quality": "deep", "pending": false, "error": null}
```

That matters because an API player must pay for an evaluation with
`POST /api/game/phone-a-friend` at `{"kind": "eval"}`, which is
budgeted per side per game. Reading the viewer gives the same class
of information for free.

Nothing prevents that read. The viewer has no authentication, so
anything a browser can fetch, a script can fetch too. Serving the
eval bar to the page while it withholds the bar from `curl` is not
possible, without authentication that this server deliberately does
not have.

So the server records the read instead. This applies when either
route is fetched during a game in progress, by a client whose
`User-Agent` does not start with `Mozilla/`. The server logs a
warning that names the path, the address, and the User-Agent. The
server also adds an entry to that game's record. The finished game's
transcript then carries a supplementary tag:

```
[EvalBarReads "3 (after ply 1, 2, 2)"]
```

The tag is absent when there is nothing to report.

**This is an audit trail, not a control.** Any client can send any
`User-Agent` it likes. The check catches only a caller that did not
think to disguise itself. A clean record is not proof that nobody
looked. The response is never gated on the outcome, so a wrong guess
about the client costs nobody their eval bar.

**Downloading a transcript.** Once the game ends, and before a new
one starts (if any does), a "Download transcript (PGN)" button
appears above the start-game form. It downloads the just-finished
game from `GET /game/transcript` (the same underlying call as
`GET /api/game/transcript` above), and disappears again the moment a
new game actually starts.

**Starting a game.** When no game is in progress, including right
after a game finishes, the page shows a form to start one. A person
picks a type for White and a type for Black:

- `api-user` — moves come from the REST API (an agent, or curl).
- `api-trainee` — an API user subject to the phone-a-friend and
  reasoning requirements described under `POST /api/game/move`.
- `engine` — GNU Chess or Stockfish plays this side (see below).
- `web-user` — the person at this page plays this side, by clicking
  the board (see below).
- `centaur` — the REST API can only suggest a move (see
  `POST /api/game/suggest`). The person at this page decides whether
  to play it or something else (see below).

Each side that is `engine` gets its own engine dropdown (GNU Chess
or Stockfish) and its own difficulty dropdown. As a result, an
engine-vs-engine game can pit two different engines, two different
strengths, or both, against each other. Whenever either side is
`api-user`, `api-trainee`, or `centaur`, the form also shows
phone-a-friend inputs: independent L10 and L20 limits for GNU Chess
and Stockfish, plus the separate
Stockfish Eval limit. See `POST /api/game/phone-a-friend` above. The
form supports every combination of the five player types, including
API users or trainees against an engine, a web user, or a centaur,
and two engines. An engine-vs-engine game plays itself out, one
paced move at a time, with no further input needed.

**Playing as a web user.** When it is a `web-user` side's turn, the
page lets that person click a piece. Then the person clicks a
highlighted square to move there. If the move is a promotion, the page
shows a small picker for the piece to promote to.

**Playing centaur.** When it is a `centaur` side's turn, the page
behaves like `web-user`: click a piece, then a highlighted square,
to play any legal move. One addition applies. Once the API has
called `POST /api/game/suggest`, its suggestion appears as a dashed
arrow, in a color distinct from the last-move arrow, with the
suggested squares outlined. An "Accept suggestion: `<move>`" button
also appears above the board. Clicking the button plays that exact
move. Playing anything else instead, by clicking the board as usual,
overrides the suggestion. There is no separate "reject" step. Until
a suggestion arrives, the status line reads "waiting for a
suggestion from the API".

**Names and chat.** A players bar above the board shows each side's
display name and type. For an `"engine"` side, the bar also shows
which engine it is and its difficulty level. For an `"api-user"`,
`"api-trainee"`, or `"centaur"` side, the bar shows its five
remaining phone-a-friend budgets: L10 GNU Chess, L20 GNU Chess, L10 Stockfish,
L20 Stockfish, and Stockfish Eval. Set a name with
`POST /api/game/name`. The side to move is highlighted. Any move
that carried `chat` (see `POST /api/game/move`) shows up in a chat
panel below the board, next to that move. There is no standalone
chat channel. While it is a `web-user` side's own turn, an input box
under the panel lets that person type a message. The server attaches
this message automatically to whichever move they submit next.

**Resign or restart.** While a game is in progress, a button appears
above the board. A person can be behind either side. That is, at
least one side can be `web-user`. If so, the button reads "Resign"
and ends the game as that side, the same as
`POST /api/game/resign`. If no side is `web-user` — for example an
api-user/engine or engine-vs-engine game — the button reads
"Restart" instead. It ends the game at once with no winner, the
same as `POST /api/game/abort`. An
engine-vs-engine match actually stops on the spot, rather than
continuing to play in the background while a person chooses a new
game's settings. Either way, the start form reappears automatically,
the same as after any other game-ending result.

**Last-move arrow.** After a move, a semi-transparent arrow points
from its start square to its end square, on top of the piece that
moved. This happens for a move by any side, whether that side is a
web user, an API user, or an engine. It updates on every new move,
fades on its own after 60 seconds of no further move, and clears
when a new game starts.

The page's own `/game/start`, `/game/move`, `/game/legal-moves`,
`/game/resign`, `/game/abort`, `/game/chat`, and
`/game/eval-quality` routes back these features. They call the same
`ChessGame` object as the REST API (port 5003), so they enforce the
same rules. They exist only so the page's own JS can act on the game
from its own origin. `api.py` (port 5003) stays the reference for
programmatic play.

**Eval bar.** A vertical bar next to the board shows the eval bar's
live Stockfish assessment. White's share of the bar grows as White's
position improves, with a numeric read (`+1.3`, or `M4` for mate in
4) underneath. It runs on its own dedicated Stockfish process. This
process is entirely separate from any engine that plays a side of
the game or answers a phone-a-friend query (see
`POST /api/game/eval-quality` above). As a result, it never affects,
and is never affected by, actual gameplay. A control next to the
board/piece
style pickers lets a
person choose the eval bar's speed/accuracy trade-off (see
`GET /api/eval-qualities` above): Off, Fast, Balanced (the default),
or Deep. Each choice carries a plain-language description of the
trade-off, so the choice does not need this reference. The setting
is server-side
and sticky, like engine level/choice, so it is shared by everyone
watching, not a per-browser preference.

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
- GNU Chess and Stockfish are the two `"engine"` choices (see
  `engine` under `POST /api/game` above), equally supported and
  chosen per side. The server talks to whichever engine is assigned
  to a side over the UCI protocol (`gnuchess --uci` / `stockfish`),
  through `python-chess`'s engine interface. Both engines share one
  0-20 difficulty scale. For Stockfish, the scale applies directly
  to its own native "Skill Level" option. GNU Chess has no such
  option of its own, so the server approximates it through a derived
  search-depth cap (see `GET /api/engine-levels` above).
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
