"""Start the AI Laboratory Assessment System:  python run.py

* Reads settings from .env (values in .env win over old terminal variables).
* Creates the database with sample data automatically on first run.
* Uses port 5001 (macOS AirPlay often occupies 5000); if 5001 is busy it
  picks the next free port and prints the address.
"""
import os
import socket
import sys

if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or newer is required. Check with: python3 --version")

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("Required packages are missing. Run:  bash setup.sh   "
             "(or: source .venv/bin/activate && pip install -r requirements.txt)")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

from app import create_app  # noqa: E402
from app.db import ensure_database  # noqa: E402
from app.services.ai_provider import get_ai_service  # noqa: E402

app = create_app()


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def _banner(port: int, db_state: str) -> None:
    service = get_ai_service(app.config)
    line = "-" * 64
    print(line)
    print(" AI Laboratory Assessment System")
    print(f" Open in your browser:  http://127.0.0.1:{port}")
    if service.is_configured:
        print(f" AI: {service.display_name} is configured (model: {service.model})")
    else:
        key_name = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY",
                    "claude": "ANTHROPIC_API_KEY"}[service.provider]
        print(f" AI: {service.display_name} is NOT configured - add {key_name} to .env and restart.")
        print("     (Everything else works; AI buttons will show a clear message.)")
    if db_state == "created":
        print(" Database created with sample data.")
    elif db_state == "outdated":
        print(" WARNING: the database was made by an older version. Run:  python init_db.py")
    print(" Sample logins: teacher1 / Teacher@123   student1 / Student@123")
    print(" Stop the server: press Control + C")
    print(line, flush=True)


if __name__ == "__main__":
    with app.app_context():
        state = ensure_database()
    port = _free_port(int(os.environ.get("PORT", "5001")))
    _banner(port, state)
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1",
            use_reloader=False)
