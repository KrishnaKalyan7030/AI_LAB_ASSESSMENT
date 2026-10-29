"""Create (or reset) the SQLite database and load sample data.

    python init_db.py              # reset: delete all data, recreate sample data
    python init_db.py --no-seed    # reset to empty tables
    python init_db.py --if-missing # only create it if it does not exist yet

Note: `python run.py` also creates the database automatically on first run.
"""
import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("Required packages are missing. Run:  bash setup.sh")

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)

from app import create_app  # noqa: E402
from app.db import ensure_database, init_db  # noqa: E402

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        if "--if-missing" in sys.argv:
            state = ensure_database()
            print({"created": "Database created with sample data.",
                   "ok": "Database already exists - left unchanged.",
                   "outdated": "Database is from an older version. Run: python init_db.py"}[state])
        else:
            init_db(seed="--no-seed" not in sys.argv)
            print(f"Database reset at {app.config['DATABASE']}")
            if "--no-seed" not in sys.argv:
                print("Sample logins: teacher1 / Teacher@123, student1 / Student@123")
