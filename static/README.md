# Board & piece art (`static/chess/`)

This directory holds the visual assets the board viewer (port 5004) offers
in its appearance controls:

```
static/chess/boards/    8 square textures (4 dark + 4 light) + squares_catalogue.json
static/chess/pieces/    4 full piece sets (black + white, 6 types each) + pieces_catalogue.json
```

The viewer reads both `*_catalogue.json` files at request time (see
`GET /api/catalogue` in `viewer.py`) and turns them into the "Board style"
/ "Piece style" dropdowns on the page — add a new material to
`squares_catalogue.json` or a new set to `pieces_catalogue.json` (plus its
image files) and it shows up automatically, no code changes needed.

Each person's chosen style is a client-side preference (stored in their
browser's `localStorage`), not game state, so different viewers of the
same game can each pick their own look.

If this directory (or a catalogue file) is missing, the viewer falls back
to flat colors and Unicode chess glyphs (♔♕♖♗♘♙) — nothing breaks, the
appearance controls just have nothing to offer.
