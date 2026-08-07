# gnuchess-api

A dockerized chess server: GNU Chess running inside an Ubuntu container,
controlled entirely through a JSON REST API. Supports either two "outside"
players (each submitting moves over the API) or one outside player against
GNU Chess itself. A separate, read-only web page shows the current board.

Only one game is tracked at a time — starting a new game replaces whatever
game was previously in progress. There is no authentication on any
endpoint.

## Quick start

```bash
docker build -t gnuchess-api .
docker run -it --rm -p 5003:5003 -p 5004:5004 gnuchess-api
```

or just `./run.sh`.

- REST API: `http://localhost:5003/api` (see below)
- Board viewer (read-only): `http://localhost:5004/`

## REST API (port 5003)

All request/response bodies are JSON. `GET /api` returns this same
reference as machine-readable JSON.

### `POST /api/game` — start a new game

```json
{"white": "human", "black": "engine"}
```

`white` and `black` are each `"human"` (an outside player who will submit
moves via this API) or `"engine"` (GNU Chess). At least one side must be
`"human"` — two outside players, or one outside player vs. GNU Chess, are
both supported; GNU Chess playing itself is not.

If `white` is `"engine"`, GNU Chess's opening move is played immediately
and returned in the response as `engine_move`.

Response: `201` with `{"state": {...}, "engine_move": {...} | null}`.

### `GET /api/game` — current state

Returns the board, whose turn it is, game status, move history, etc.:

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
  "players": {"white": "human", "black": "engine"},
  "fullmove_number": 1,
  "halfmove_clock": 0,
  "move_log": [{"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "human"}]
}
```

`status` is one of: `not_started`, `in_progress`, `checkmate`, `stalemate`,
`draw_insufficient_material`, `draw_75_moves`, `draw_5fold_repetition`,
`draw_claimable_50_moves`, `draw_claimable_threefold_repetition`,
`resigned`.

`404` if no game has been started yet.

### `GET /api/game/legal-moves` — legal moves for the side to move

Optional query param `from=e2` restricts to moves starting on that square.

```json
{"moves": [{"uci": "e2e4", "san": "e4", "from": "e2", "to": "e4", "promotion": null}, ...], "count": 20}
```

### `POST /api/game/move` — submit a move

```json
{"move": "e2e4"}
```

Accepts UCI notation (`e2e4`, `e7e8q` for promotion) or SAN (`e4`, `Nf3`,
`O-O`). Applies to whichever color currently has the move — the caller
doesn't need to specify color, only one side can legally move at a time.
If it becomes GNU Chess's turn afterward, its reply is computed and
applied immediately.

```json
{"move": {"ply": 1, "color": "white", "uci": "e2e4", "san": "e4", "by": "human"},
 "engine_move": {"ply": 2, "color": "black", "uci": "d7d5", "san": "d5", "by": "engine"},
 "state": {...}}
```

`400` for an illegal/unparseable move, a move submitted when it's the
engine's turn, or no game in progress.

### `POST /api/game/resign` — resign

```json
{"player": "white"}
```

Ends the game; the other side is recorded as the winner.

## Board viewer (port 5004)

`GET /` — a view-only HTML page showing the current board, updated live.
No inputs; it cannot be used to submit moves. Updates are pushed to the
browser over Server-Sent Events (`GET /events`) the instant the game
changes, rather than polled on a timer — the page holds one open
connection and only repaints the squares that actually changed, so
there's no periodic flash/reload. (`GET /state` is also available for a
one-off fetch, and is used as an automatic fallback if SSE isn't
available.) Pieces render from `/static/pieces/<code>.png` (e.g.
`wN.png`, `bK.png`) if present, falling back to Unicode chess glyphs
otherwise — see `static/pieces/README.md` to drop in your own piece art
later, no code changes required.

## How it works

- [`python-chess`](https://python-chess.readthedocs.io/) is the source of
  truth for the board, move legality, and SAN/UCI parsing.
- GNU Chess only acts as the `"engine"` player, spoken to over the UCI
  protocol (`gnuchess --uci`) via `python-chess`'s engine interface.
- `server.py` runs the REST API and the viewer as two Flask apps in one
  process, sharing a single in-memory `ChessGame` object (`game.py`)
  guarded by a lock — no database needed for a single concurrent game.

## Files

```
Dockerfile      Ubuntu + gnuchess + Python/Flask/python-chess image
run.sh          Host-side build-and-run convenience script
server.py       Entry point: starts the API (5003) and viewer (5004)
game.py         Game state, rules, and GNU Chess (UCI) integration
api.py          REST API routes
viewer.py       Read-only board viewer routes + page
static/pieces/  Drop piece images here later (see its README.md)
```
