# Dockerfile — GNU Chess REST API
#
# Build:
#   docker build -t gnuchess-api .
#
# Run:
#   docker run -it --rm -p 5003:5003 -p 5004:5004 gnuchess-api
#
# Port 5003: REST API (start games, submit moves, query state — no auth).
# Port 5004: read-only web viewer of the current board (no input).
#
# See run.sh for a convenience wrapper around the above.

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/games:${PATH}"

# gnuchess: the chess engine. It plays the "engine" side of a game (spoken
#   to over UCI, via `gnuchess --uci`) while python-chess (installed below)
#   is used as the authoritative board/rules/move-legality implementation.
# python3 / python3-pip: run the Flask REST API + viewer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gnuchess \
        python3 \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir flask "chess>=1.10,<2"

WORKDIR /app

COPY server.py game.py api.py viewer.py /app/
COPY static/ /app/static/

# Board-square and piece-set art the viewer's appearance controls read
# from (see static/README.md). If this ends up empty, the viewer just
# falls back to flat colors and Unicode chess glyphs.
RUN mkdir -p /app/static/chess/boards /app/static/chess/pieces

EXPOSE 5003 5004

CMD ["python3", "/app/server.py"]
