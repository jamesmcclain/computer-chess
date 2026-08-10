# Setting up and joining games

Read this when you start a game that is not the plain
"you as White against the engine" case in SKILL.md section 1, or when
you join a game that is already running.

## Choosing the two sides

Every side is one of four types:

| Type | Who plays it |
|---|---|
| `api-user` | An outside caller like you, through the REST API. Your default. |
| `api-trainee` | The same, plus a strict process requirement. See `trainee.md`. Use it only if the user asks for it by name. |
| `web-user` | A person clicking the board in the viewer on port 5004. |
| `engine` | GNU Chess or Stockfish. |

Every pairing is valid. Set your own side and your opponent's side:

```bash
# You as White against the engine
python3 scripts/chess.py new --white api-user --black engine

# You as Black against the engine
python3 scripts/chess.py new --white engine --black api-user

# You against another API caller
python3 scripts/chess.py new --white api-user --black api-user

# You against a person at the board viewer
python3 scripts/chess.py new --white api-user --black web-user
```

Do not assume your opponent is an engine. Read the output of `new`,
which names the type of each side.

## Difficulty

`--level` takes 0 to 20, weakest to strongest. It is Stockfish's own
Skill Level scale. The default is 10. The level changes nothing for a
side that is not an engine.

```bash
python3 scripts/chess.py new --white api-user --black engine --level 3
```

Use `--white-level` and `--black-level` to set one side alone. They win
over `--level` for that side.

If the user asks for an easier or harder opponent, or names a
difficulty such as "beginner" or "hard", set the level to match. Do not
try to play badly on purpose.

## Which engine

`--engine` takes `gnuchess` or `stockfish`, and sets both engine sides
at once. The default is `gnuchess`. Use `--white-engine` and
`--black-engine` to set one side alone.

If the user names an engine, set it. Do not assume either one.

## Display names

`--white-name` and `--black-name` set a name for the board viewer and
the move log. Names are cosmetic and optional. Skip them unless the
user asks.

A name never carries over to the next game. Set it again each time.

## Hint budgets

These set the phone-a-friend budget for the whole game, per side. See
`phone-a-friend.md` for what each one buys.

| Flag | Default | Buys |
|---|---|---|
| `--friend-l10` | 2 | Level-10 move hints, for every engine |
| `--friend-l20` | 1 | Level-20 move hints, for every engine |
| `--friend-eval` | 1 | Full-strength Stockfish position evaluations |

Any of them accepts `-1` for unlimited, or `0` to turn that kind off.
Raise them if the user wants more help. None of them carries over to
the next game.

## Two engines playing each other

Set both sides to `engine` when the user wants to watch instead of
play:

```bash
python3 scripts/chess.py new --white engine --black engine \
  --white-engine stockfish --black-engine gnuchess \
  --white-level 12 --black-level 8
```

Neither side sends moves. The game plays itself out in the background,
one paced move at a time, and streams to the board viewer.

The move loop in SKILL.md section 2 does not apply to this kind of
game. Do not start it if the user wants to play. To follow the game,
run `chess.py turn` when the user asks, or point them at the viewer.

## Joining a game already in progress

There is no login and no seat reservation. To join, find the open side
and start moving for it.

1. Read the current game:

   ```bash
   python3 scripts/chess.py turn
   ```

   An error saying no game is in progress means you should start one
   instead.

2. Check the type of the color the user wants. `chess.py new` prints
   the types when a game starts; for a running game, read `players` from
   `GET /api/game` (see `rest-api.md`).
   - `api-user`: you can play it, if nobody else is acting for it.
   - `engine` or `web-user`: you cannot take it over. Tell the user, and
     offer a new game with the colors set correctly.
   - Both sides `engine`: this is a watch-only game. No side is
     joinable. Offer a new game.

3. If the game has already ended, do not move. Report the result and
   offer a new game.

4. Read whose turn it is. If it is your color, play (SKILL.md section
   2). If not, wait (SKILL.md section 3).

When both sides are `api-user`, nothing in the game records which
caller owns which color. Ask the user which color you play. Then pass
that color as `--side` on every command, and never move for the other
one.
