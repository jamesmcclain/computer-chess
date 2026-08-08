"""REST API (port 5003). All interaction with the game happens here.

No authentication — anyone who can reach the port can start games and
submit moves for either side. All requests/responses are JSON.
"""

from flask import Flask, jsonify, request

from game import GameError, LEVEL_MAX, LEVEL_MIN, describe_levels

API_DOC = {
    "description": "GNU Chess REST API. One game at a time; starting a new "
                    "game replaces any game already in progress.",
    "endpoints": {
        "POST /api/game": {
            "body": {"white": "api-user|engine", "black": "api-user|engine",
                     "level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional"},
            "description": "Start a new game. At least one side must be "
                            "'api-user'. 'level' sets gnuchess's difficulty "
                            "for this game (omit to keep whatever level "
                            "was last set); see GET /api/engine-levels. "
                            "If white is 'engine', gnuchess's opening "
                            "move is played immediately and returned as "
                            "'engine_move'.",
        },
        "GET /api/game": "Current game state (board, whose turn it is, "
                          "status, move log, engine level, ...). This is "
                          "also how to check whose turn it is — see the "
                          "'turn' field.",
        "GET /api/game/legal-moves": "Legal moves for the side to move. "
                                      "Optional query param: from=e2",
        "POST /api/game/move": {
            "body": {"move": "e2e4 (UCI) or e4 (SAN)"},
            "description": "Submit a move for whichever side is currently "
                            "to move. If it becomes the engine's turn "
                            "afterward, gnuchess replies immediately and "
                            "its move is returned as 'engine_move'.",
        },
        "POST /api/game/resign": {
            "body": {"player": "white|black"},
            "description": "Resign on behalf of a side, ending the game.",
        },
        "GET /api/engine-levels": "List of valid gnuchess difficulty "
                                   "levels (1=weakest..10=strongest) and "
                                   "their search-depth/time-cap tuning.",
        "POST /api/game/level": {
            "body": {"level": f"{LEVEL_MIN}-{LEVEL_MAX}"},
            "description": "Change gnuchess's difficulty. Works whether "
                            "or not a game is running, and takes effect "
                            "on the engine's next move.",
        },
    },
    "viewer": "A read-only board viewer is served separately on port 5004.",
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
        try:
            state, engine_move = game.new_game(white, black, level=level)
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
        if not move_str:
            return error("'move' is required (UCI, e.g. 'e2e4', or SAN, e.g. 'e4')")
        try:
            player_move, engine_move = game.make_move(move_str)
        except GameError as e:
            return error(str(e))
        return jsonify(move=player_move, engine_move=engine_move, state=game.state())

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
        if level is None:
            return error(f"'level' is required ({LEVEL_MIN}-{LEVEL_MAX})")
        try:
            new_level = game.set_level(level)
        except GameError as e:
            return error(str(e))
        return jsonify(level=new_level)

    return app
