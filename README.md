# computer-chess

computer-chess is a dockerized chess server. GNU Chess runs inside an
Ubuntu container. A JSON REST API controls the game. A side is an API
user (an outside caller that submits moves through the API), a web
user (a person playing through the board viewer), or GNU Chess itself.
A separate web page shows the current board, and also lets a person
start a game or play a web-user side.

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
{"white": "api-user", "black": "engine", "level": 5}
```

`white` and `black` are each one of three types: `"api-user"` (an API
user that submits moves through this API), `"engine"` (GNU Chess), or
`"web-user"` (a person playing through the board viewer on port 5004,
by clicking the board). Both sides cannot be `"engine"`. Every other
combination is supported, including two API users or two web users.

`level` (optional, `1`-`10`, weakest to strongest) sets the difficulty of
GNU Chess for this game. Omit `level` to keep the last value (default
`5`). See `GET /api/engine-levels` and `POST /api/game/level` below.

If `white` is `"engine"`, GNU Chess plays its opening move immediately.
The response returns this move as `engine_move`.

Response: `201` with `{"state": {...}, "engine_move": {...} | null}`.

### `GET /api/game/legal-moves` — legal moves for the side to move

The optional query parameter `from=e2` limits the result to moves that
start on that square.

```json
{"moves": [{"uci": "e2e4", "san": "e4", "from": "e2", "to": "e4", "promotion": null}, ...], "count": 20}
```

### `GET /api/engine-levels` — list of difficulty levels

```json
{"levels": [{"level": 1, "depth": 1, "max_time_seconds": 0.2}, ..., {"level": 10, "depth": 15, "max_time_seconds": 5.0}], "default": 5}
```

GNU Chess's UCI mode has no built-in skill-level or Elo option. The API
approximates difficulty the standard way for UCI engines: it limits the
search depth (`depth`), with a time limit (`max_time_seconds`) as a
safety net. Level 1 plays weak, often-obvious moves in almost no time.
Level 10 searches much deeper and can take up to the time limit.

### `POST /api/game/level` — change the difficulty of GNU Chess

```json
{"level": 8}
```

This endpoint works with or without a game in progress. The new level
applies to GNU Chess's next move. Response: `{"level": 8}`.

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
  "engine_level": 5,
  "fullmove_number": 1,
  "halfmove_clock": 0,
  "move_log": [{"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user"}]
}
```

`status` is one of: `not_started`, `in_progress`, `checkmate`,
`stalemate`, `draw_insufficient_material`, `draw_75_moves`,
`draw_5fold_repetition`, `draw_claimable_50_moves`,
`draw_claimable_threefold_repetition`, `resigned`.

If no game has started, this endpoint returns `404`.

CAUTION: Do not poll this endpoint in a tight loop to check the turn.
If you wait for an opponent, space out requests by at least two
seconds. For a faster update, use the SSE stream on port 5004 (below).
The SSE stream pushes a new state the instant the game changes.

### `POST /api/game/move` — submit a move

```json
{"move": "e2e4"}
```

This endpoint accepts UCI notation (`e2e4`, `e7e8q` for promotion) or
SAN (`e4`, `Nf3`, `O-O`). The move applies to the side with the current
turn. The caller does not name the color, because only one side can
move at a time. If it becomes GNU Chess's turn next, the server
computes and applies its reply immediately.

```json
{"move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "api-user"},
 "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine"},
 "state": {...}}
```

This endpoint returns `400` for an illegal or unparseable move, for a
move submitted during the engine's turn, or when no game is in
progress.

### `POST /api/game/resign` — resign

```json
{"player": "white"}
```

This endpoint ends the game. The API records the other side as the
winner.

## Board viewer (port 5004)

`GET /` returns an HTML page. The page shows the current board and
updates live. The browser receives updates through Server-Sent Events
(`GET /events`) the instant the game changes, instead of on a fixed
timer. The page keeps one open connection and repaints only the
squares that changed, so there is no flash or reload.

`GET /state` is also available for a single fetch. The page uses
`GET /state` as a fallback when SSE is not available.

**Starting a game.** When no game is in progress, including right
after a game finishes, the page shows a form to start one. A person
picks a type for White and a type for Black:

- `api-user` — moves come from the REST API (an agent, or curl).
- `engine` — GNU Chess plays this side.
- `web-user` — the person at this page plays this side, by clicking
  the board (see below).

If either side is `engine`, the form also shows a difficulty level.
This form supports every combination the API supports:

- Two API users.
- An API user against the engine.
- A web user against the engine.
- A web user against an API user.

**Playing as a web user.** When it is a `web-user` side's turn, the
page lets that person click a piece. Then the person clicks a
highlighted square to move there. If the move is a promotion, the page
shows a small picker for the piece to promote to.

The page's own `/game/start`, `/game/move`, and `/game/legal-moves`
routes back these two features. They call the same `ChessGame` object
as the REST API (port 5003), so they enforce the same rules. They exist
only so the page's own JS can act on the game from its own origin.
`api.py` (port 5003) stays the reference for programmatic play.

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
- GNU Chess acts only as the `"engine"` side. The server talks to it
  over the UCI protocol (`gnuchess --uci`) through `python-chess`'s
  engine interface.
- `server.py` runs the REST API and the viewer as two Flask apps in one
  process. Both apps share one in-memory `ChessGame` object (`game.py`),
  guarded by a lock. The server needs no database, because it supports
  only one game at a time.

## Files

```
Dockerfile      Ubuntu, gnuchess, and the Python/Flask/python-chess image
run.sh          Convenience script to build and run the image
server.py       Entry point: starts the API (port 5003) and the viewer (port 5004)
game.py         Game state, rules, and the GNU Chess (UCI) integration
api.py          REST API routes
viewer.py       Read-only board viewer routes, page, and appearance catalogue
static/chess/   Board-square and piece-set art for the viewer (see its README.md)
```
