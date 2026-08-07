"""Entry point.

Runs two Flask apps in the same process, sharing one ChessGame instance
directly in memory:
  - REST API      on port 5003 (api.py)    — all game control
  - Read-only viewer on port 5004 (viewer.py) — board display only

Only one game is supported at a time, so a single in-process shared
object (rather than a database) is all that's needed.
"""

import atexit
import threading

from api import create_api_app
from game import ChessGame
from viewer import create_viewer_app

API_PORT = 5003
VIEWER_PORT = 5004


def main():
    game = ChessGame()
    atexit.register(game.shutdown)

    api_app = create_api_app(game)
    viewer_app = create_viewer_app(game)

    api_thread = threading.Thread(
        target=lambda: api_app.run(
            host="0.0.0.0", port=API_PORT, threaded=True, use_reloader=False
        ),
        daemon=True,
        name="rest-api",
    )
    api_thread.start()

    print(f" * REST API listening on 0.0.0.0:{API_PORT}")
    print(f" * Board viewer listening on 0.0.0.0:{VIEWER_PORT}")

    # Run the viewer on the main thread so the process exits cleanly on
    # Ctrl-C / SIGTERM rather than needing both servers backgrounded.
    viewer_app.run(host="0.0.0.0", port=VIEWER_PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
