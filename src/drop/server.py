"""Flask static-page server with cookie-form auth (full impl: Phase 8).

This minimal stub starts a Flask app that returns a placeholder so
Phase 6 lifecycle tests can verify the spawn+bind+stop flow. Phase 8
replaces with real routes (index, /p/<id>/[file], auth).
"""

from flask import Flask


def run_server(port: int = 8080, host: str = "0.0.0.0") -> None:
    app = Flask(__name__)

    @app.route("/")
    def _index():
        return "drop v2 server stub (Phase 8 not yet implemented)"

    app.run(host=host, port=port, debug=False, use_reloader=False)
