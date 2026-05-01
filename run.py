"""Entry point. `python run.py` to start the local Flask server."""

from app.config import PORT
from app.server import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=PORT, debug=False)
