# Centaur mode

Read this only if the user asked for "centaur mode" or `centaur` by name.
Otherwise play as `api-user` and ignore this file.

## What it is

A `centaur` side never plays a move directly. It only *suggests* one, with
`suggest`, not `move`. A person at the board viewer (port 5004) decides:
accept the suggestion as-is, or play any other legal move instead. Either
way, that person's choice is what actually lands on the board — not yours.

This is "centaur chess": the engine (or you) proposes, the human disposes.

## The move loop, changed

Section 2 of SKILL.md still applies — read the position, narrate the
opponent's move, choose, narrate your choice — except step 5 changes:

```bash
python3 scripts/chess.py suggest --side white e2e4 \
  --chat "Good luck!" \
  --tactical "No captures or checks to calculate yet." \
  --strategic "e4 takes the center and frees the bishop and queen."
```

`--chat`, `--tactical`, and `--strategic` are all required, exactly like
`move`. Unlike `move`, this call **never applies the move**. It only
stores it as a suggestion for the person at the board to see.

After suggesting, wait for the person to act:

```bash
python3 scripts/chess.py wait --side white
```

`wait` returns once the position changes — whether the person accepted
your suggestion or played something else. Read the new position with
`turn` before suggesting again; do not assume your suggestion was the one
played.

## You can still phone a friend

A `centaur` side has the same phone-a-friend budget as `api-user` and
`api-trainee`. Ask before you suggest, the same way section 4 of
SKILL.md describes:

```bash
python3 scripts/chess.py phone-a-friend --side white eval
```

A query never touches the board and never counts as your suggestion.
Use it to inform the move you then send with `suggest`.

## What "suggest" is not

- It is not a move. It does not end your turn, and it does not change
  whose turn it is.
- It does not forfeit anything. Unlike `api-trainee`, a `suggest` call
  missing `--tactical` or `--strategic` is just refused (fix it and call
  again) — nothing is lost, because nothing was played.
- Calling `suggest` again before the person acts replaces your previous
  suggestion. There is no queue.
- There is no way to force your suggestion onto the board from here. A
  `move` call for a `centaur` side is always refused by the server — see
  `references/rest-api.md`.

## Narrate it to the user

Say what you're suggesting and why, same as an ordinary move — the user
may be watching the board viewer live and deciding whether to take your
advice.
