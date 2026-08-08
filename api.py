"""REST API (port 5003). All interaction with the game happens here.

No authentication — anyone who can reach the port can start games and
submit moves for either side. All requests/responses are JSON.
"""

from flask import Flask, jsonify, request

from game import (
    GameError,
    LEVEL_MAX,
    LEVEL_MIN,
    WAIT_DEFAULT_TIMEOUT_SECONDS,
    WAIT_MAX_TIMEOUT_SECONDS,
    describe_levels,
)

API_DOC = {
    "description": "GNU Chess REST API. One game at a time; starting a new "
                    "game replaces any game already in progress.",
    "endpoints": {
        "POST /api/game": {
            "body": {"white": "api-user|web-user|engine", "black": "api-user|web-user|engine",
                     "level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional",
                     "white_level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional",
                     "black_level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional",
                     "white_name": "optional, up to 40 chars",
                     "black_name": "optional, up to 40 chars"},
            "description": "Start a new game, replacing any game already "
                            "in progress. Both sides can be 'engine'; the "
                            "two engines then play each other, paced one "
                            "move at a time, with no further calls needed. "
                            "'level' sets the difficulty for both sides at "
                            "once; 'white_level'/'black_level' set one "
                            "side's difficulty and win over 'level' for "
                            "that side, useful for an engine-vs-engine "
                            "game where the two sides differ. Any level "
                            "left unset keeps whatever was last set; see "
                            "GET /api/engine-levels. 'white_name'/"
                            "'black_name' set that side's display name, "
                            "shown in the board viewer and stamped on its "
                            "move-log entries; leave unset to keep "
                            "whatever name was last set for that side (see "
                            "POST /api/game/name). If white is 'engine' "
                            "and black is not, gnuchess's opening move is "
                            "played immediately and returned as "
                            "'engine_move'.",
        },
        "GET /api/game": "Current game state (board, whose turn it is, "
                          "status, move log, chat log, engine levels, "
                          "player names, ...). This is also how to check "
                          "whose turn it is — see the 'turn' field.",
        "GET /api/game/legal-moves": "Legal moves for the side to move. "
                                      "Optional query param: from=e2",
        "POST /api/game/move": {
            "body": {"move": "e2e4 (UCI) or e4 (SAN)",
                     "message": "optional, up to 240 chars",
                     "reasoning": "optional, up to 1000 chars"},
            "description": "Submit a move for whichever side is currently "
                            "to move. 'message' (optional) is a short chat "
                            "line attached to this move — it is stamped, "
                            "along with your current display name (see "
                            "POST /api/game/name), onto this move's entry "
                            "in 'move_log'. There is no separate inbox: "
                            "your opponent sees it the next time they read "
                            "the game state, e.g. in the response to their "
                            "own next move, or a plain GET /api/game. "
                            "'reasoning' (optional) is a private note on "
                            "why you chose this move — unlike 'message', "
                            "it is never returned by this or any other "
                            "endpoint; it is kept server-side only. If it "
                            "becomes the engine's turn afterward, gnuchess "
                            "replies immediately and its move is returned "
                            "as 'engine_move'.",
        },
        "GET /api/game/wait": {
            "query": {"color": "white|black",
                      "timeout": f"seconds, optional, default {WAIT_DEFAULT_TIMEOUT_SECONDS}, "
                                 f"max {WAIT_MAX_TIMEOUT_SECONDS}"},
            "description": "Block until it is 'color's turn, the game "
                            "ends, or 'timeout' seconds pass — whichever "
                            "comes first — then return {'state': ...}. "
                            "Returns immediately if it is already that "
                            "side's turn, the game already ended, or no "
                            "game has started. A timeout looks the same "
                            "as any other return; check 'state.turn' "
                            "yourself. Lets a side wait for its opponent's "
                            "move with one call instead of polling "
                            "GET /api/game in a loop.",
        },
        "POST /api/game/chat": {
            "body": {"color": "white|black", "message": "up to 240 chars"},
            "description": "Send a standalone chat message ('banter') "
                            "from 'color', not attached to any particular "
                            "move (contrast with 'message' on "
                            "POST /api/game/move). Visible to everyone who "
                            "reads the game state afterward, in "
                            "'chat_log', including a person watching the "
                            "board viewer. Requires a game to exist, but "
                            "not that it still be in progress. Returns "
                            "the created entry.",
        },
        "POST /api/game/resign": {
            "body": {"player": "white|black"},
            "description": "Resign on behalf of a side, ending the game.",
        },
        "GET /api/engine-levels": "List of valid gnuchess difficulty "
                                   "levels (1=weakest..10=strongest) and "
                                   "their search-depth/time-cap tuning.",
        "POST /api/game/level": {
            "body": {"level": f"{LEVEL_MIN}-{LEVEL_MAX}",
                     "color": "white|black, optional"},
            "description": "Change the engine's difficulty. Omit 'color' "
                            "to set both sides at once (all that matters "
                            "when only one side is 'engine'); pass 'color' "
                            "to change one side of an engine-vs-engine "
                            "game without touching the other. Works "
                            "whether or not a game is running, and takes "
                            "effect on that side's next move. Returns the "
                            "updated {'white': N, 'black': N}.",
        },
        "POST /api/game/name": {
            "body": {"color": "white|black", "name": "up to 40 chars"},
            "description": "Set (or, with an empty 'name', clear) one "
                            "side's display name. Shown in the board "
                            "viewer and stamped on that side's move-log "
                            "entries from then on. Useful for an API user "
                            "joining a game they did not start, since "
                            "'white_name'/'black_name' on POST /api/game "
                            "only set a name when that game is created. "
                            "Works whether or not a game is running. "
                            "Returns the updated "
                            "{'white': name_or_null, 'black': name_or_null}.",
        },
    },
    "viewer": "A board viewer is served separately on port 5004. It shows "
              "the game live and also lets a person start a game or play "
              "as 'web-user' by clicking the board — see that page.",
}


def create_api_app(game):
    app = Flask(__name__)

    def error(message, status=400):
        return jsonify(error=message), status

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(error="not found"), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify(error="method not allowed"), 405

    @app.get("/api")
    @app.get("/api/")
    def index():
        return jsonify(API_DOC)

    @app.post("/api/game")
    def new_game():
        body = request.get_json(silent=True) or {}
        white = body.get("white", "api-user")
        black = body.get("black", "engine")
        level = body.get("level")
        white_level = body.get("white_level")
        black_level = body.get("black_level")
        white_name = body.get("white_name")
        black_name = body.get("black_name")
        try:
            state, engine_move = game.new_game(
                white, black, level=level, white_level=white_level, black_level=black_level,
                white_name=white_name, black_name=black_name,
            )
        except GameError as e:
            return error(str(e))
        return jsonify(state=state, engine_move=engine_move), 201

    @app.get("/api/game")
    def get_state():
        if not game.is_started():
            return error("no game in progress; POST /api/game to start one", 404)
        return jsonify(game.state())

    @app.get("/api/game/legal-moves")
    def get_legal_moves():
        from_square = request.args.get("from")
        try:
            moves = game.legal_moves(from_square)
        except GameError as e:
            return error(str(e), 404 if "no game" in str(e) else 400)
        return jsonify(moves=moves, count=len(moves))

    @app.post("/api/game/move")
    def post_move():
        body = request.get_json(silent=True) or {}
        move_str = body.get("move")
        message = body.get("message")
        reasoning = body.get("reasoning")
        if not move_str:
            return error("'move' is required (UCI, e.g. 'e2e4', or SAN, e.g. 'e4')")
        try:
            player_move, engine_move = game.make_move(move_str, message=message, reasoning=reasoning)
        except GameError as e:
            return error(str(e))
        return jsonify(move=player_move, engine_move=engine_move, state=game.state())

    @app.get("/api/game/wait")
    def get_wait():
        color = request.args.get("color")
        timeout = request.args.get("timeout", type=float)
        try:
            state = game.wait_for_turn(color, timeout=timeout)
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.post("/api/game/chat")
    def post_chat():
        body = request.get_json(silent=True) or {}
        color = body.get("color")
        message = body.get("message")
        try:
            entry = game.send_chat(color, message)
        except GameError as e:
            return error(str(e))
        return jsonify(chat=entry, state=game.state())

    @app.post("/api/game/resign")
    def post_resign():
        body = request.get_json(silent=True) or {}
        player = body.get("player")
        try:
            state = game.resign(player)
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.get("/api/engine-levels")
    def get_engine_levels():
        return jsonify(describe_levels())

    @app.post("/api/game/level")
    def post_level():
        body = request.get_json(silent=True) or {}
        level = body.get("level")
        color = body.get("color")
        if level is None:
            return error(f"'level' is required ({LEVEL_MIN}-{LEVEL_MAX})")
        try:
            new_levels = game.set_level(level, color=color)
        except GameError as e:
            return error(str(e))
        return jsonify(levels=new_levels)

    @app.post("/api/game/name")
    def post_name():
        body = request.get_json(silent=True) or {}
        color = body.get("color")
        name = body.get("name")
        try:
            new_names = game.set_name(color, name)
        except GameError as e:
            return error(str(e))
        return jsonify(player_names=new_names)

    return app
