---
name: computer-chess
description: Play chess through the computer-chess REST service, a dockerized chess server (GNU Chess and Stockfish, both equally supported) with a JSON API on port 5003 and a board viewer on port 5004. Use this skill when the user wants to start, join, play, watch, or check a game — against an engine, another API user, or a person on the board viewer. It also covers a specific move, the side to move, and the game result. Read this skill before you call the API by hand. It gives the move loop, the scripts/chess.py helper that makes the calls, and the turn-taking and end-of-game rules for correct play.
---

# Playing chess through computer-chess

Use `scripts/chess.py` for every call. It wraps the REST API. It prints
a short digest instead of raw JSON, and it refuses to move for the wrong
side. Section 6 covers the raw endpoints, for the rare case where you
cannot run the script.

Assume the container runs already. The script defaults to
`http://10.0.2.2:5003`. If you were given a different host or port, pass
`--url`, or set `CHESS_API` once.

```bash
python3 scripts/chess.py --help
```

## The rules that matter most

Read these before your first move.

- **Never write a `for` or `while` loop that plays moves.** This applies
  to the shell and to Python. Make one call per move. Think about the
  move before you send it. A loop skips the think step and plays a whole
  game blind. See section 2.
- **Never use `sleep`, and never poll in a loop.** To wait for an
  opponent, use `chess.py wait`. It blocks until your turn. See
  section 3.
- **Play one side only.** You never play both sides of a game.
- **Keep playing until the game ends.** Play every turn on your own. Do
  not stop for user input between moves. Stop only if the user tells you
  to stop.
- **Trust the board, not your memory.** Read the status line from each
  command. You cannot predict an opponent's move.
- **Narrate every turn to the user.** Say what the opponent's move does.
  Then say why you chose yours. Narration is not a stop — keep playing.
- **Send `--chat`, `--tactical`, and `--strategic` on every move.** The
  script requires all three. `--chat` is banter only: your opponent
  reads it. Your analysis goes in the other two, which stay private
  until the game ends.

One game runs at a time. A new game replaces any game in progress.

## 1. Starting or joining a game

To start a game against the engine, as White:

```bash
python3 scripts/chess.py new --white api-user --black engine --level 10
```

`--level` runs 0 to 20, weakest to strongest. Set it to match any
difficulty the user asks for.

To take over a side of a game that already runs, use `join`:

```bash
python3 scripts/chess.py join --side white --name "Deep Purple"
```

`join` confirms that the side is one you can play, and that the game is
still running. It refuses an `engine` or `web-user` side. The `--name`
is optional and cosmetic.

Read **`references/setup.md`** for these topics:

- playing Black, or against a person or another API user
- choosing GNU Chess or Stockfish, and setting the difficulty
- display names and hint budgets
- two engines playing each other
- joining a game in more detail

To change a name, a level, or an engine during a game, use `set`:

```bash
python3 scripts/chess.py set --side white --name "Deep Purple"
python3 scripts/chess.py set --side black --level 3
```

If the user asks for "trainee mode" by name, read
**`references/trainee.md`** first. Do not use trainee mode otherwise.

## 2. The move loop

Repeat these steps for each of your turns. Perform the loop yourself,
one turn at a time. Never write a code loop that does it for you.

**Step 1 — read the position.**

```bash
python3 scripts/chess.py turn --side white
```

This one command prints everything you need:

- the status, and whose turn it is
- the opponent's last move, and any chat on it
- the board and the FEN
- a `tactics:` summary — loose material, pins, and the checks and
  captures available to you
- your hint budget, and the legal moves

Read the `tactics:` lines before you choose. They name the pieces that
can be taken on both sides. You do not have to find them by eye.

NOTE: the tactics summary marks squares worth a second look. It counts
direct attackers and defenders only. Confirm a capture before you trust
it.

**Step 2 — narrate the opponent's move.** Tell the user in plain words
what their move does. Do this before you plan your own.

**Step 3 — choose a move.** Pick one from the legal list. If the
position is hard to read, you can ask for help first (section 4).

**Step 4 — narrate your choice.** Tell the user the idea behind the
move. Say why you preferred it to the alternatives. Do this every turn,
not only for unusual moves.

**Step 5 — submit it.**

```bash
python3 scripts/chess.py move --side white e2e4 \
  --chat "Good luck!" \
  --tactical "No captures or checks to calculate yet." \
  --strategic "e4 takes the center and frees the bishop and queen."
```

`--side` is required. The API has no login. In a game between two API
callers, the server accepts a move sent on the wrong side and applies
it. The script checks whose turn it is, and it refuses when the turn is
not yours.

The script also checks your move against the legal list before it sends
it. If the move is illegal, the script prints the legal moves and sends
nothing.

The output shows your move, the engine's reply if there is one, and the
new status. Treat that reply as the opponent move you narrate in step 2
of your next turn.

**Step 6 — repeat from step 1** until the game ends. Do this on your
own, with no stop for user input.

## 3. Waiting for a human or another API user

An engine replies inside your `move` call. Any other opponent needs a
wait:

```bash
python3 scripts/chess.py wait --side white
```

The call blocks until your turn comes, the game ends, or the timeout
passes. Tell the user you are waiting before you call it. If the output
says there was no change, call it again.

## 4. Asking for help with a position

You have a small budget of hints per game. A hint does not change the
board and does not end your turn.

```bash
python3 scripts/chess.py phone-a-friend --side white eval
python3 scripts/chess.py phone-a-friend --side white 20:stockfish
python3 scripts/chess.py phone-a-friend --side white eval 10
```

- `eval` asks Stockfish who is winning, at full strength.
- `10` or `20` asks an engine for its own choice of move. Level 20 is
  stronger.
- `LEVEL:ENGINE` picks the engine, for example `20:stockfish`.

You can ask more than one query in a run, as the third example shows.
Use hints only for a hard position — the budget is small on purpose.
Tell the user whenever you use one.

Read **`references/phone-a-friend.md`** for the budgets, how the two
kinds differ, and how to read an eval score.

## 5. The end of the game

The status line names the result. When `status:` changes from
`in_progress`, the game is over.

Report the result to the user in plain words, such as "Checkmate — Black
wins" or "Draw by stalemate". Do not read out the raw status string.
Then offer a new game.

CAUTION: The next two commands end the game at once. You cannot undo
either one. Use them only when the user asks, or when you have told the
user why you resign.

```bash
python3 scripts/chess.py resign --side white   # the other side wins
python3 scripts/chess.py resign --abort        # ends with no winner
```

To save the annotated PGN once the game has ended:

```bash
python3 scripts/chess.py transcript --out game.pgn
```

Read **`references/endgame.md`** for every status value, the draws you
can claim, and what the transcript contains.

## 6. Calling the API without the script

Use the script when you can. If you cannot run it, read
**`references/rest-api.md`** for the endpoints and response shapes. The
repository's `README.md` is the full reference.

The rules in this file still apply when you call the API directly. They
govern how you play, not which tool you use.

## Watching without playing

`http://10.0.2.2:5004/` is a live HTML board for a user who wants to
watch. A person there can start a game, play a `web-user` side by
clicking, chat, and resign. That is the user acting, not you. Always act
through the API yourself.
