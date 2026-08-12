# Phoning a friend

You can ask for help with the current position without submitting a
move. It is available only on your own turn, and only when your side is
`api-user`, `api-trainee`, or `centaur`.

A query never changes the board, never ends your turn, and never counts
as your move. You still choose and submit your own move afterwards.

## The two kinds

| Query | Asks | Answered by |
|---|---|---|
| `eval` | Who is winning, and by how much | Stockfish, full strength |
| `10` or `20` | What to play here | GNU Chess or Stockfish, at that level |

They draw on separate budgets. Spending one does not cost you the
other.

```bash
# who is winning?
python3 scripts/chess.py phone-a-friend --side white eval

# what to play here. Level 20 is stronger than level 10
python3 scripts/chess.py phone-a-friend --side white 20
python3 scripts/chess.py phone-a-friend --side white 10:gnuchess

# several queries in one run
python3 scripts/chess.py phone-a-friend --side white eval 20:stockfish
```

`--side` is required, and the script refuses if it is not your turn.

## Reading a move hint

```
hint  stockfish L20 -> Nf3 (g1f3)  [0 left]
```

This is the move that engine chooses. You are free to ignore it.

## Reading an eval

```
eval  stockfish -> +0.34 (favors white)  [1 left]
```

The score is always from **White's** point of view, whichever side
asked. `+0.34` means White is ahead by about a third of a pawn. `-2.50`
means Black is ahead by about two and a half pawns.

**Read `favors` rather than working out the sign.** It says `white`,
`black`, or `equal` outright. This matters most when you play Black,
where a positive score means you are losing.

A score like `#3` means a forced mate in 3 for White. `#-2` means a
forced mate in 2 for Black.

An eval tells you how you stand. It does not tell you what to play. Ask
for a move hint if that is what you need.

## Budgets

Each budget is per side, per game.

| Budget | Set at game start with |
|---|---|
| Level 10, per engine | `--friend-l10` |
| Level 20, per engine | `--friend-l20` |
| `eval` | `--friend-eval` |

GNU Chess and Stockfish hold independent quotas. Asking GNU Chess for a
hint does not spend your Stockfish budget.

**Read your budget. Do not assume it.** Whoever starts the game sets
these numbers. A game can begin with any of them, including none at
all.

`chess.py turn --side white` prints what you have left. The numbers
below are one example, not a starting point to expect:

```
budget remaining: L10 GNU Chess 3; L20 GNU Chess 1; L10 Stockfish 0; L20 Stockfish 2; Stockfish Eval 4
```

The line always labels all five budgets separately: L10 GNU Chess, L20
GNU Chess, L10 Stockfish, L20 Stockfish, and Stockfish Eval. `-1` means
unlimited. `0` means that specific budget is spent — try another engine,
level, or kind of help, or decide on your own. In the example above,
GNU Chess still has hints at both levels, and Stockfish has level-20
hints only.

A failed query costs you nothing.

## When to use one

Use a query for a hard position. Good examples are a messy exchange, a
position you cannot read, and a move that looks risky. The budget is
small on purpose. Do not spend it every turn.

Tell the user whenever you ask for help, as part of your normal
narration.

Trainee mode changes this from optional to mandatory. See `trainee.md`.
