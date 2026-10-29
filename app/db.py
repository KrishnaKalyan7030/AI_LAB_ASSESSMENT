"""SQLite connection handling and database initialisation."""
import os
import sqlite3

import click
from flask import current_app, g


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        path = current_app.config["DATABASE"]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(seed: bool = True) -> None:
    """(Re)create every table and optionally load the sample data."""
    db = get_db()
    with current_app.open_resource("schema.sql") as fh:
        db.executescript(fh.read().decode("utf-8"))
    db.commit()
    if seed:
        from .seed import seed_database

        seed_database(db)


def ensure_database() -> str:
    """Create and seed the database if it does not exist yet.

    Returns "created", "ok" or "outdated" (made by an older version of the app).
    """
    db = get_db()
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'viva_scores'"
    ).fetchone()
    if row is None:
        init_db(seed=True)
        return "created"
    if "'groq'" not in (row["sql"] or ""):
        return "outdated"
    return "ok"


@click.command("init-db")
@click.option("--no-seed", is_flag=True, help="Create empty tables without sample data.")
def init_db_command(no_seed: bool) -> None:
    """Delete existing data, create tables and load sample accounts."""
    init_db(seed=not no_seed)
    click.echo("Database initialised" + ("" if no_seed else " with sample data") + ".")


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
