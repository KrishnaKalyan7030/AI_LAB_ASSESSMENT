#!/usr/bin/env bash
# One-time setup for macOS/Linux:  bash setup.sh
# Creates the virtual environment, installs packages, creates .env (with a
# generated secret key), asks for your Groq API key and creates the database.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/macos/ and re-run: bash setup.sh"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is required (you have $(python3 --version)). Install a newer Python from python.org."
  exit 1
fi

echo "==> Creating virtual environment (.venv)"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing packages"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo "==> Preparing .env"
python scripts/configure_env.py init

if ! python scripts/configure_env.py has-groq-key; then
  echo
  echo "Get a free Groq API key at https://console.groq.com/keys (it starts with gsk_)."
  printf "Paste your Groq API key and press Enter (or just press Enter to add it later): "
  read -r -s GROQ_KEY_INPUT || true
  echo
  if [ -n "$GROQ_KEY_INPUT" ]; then
    printf '%s\n' "$GROQ_KEY_INPUT" | python scripts/configure_env.py set-groq-key
  else
    echo "Skipped. Add GROQ_API_KEY=... to .env later and restart the app."
  fi
  unset GROQ_KEY_INPUT
fi

echo "==> Preparing database"
python init_db.py --if-missing

echo
echo "Setup complete. Start the app with:  bash start.sh"
