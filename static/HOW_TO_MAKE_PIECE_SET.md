# How to Make a Full Piece Set

This document tells you how to make one full set of chess piece images for the project. The same method works for black pieces and for white pieces.

## What a piece set is

A full piece set has six piece types:

| Type | Role on the board |
|------|-------------------|
| king | royal piece with a cross on the crown |
| queen | royal piece with a coronet and a ball on top |
| rook | castle tower with battlements |
| bishop | mitre top with a diagonal slit |
| knight | horse head on a base |
| pawn | small piece with a ball head |

For one color you make six images (one per type). For a full army you make twelve images (six black and six white).

A **material set** groups those twelve finished images under one material name (for example `playdough`). The piece catalogue lists each material set.

## Image structure

### Work image (before chroma key)

1. The width is 256 pixels.
2. The height is 256 pixels.
3. The view is isometric 3/4 (not top-down).
4. One piece sits near the center of the frame.
5. The background is solid green near `#00FF00` for chroma key.
6. The image has no board, no second piece, and no heavy drop shadow.
7. The piece type is easy to read (crown and cross for the king, and so on).
8. The file format is PNG.

### Finished image (after chroma key)

1. Same size and view as the work image.
2. The piece has a clear background so the board shows through.
3. The file is the final artifact that the catalogue points to.
4. The project does not keep the green work image after the set is done.

The chroma-key script turns near-green background pixels clear. The diffusion model does not make pure `#00FF00`, so the script keys a green range, not one exact color.

## Directory layout

```text
static/
  chroma_key.py
  HOW_TO_MAKE_PIECE_SET.md
  pieces/
    pieces_catalogue.json
    black/
      <material>/
        black_king.png
        black_queen.png
        black_rook.png
        black_bishop.png
        black_knight.png
        black_pawn.png
    white/
      <material>/
        white_king.png
        white_queen.png
        white_rook.png
        white_bishop.png
        white_knight.png
        white_pawn.png
```

Replace `<material>` with a short material id (for example `playdough`).

### Naming rules

| Item | Rule | Example |
|------|------|---------|
| Material folder | short id, lowercase | `playdough` |
| Finished file | `<color>_<type>.png` | `black_king.png` |
| Catalogue paths | relative to `pieces_catalogue.json` | `black/playdough/black_king.png` |

Do **not** put the material name in the file name. The folder already names the material. Do **not** keep `_rgba` or `_playdough` suffixes on finished files.

### Work files (temporary)

Generate green-background work images in a scratch place (for example `/tmp` or `static/work/`). After chroma key and a visual check, move the finished clear-background PNG into the correct `pieces/<color>/<material>/` path with the final name. Then delete the green work image.

## Style used in this project

The current army uses a **dark gray or off-white playdough** look:

- soft matte clay
- rounded edges
- handmade craft surface
- classic Staunton silhouette

Black pieces use dark gray playdough. White pieces use off-white or cream playdough.

Finished playdough files live under:

- `static/pieces/black/playdough/`
- `static/pieces/white/playdough/`

If you add a new material, make all six types for both colors in that material so the set stays complete and consistent.

## Piece catalogue

File: `static/pieces/pieces_catalogue.json`

The catalogue lists material sets from a user view (how the piece looks), not pipeline detail. Each set has:

- `id`, `label`, `theme`, `material`, `view`
- a short set-level `read`
- `pieces.black` and `pieces.white`, each with the six types
- per piece: `path`, `type`, `color`, and a short `read`

After you add or replace finished pieces, update the catalogue paths and reads.

## Image service

The project uses the Krea 2 text-to-image API.

- Health check: `GET http://10.0.2.2:5002/health`
- Generate: `POST http://10.0.2.2:5002/generate`
- Body fields that you must set:
  - `prompt` (string)
  - `cfg` (number) — use `1.0` (do not use `0.0`)
  - `width` (number) — use `256`
  - `height` (number) — use `256`
  - `seed` (integer) — use a different seed for each piece

**CAUTION:** Do not generate a larger image and then resize it. Generate at 256 by 256.

**CAUTION:** Do not drive many different prompts from one shell loop that reuses one prompt string by mistake. Send one clear `curl` call per piece with its own prompt and seed. Identical prompts with careless automation can yield near-duplicate images.

## Prompt rules for pieces

Include all of these ideas in each piece prompt:

1. **isometric 3/4 view**
2. **color and material** — for example `dark gray playdough` or `off-white playdough`
3. **piece type and Staunton cues** (see the table below)
4. **single piece centered**
5. **pure solid `#00FF00` green background**
6. **no board, no shadow** (or no heavy shadow)
7. soft matte clay / handmade look when you use the playdough style

### Staunton cues by type

| Type | Put these words in the prompt |
|------|-------------------------------|
| king | classic Staunton king, coronet, clear cross finial |
| queen | classic Staunton queen, coronet crown, ball finial |
| rook | classic Staunton rook, crenellated battlement top, wide base |
| bishop | classic Staunton bishop, mitre top, diagonal slit, ball finial |
| knight | classic Staunton knight, horse head facing right, clay base |
| pawn | classic Staunton pawn, ball head, simple collar ring, wide base |

### Words that help

- soft matte clay, rounded edges, handmade modeling dough
- stacked clay rings (for king, queen, bishop, rook bodies)
- short, squat, stubby, wide base (if you want a low piece)

### Words that hurt

- toadstool or other non-chess metaphors that hide the type
- busy scenes, boards, hands, multiple pieces
- glossy plastic when the rest of the set is matte clay

Note: the model often ignores “short” and “squat” and still draws a tall Staunton king. Prefer a clear crown and cross over a strange short metaphor that no longer reads as a king.

### Example prompts

**Black king (playdough):**

```text
isometric 3/4 view short squat stubby black chess king piece, dark gray
childrens modeling playdough, soft matte lumpy handmade clay, classic king
crown coronet with clear cross finial on top, thick stacked clay rings, pure
solid #00FF00 green background, single piece centered, no board no shadow
```

**White knight (playdough):**

```text
isometric 3/4 view white chess knight piece sculpted from off-white cream
playdough modeling clay, soft matte clay handmade look, classic Staunton horse
head knight facing right, rounded soft mane and snout, clay base pedestal,
pure solid #00FF00 green background, single game piece centered no board no
shadow
```

**Black pawn (playdough):**

```text
isometric 3/4 view black chess pawn piece of dark gray childrens playdough
clay, soft matte rounded handmade texture, classic Staunton pawn with ball
head and simple collar ring, stubby clay body wide base, pure solid #00FF00
green background, single centered piece no board no shadow
```

## Procedure: make one piece

1. Make sure that the image service answers on `/health`.
2. Choose the color (`black` or `white`), the type, and the material id.
3. Write a prompt with the rules and the Staunton cues for that type.
4. Choose a unique `seed`.
5. Send one `POST` to `/generate` with `cfg` `1.0` and size 256 by 256.
6. Save the green work PNG in a scratch path.
7. Open the work image and make sure that:
   - the piece type is obvious
   - the background is green
   - only one piece is in the frame
8. Run the chroma-key script on the work image.
9. Move the clear-background result to:
   `static/pieces/<color>/<material>/<color>_<type>.png`
10. Delete the green work image and any extra keyed copy in scratch.
11. Update `pieces_catalogue.json` if this path is new.

### Example command (black rook work image)

```bash
mkdir -p "static/work"
curl -s -X POST http://10.0.2.2:5002/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "isometric 3/4 view black chess rook castle piece of dark gray playdough modeling clay, soft squishy matte handmade texture rounded edges, classic Staunton rook with crenellated castle top battlements, wide clay base, pure solid #00FF00 green background, single centered game piece no board no shadow",
    "cfg": 1.0,
    "width": 256,
    "height": 256,
    "seed": 6002
  }' \
  -o "static/work/black_rook_green.png"
```

### Example: key and install the finished piece

```bash
python3 "static/chroma_key.py" \
  "static/work/black_rook_green.png"

mkdir -p "static/pieces/black/playdough"
mv -f "static/work/black_rook_green_rgba.png" \
  "static/pieces/black/playdough/black_rook.png"
rm -f "static/work/black_rook_green.png"
```

The chroma-key tool writes `<name>_rgba.png` next to the work file by default. Rename that file when you install it into `pieces/`.

## Procedure: make a full color set (six pieces)

1. Create `static/pieces/black/<material>/` or `.../white/<material>/` as needed.
2. Make the king, queen, rook, bishop, knight, and pawn (work image, key, install).
3. Compare all six finished files side by side. Reject any piece that does not match the material or that hides its type.
4. If a piece fails, generate that type again with a new seed and a clearer type cue.
5. Update the piece catalogue for this material and color.

For both colors, run the six-piece procedure twice: once for black, once for white. Keep material language parallel so the two colors look like one army.

## Chroma key (green to clear)

Tool: `static/chroma_key.py`

The script does this work:

1. Scores pixels that look like green screen (hue near green, and green channel stronger than red and blue).
2. Builds a soft clear/opaque mask.
3. Softens only edge pixels.
4. Reduces green fringe on the piece edge (despill).
5. Writes a clear-background PNG next to the source (`*_rgba.png` by default).

Pass explicit work-file paths. Do not rely on old default folders under `static/black` or `static/white`. Those paths are no longer the piece layout.

### Run on one work file

```bash
python3 "static/chroma_key.py" \
  "static/work/black_king_green.png"
```

### Dependencies

```bash
pip install pillow numpy
```

## Quality checks

Reject a piece if any of these faults occur:

- size other than 256 by 256
- type not clear (for example a king with no cross)
- work image background not green enough for the key
- second piece, board, or text in the frame
- material very different from the other five pieces in the same color
- after chroma key: large green fringe, holes in the piece, or lost crown details
- finished file not at `pieces/<color>/<material>/<color>_<type>.png`

Good finished results show:

- clear background
- solid piece body
- little or no green edge glow

## Checklist for one full army (12 finished pieces)

Material example: `playdough`.

- [ ] `pieces/black/playdough/black_king.png`
- [ ] `pieces/black/playdough/black_queen.png`
- [ ] `pieces/black/playdough/black_rook.png`
- [ ] `pieces/black/playdough/black_bishop.png`
- [ ] `pieces/black/playdough/black_knight.png`
- [ ] `pieces/black/playdough/black_pawn.png`
- [ ] `pieces/white/playdough/white_king.png`
- [ ] `pieces/white/playdough/white_queen.png`
- [ ] `pieces/white/playdough/white_rook.png`
- [ ] `pieces/white/playdough/white_bishop.png`
- [ ] `pieces/white/playdough/white_knight.png`
- [ ] `pieces/white/playdough/white_pawn.png`
- [ ] `pieces/pieces_catalogue.json` lists this material set

## Related files

- Chroma-key tool: `static/chroma_key.py`
- Piece catalogue: `static/pieces/pieces_catalogue.json`
- Board square guide: `static/HOW_TO_MAKE_BOARD_SQUARES.md`
- Board catalogue: `static/boards/squares_catalogue.json`
