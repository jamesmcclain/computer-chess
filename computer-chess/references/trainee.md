# Trainee mode

Read this only if the user asked for "trainee mode", "training mode",
or `api-trainee` by name. Otherwise play as `api-user` and ignore this
file.

## What it is

`api-trainee` behaves exactly like `api-user` — same commands, same
responses — with two extra requirements on every move. The server
checks both *before* it applies the move.

It exists to force a real analysis process, instead of "make a legal
move".

## The two requirements

Before each move you must:

1. **Call `phone-a-friend` since your last move.** Any kind, any level,
   any engine satisfies this. The only exception is when your budget is
   zero everywhere — every level, every engine, *and* the
   `stockfish_eval` budget.
2. **Send both `--tactical` and `--strategic`.** `scripts/chess.py`
   requires both on every move already, so following SKILL.md covers
   this.

## The penalty

**Skipping either requirement forfeits the game immediately.** The
server discards your move without applying it, sets the status to
`forfeited`, and your opponent wins. There is no warning and no retry.

`chess.py move` prints this plainly when it happens:

```
FORFEITED by white — the move was NOT applied.
  - no phone-a-friend call before this move, despite having queries left
status: forfeited  winner: black  move 1
```

If you see it, tell the user plainly that you forfeited and why. Do not
hide it or describe it as an ordinary loss.

## How to play safely

Start the game with your side set to `api-trainee`:

```bash
python3 scripts/chess.py new --white api-trainee --black engine --level 10
```

Then add one step to the move loop in SKILL.md section 2. Between step
1 (read the position) and step 5 (submit), always run:

```bash
python3 scripts/chess.py phone-a-friend --side white eval
```

Make it a fixed habit for the whole game, not a decision you take each
turn.

`chess.py turn --side white` prints your remaining budget. Read it every
turn. While any number there is not `0`, you owe a call.

If you are ever unsure whether you already called phone-a-friend for
the current move, **call it again.** An extra call costs one unit of
budget. A skipped call costs the whole game.

## When the budget runs out

Once every budget shows `0`, the requirement lifts and you can move
without a call. Check the budget line rather than counting calls
yourself.

The reasoning requirement never lifts. Send `--tactical` and
`--strategic` on every move, always.
