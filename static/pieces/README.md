# Piece images

Drop PNG images here to replace the built-in Unicode chess-glyph fallback
used by the board viewer (port 5004). No code changes needed — the viewer
looks for a file matching each piece and silently falls back to a glyph
if it's missing.

Expected filenames (case-sensitive), one per piece per color:

```
wP.png  wN.png  wB.png  wR.png  wQ.png  wK.png   (white pawn, knight, bishop, rook, queen, king)
bP.png  bN.png  bB.png  bR.png  bQ.png  bK.png   (black pawn, knight, bishop, rook, queen, king)
```

Any reasonable square-ish image works (the viewer scales it to fit each
board square); transparent-background PNGs look best.
