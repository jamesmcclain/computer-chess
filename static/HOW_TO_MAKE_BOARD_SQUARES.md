# How to Make Board Squares

This document tells you how to make dark and light board-square images for the chess project.

## What a board square is

A board square is one tile of the chessboard. The tile is a square image. The image size is 256 by 256 pixels.

The project keeps two roles for tiles:

- **dark** — the dark squares on the board
- **light** — the light squares on the board

A **set** is one dark tile and one light tile that share the same material theme. Examples of sets are ebony/ivory, mahogany/ash, brushed metal, and cyberpunk.

The catalogue file is `static/boards/squares_catalogue.json`. That file lists each set, the path of each tile, the material name, and a short visual read.

## Structure of a square image

Each square image must obey these rules:

1. The width is 256 pixels.
2. The height is 256 pixels.
3. The material fills the full frame from edge to edge.
4. The image has no border, no margin, and no frame.
5. The image has no chess piece and no other object.
6. The view is flat and top-down (orthographic).
7. The lighting is even across the tile.
8. The file format is PNG.

You do **not** run the chroma-key script on board squares. Board squares are opaque textures. Only piece sprites use a green background and alpha.

## Where files go

Put square images in:

```text
static/boards/
```

Use clear names. Examples:

| Role  | Example path |
|-------|----------------|
| dark  | `static/boards/square_ebony.png` |
| light | `static/boards/square_ivory.png` |
| dark  | `static/boards/square_metal_dark.png` |
| light | `static/boards/square_metal_light.png` |

After you add a new set, update `static/boards/squares_catalogue.json`.

## Image service

The project uses the Krea 2 text-to-image API.

- Health check: `GET http://10.0.2.2:5002/health`
- Generate: `POST http://10.0.2.2:5002/generate`
- Body fields that you must set:
  - `prompt` (string)
  - `cfg` (number) — use `1.0` (do not use `0.0`)
  - `width` (number) — use `256`
  - `height` (number) — use `256`
  - `seed` (integer) — use a different seed for each image

**CAUTION:** Do not generate a larger image and then resize it. Generate at 256 by 256.

## Prompt rules for squares

Write the prompt so that the model fills the frame with one material. Include all of these ideas in the prompt:

1. **Seamless tileable square** — the tile is one board cell.
2. **Material name** — for example ebony, ivory, mahogany, ash, brushed steel.
3. **Flat top-down orthographic view** — not isometric, not a 3/4 view.
4. **Fills the entire frame edge to edge** — no empty border.
5. **No border, no margin, no frame**
6. **Continuous texture for a repeating chessboard square**
7. **Even lighting**
8. **No objects, no pieces, no characters**

For a dark square, name a dark material. For a light square, name a light material. Keep the two materials in one set related (same theme family).

### Example prompt (dark wood)

```text
seamless tileable square of polished ebony wood, dark black-brown hardwood grain,
flat top-down orthographic view, fills entire frame edge to edge, no border no
margin no frame, continuous wood texture suitable for repeating chessboard dark
square, even lighting, no objects no pieces
```

### Example prompt (light wood)

```text
seamless tileable square of polished pale ash wood, very light blonde hardwood,
subtle straight grain, flat top-down orthographic view, fills entire frame edge
to edge, no border no margin no frame, continuous wood texture suitable for
repeating chessboard light square, even lighting, no objects no pieces
```

### Example prompt (dark metal)

```text
seamless tileable square of dark brushed metal, gunmetal black steel surface,
subtle horizontal brush marks, flat top-down orthographic view, fills entire
frame edge to edge, no border no margin no frame, continuous metallic texture
suitable for repeating chessboard dark square, even studio lighting, no objects
no pieces no reflections of room
```

### Example prompt (cyberpunk light)

```text
seamless tileable square cyberpunk light panel, pale frosted white tech acrylic
surface with subtle soft neon pink and cyan circuit filigree, light holographic
sheen, flat top-down orthographic view, fills entire frame edge to edge, no
border no margin, continuous texture for repeating chessboard light square,
bright sci-fi, no objects no characters
```

## Procedure: make one square

1. Make sure that the image service answers on `/health`.
2. Choose the role (dark or light) and the material name.
3. Write a prompt with the rules in the section above.
4. Choose a unique `seed` integer.
5. Send one `POST` request to `/generate` with `cfg` `1.0`, `width` `256`, and `height` `256`.
6. Save the response body as a PNG in `static/boards/`.
7. Open the image and make sure that:
   - the material touches all four edges
   - no piece or object is in the frame
   - the tone matches the role (dark or light)

### Example command

```bash
curl -s -X POST http://10.0.2.2:5002/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "seamless tileable square of polished mahogany wood, rich reddish-brown hardwood grain, flat top-down orthographic view, fills entire frame edge to edge, no border no margin no frame, continuous wood texture suitable for repeating chessboard dark square, even lighting, no objects no pieces",
    "cfg": 1.0,
    "width": 256,
    "height": 256,
    "seed": 9101
  }' \
  -o "static/boards/square_mahogany.png"
```

## Procedure: make a full set

1. Make the dark square with a dark material prompt and seed A.
2. Make the light square with a light material prompt and seed B.
3. Compare the two images. Make sure that a player can tell dark from light at a glance.
4. Add one object to `sets` in `static/boards/squares_catalogue.json`.

### Catalogue entry shape

Each set in the catalogue has:

- `id` — short stable name (for example `mahogany_ash`)
- `label` — human title
- `theme` — short theme phrase
- `dark` — object with `path`, `role`, `material`, `read`
- `light` — object with `path`, `role`, `material`, `read`

Paths in the catalogue are relative to the catalogue file. The catalogue also stores `tile_size` as `[256, 256]`.

## Quality checks

Reject a square if any of these faults occur:

- empty margin or colored border around the material
- strong perspective or 3/4 view (the board needs a flat top view)
- chess piece, logo, or other object in the frame
- size other than 256 by 256
- dark and light tiles too close in brightness (bad contrast on the board)
- large hard seam lines that break when you tile the image

Note: diffusion tiles are not always seamless at the edges. If seams show on an 8 by 8 board, make a new pair with a more even texture, or blend the edges in a later step.

## Known sets

| id | Dark file | Light file |
|----|-----------|------------|
| `ebony_ivory` | `square_ebony.png` | `square_ivory.png` |
| `mahogany_ash` | `square_mahogany.png` | `square_ash.png` |
| `metal` | `square_metal_dark.png` | `square_metal_light.png` |
| `cyberpunk` | `square_cyberpunk_dark.png` | `square_cyberpunk_light.png` |

## Related files

- Catalogue: `static/boards/squares_catalogue.json`
- Piece guide: `HOW_TO_MAKE_PIECE_SET.md`
- Chroma-key tool (pieces only): `chroma_key.py`
