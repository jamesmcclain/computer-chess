# Calling the REST API directly

Use `scripts/chess.py` when you can. It makes these same calls and
prints far less. Read this file only when you cannot run the script.

The repository's `README.md` is the complete endpoint reference,
including every request field and response shape. This file covers only
what the move loop needs.

The base URL is `http://10.0.2.2:5003` unless you were told otherwise.

## The calls the move loop needs

```bash
# whose turn, the position, the last move, the hint budget
curl http://10.0.2.2:5003/api/game

# legal moves: one space-separated string of UCI moves
curl http://10.0.2.2:5003/api/game/legal-moves

# derived tactics: loose material, pins, captures, checks
curl http://10.0.2.2:5003/api/game/analysis

# submit a move
curl -X POST http://10.0.2.2:5003/api/game/move \
  -H 'Content-Type: application/json' \
  -d '{"move": "e2e4", "chat": "Good luck!",
       "tactical_reasoning": "No captures or checks to calculate.",
       "strategic_reasoning": "e4 takes the center."}'

# block until your turn
curl 'http://10.0.2.2:5003/api/game/wait?color=white&timeout=25'

# ask for help
curl -X POST http://10.0.2.2:5003/api/game/phone-a-friend \
  -H 'Content-Type: application/json' -d '{"kind": "eval"}'
```

`GET /api` returns the full endpoint list at any time.

`GET /api/game/analysis` reports what the position holds: the material
that can be taken on both sides, the absolute pins, and your legal
captures and checks. Every fact in it follows from the FEN. Deriving
those facts by eye is where blunders come from, so read this endpoint
rather than repeat its work.

CAUTION: the `hanging` list counts direct attackers and defenders only.
It is not a static exchange evaluation. It does not see x-rays,
batteries, or pinned defenders. Treat each entry as a square worth a
second look.

## Things to know about the responses

- The position comes as `fen` and `board_ascii`. There is no 8x8 array.
- Per-move responses leave out the fields that cannot change during a
  game: `started`, `players`, `player_names`, `engine_levels`,
  `engine_names`. Read those from `GET /api/game`.
- Add `?verbose=1` to any of them for the full payload. You do not
  normally need it.
- `GET /api/game/wait` returns `{"changed": false, ...}` with no state
  when the timeout expires and nothing happened. Call it again.
- `POST /api/game/move` returns `{"forfeited": true, ...}` instead of
  the usual shape when an `api-trainee` side breaks a requirement. See
  `trainee.md`.
- A `400` carries an `error` field. Read it before you retry.

## The check the script does for you

**The API has no authentication.** Take a game where both sides are
`api-user`. The server accepts a move sent during the wrong side's turn,
and it applies that move. Nothing rejects it.

`chess.py` takes a required `--side` and refuses to act when it is not
that side's turn. If you call the API directly, you lose that
protection. Read `turn` from `GET /api/game` and confirm it matches your
color before every single move.
