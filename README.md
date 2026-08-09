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

`white` and `black` are each one of three types: `"api-user"` (an API
user that submits moves through this API), `"engine"` (GNU Chess or
Stockfish — see `engine` below), or `"web-user"` (a person playing
through the board viewer on port 5004, by clicking the board). Every
combination is supported, including two engines. When both sides are
`"engine"`, the two engines play each other. This game needs no
further calls. It plays itself out in the background, one paced move
at a time, so it streams to the board viewer like any other game.

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
level-10 and level-20 engine hints an `"api-user"` side may request
over the course of the game, for *every* engine at once. Each engine's
quota is tracked separately, not pooled — `friend_limits` (optional,
an object of the form `{engine_name: {tier: limit}}`, e.g.
`{"stockfish": {"10": 5}, "gnuchess": {"20": 0}}`) sets one or more
engines' budgets at one or both tiers specifically, and wins over the
generic fields for whichever engine/tier it names — so an
`"api-user"` side can be given, say, 5 GNU Chess hints and 1 Stockfish
hint, independent of each other, and this scales to however many
engines the server supports. Any limit, generic or per-engine, can be
`-1` instead of a number, which makes that tier (for that engine, or
for every engine via the generic fields) unlimited for the game — the
query never fails for running out. Like the name fields above, none
of these are sticky — every new game gets the defaults shown above
unless overridden here,
and usage always resets to zero. See `POST /api/game/phone-a-friend`
below.

If `white` is `"engine"` and `black` is not, that engine plays its
opening move immediately. The response returns this move as
`engine_move`.

Response: `201` with `{"state": {...}, "engine_move": {...} | null}`.

### `GET /api/game/legal-moves` — legal moves for the side to move

The optional query parameter `from=e2` limits the result to moves that
start on that square.

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

### `GET /api/game` — current state

This endpoint returns the board, the side to move, the game status, the
move history, and more. Check the `turn` field to find the side to
move.

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
  "board": [[{"color": "white", "type": "P", "code": "wP"}, null, ...], ...],
  "players": {"white": "api-user", "black": "engine"},
  "player_names": {"white": "Deep Purple", "black": null},
  "engine_levels": {"white": 10, "black": 10},
  "engine_names": {"white": "gnuchess", "black": "stockfish"},
  "phone_a_friend": {
    "limits": {"gnuchess": {"level_10": 2, "level_20": 1}, "stockfish": {"level_10": 2, "level_20": 1}},
    "white": {
      "gnuchess": {"used": {"level_10": 0, "level_20": 0}, "remaining": {"level_10": 2, "level_20": 1}},
      "stockfish": {"used": {"level_10": 0, "level_20": 0}, "remaining": {"level_10": 2, "level_20": 1}}
    },
    "black": {
      "gnuchess": {"used": {"level_10": 0, "level_20": 0}, "remaining": {"level_10": 2, "level_20": 1}},
      "stockfish": {"used": {"level_10": 0, "level_20": 0}, "remaining": {"level_10": 2, "level_20": 1}}
    }
  },
  "fullmove_number": 1,
  "halfmove_clock": 0,
  "move_log": [{"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"}]
}
```

`status` is one of: `not_started`, `in_progress`, `checkmate`,
`stalemate`, `draw_insufficient_material`, `draw_75_moves`,
`draw_5fold_repetition`, `draw_claimable_50_moves`,
`draw_claimable_threefold_repetition`, `resigned`.

`engine_names` shows which engine (`"gnuchess"` or `"stockfish"`)
plays each `"engine"` side — see `POST /api/game/engine` above. Its
entry for a non-`"engine"` side has no meaning.

`phone_a_friend` shows this game's hint budget (`limits`, set at
`POST /api/game` time) and each side's usage/remaining count at each
tier, broken out per engine — GNU Chess hints and Stockfish hints draw
on independent quotas, not a shared one. See
`POST /api/game/phone-a-friend` below. Only an `"api-user"` side can
use it, but the field is always present so anyone reading the state
can see the budget.

Each `move_log` entry's `name` is that side's display name at the time
of the move, or `null` if none was set (see `POST /api/game/name`
above). Its `chat` is present only if that move carried a chat line
(see `POST /api/game/move` below). There is no standalone chat
channel — every chat line belongs to a move.

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
block. This call returns `{"state": {...}}` as soon as one of three
things happens: it becomes `color`'s turn, the game ends, or the
timeout passes. It returns immediately, without blocking, if any of
those is already true when it is called — including if no game has
started. A timed-out response looks the same as any other: check
`state.turn` and `state.game_over` in it to tell the difference.

### `POST /api/game/move` — submit a move

```json
{"move": "e2e4", "chat": "Good luck!", "reasoning": "e4 grabs the center"}
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

`reasoning` (optional, up to 1000 characters, also trimmed rather
than rejected if longer) is a private note on why this move was
chosen. Unlike `chat`, it is never returned by this or any other
endpoint while the game is in progress. It is kept server-side only,
for example for later review by whoever is operating the server. The
one exception is `GET /api/game/transcript` (below): once the game
has ended, reasoning is folded into that game's transcript, since
there is no longer any ongoing advantage to protect.

```json
{"move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user", "name": "Deep Purple", "chat": "Good luck!"},
 "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine", "name": "Stockfish"},
 "state": {...}}
```

This endpoint returns `400` for an illegal or unparseable move, for a
move submitted during the engine's turn, or when no game is in
progress.

### `POST /api/game/phone-a-friend` — ask an engine for a move recommendation

```json
{"level": 20, "engine": "stockfish"}
```

For the `"api-user"` side to move only. Asks an engine what it would
play in the current position, without submitting that move: the board
is unchanged, your turn does not end, and this is not a substitute for
`POST /api/game/move` — you still submit your own move afterward,
whether or not you take the suggestion.

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
independent quotas, not a shared one — an `"api-user"` side can use
both.

```json
{"advice": {"level": 20, "engine": "stockfish", "uci": "g1f3", "san": "Nf3", "color": "white", "used": 1, "limit": 1, "remaining": 0},
 "state": {...}}
```

Returns `400` if it is not your turn, your side is not `"api-user"`,
`level` is not `10` or `20`, `engine` is not a valid engine name, or
you have no queries left at that level for that engine. Current
budget and usage for both sides, at both engines, is always visible in
`state.phone_a_friend` (see `GET /api/game` above), whether or not
you've called this endpoint yet.

### `POST /api/game/resign` — resign

```json
{"player": "white"}
```

This endpoint ends the game. The API records the other side as the
winner.

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
`chat` (see `POST /api/game/move` above) and any private `reasoning`
recorded for it are folded in as a PGN comment on that move —
`reasoning` is otherwise never returned by any endpoint, but once the
game is over there is no ongoing advantage left to protect. For
example:

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

1. e4 {Chat: Good luck! / Reasoning: e4 grabs the center} e5 2. Qh5 Nc6
3. Bc4 Nf6 4. Qxf7# {Chat: gg} 1-0
```

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
side, the same as `POST /api/game/resign`. The start form then
reappears automatically, same as after any other game-ending result.
If no side is `web-user` (an api-user/engine or engine-vs-engine
game, for example), it reads "Restart" instead. It simply opens the
start form early, without ending the running game on its own. The
running game only actually stops once a new one is started from that
form.

**Last-move arrow.** After a move, a semi-transparent arrow points
from its start square to its end square, on top of the piece that
moved. This happens for a move by any side, whether that side is a
web user, an API user, or an engine. It updates on every new move,
fades on its own after 60 seconds of no further move, and clears
when a new game starts.

The page's own `/game/start`, `/game/move`, `/game/legal-moves`,
`/game/resign`, and `/game/chat` routes back these features. They
call the same `ChessGame` object as the REST API (port 5003), so they
enforce the same rules. They exist only so the page's own JS can act
on the game from its own origin. `api.py` (port 5003) stays the
reference for programmatic play.

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
