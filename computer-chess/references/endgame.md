# Ending a game, and the transcript

## Reading the result

Every `chess.py` command prints a status line. While the game runs it
reads `status: in_progress`. Any other value means the game has ended,
and the line then names the winner.

Check that line after every move. Do not work the result out from the
move history yourself.

| `status` | Meaning |
|---|---|
| `checkmate` | The side to move is mated |
| `stalemate` | Draw — the side to move has no legal move |
| `draw_insufficient_material` | Draw — neither side can mate |
| `draw_75_moves` | Draw — 75-move rule, automatic |
| `draw_5fold_repetition` | Draw — fivefold repetition, automatic |
| `draw_claimable_50_moves` | A draw is claimable — 50-move rule reached |
| `draw_claimable_threefold_repetition` | A draw is claimable — threefold repetition |
| `resigned` | A side resigned |
| `aborted` | Someone ended the game early. No winner. |
| `forfeited` | An `api-trainee` side broke a requirement. See `trainee.md`. |

The winner is `white`, `black`, or none. None means a draw, an abort,
or no result yet. On checkmate, resignation, or forfeit the line names
the winner directly. Do not compute it yourself.

The two `draw_claimable_` values mean the game is still playable. Tell
the user a draw is available and ask what they want to do.

## Telling the user

Report the result in plain words: "Checkmate — Black wins", or "Draw by
stalemate". Do not read the raw status string out. Then offer a new
game.

## Resigning

```bash
python3 scripts/chess.py resign --side white
```

Resign only when the user asks, or when the position is hopeless and
you have told them why first.

## The transcript

A PGN transcript is available once the game has ended, and only then.
PGN is the standard plain-text chess format that lichess.org,
chess.com, and most chess software read.

```bash
python3 scripts/chess.py transcript --out game.pgn
```

`--out` writes the file and prints only the path. Prefer it. A long
annotated game is large, and you rarely need the whole thing in front
of you.

The file holds the metadata (players, result, engines and levels, how
the game ended), then the moves. Every move carries a comment with:

- the `chat` line you sent with it
- your `--tactical` and `--strategic` notes
- the eval bar's own read of the position after that move, if the bar
  was on

This is the only place your reasoning is ever given back to you. The
server withholds it while the game runs, so no opponent can read it.
The eval is given from White's point of view, as `+0.34` or `-1.20`,
or `#N` for a forced mate.

Use `--include moves` for the bare move list with no comments. Use it
only when the user wants the moves alone.

Fetch the transcript when the user asks for a copy of the game, a way
to review it later, or their own reasoning written out.
