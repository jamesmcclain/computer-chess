# Setting up and joining games

Read this for any game other than the simple case in SKILL.md section
1. That case is you as White, against the engine.

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

If the user asks for an easier or harder opponent, set the level to
match. Do the same for a named difficulty, such as "beginner" or
"hard". Do not try to play badly on purpose.

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

| Flag | Buys |
|---|---|
| `--friend-l10` | Level-10 move hints, for every engine |
| `--friend-l20` | Level-20 move hints, for every engine |
| `--friend-eval` | Full-strength Stockfish position evaluations |

The first two flags apply independently to GNU Chess and Stockfish. So
`turn` reports five separate remaining budgets: L10 GNU Chess, L20 GNU
Chess, L10 Stockfish, L20 Stockfish, and Stockfish Eval.

Each flag takes a count. It also accepts `-1` for unlimited, or `0` to
turn that kind off. Raise a budget if the user wants more help. None of
them carries over to the next game.

Omit a flag to accept the server's own starting value for it. Read the
budget you actually have from `chess.py turn`, as `phone-a-friend.md`
describes. Never assume a number.

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

Use the `join` command. It makes every check for you:

```bash
python3 scripts/chess.py join --side white --name "Deep Purple"
```

`join` reports one of these results:

- **Success.** It names your opponent's type and whose turn it is. Play
  from SKILL.md section 2, and pass the same `--side` every time.
- **The side is not joinable.** An `engine` or `web-user` side belongs
  to someone else. Tell the user. Then offer a new game with the colors
  set correctly.
- **The game already ended.** Report the result. Then offer a new game.
- **No game exists.** Start one instead, from section 1 above.

`--name` is optional. It sets your display name at the same time.

If the side is `api-trainee`, `join` prints a warning. Read
`trainee.md` before your first move.

When both sides are `api-user`, nothing in the game records which
caller owns which color. Ask the user which color you play. Then pass
that color as `--side` on every command, and never move for the other
one.
