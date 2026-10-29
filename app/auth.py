"""Authentication (login/logout/registration) and role-based authorisation helpers."""
from __future__ import annotations

import hmac
import re
import sqlite3
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute(
            "SELECT id, username, full_name, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(role: str):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def home_for(user) -> str:
    if user is None:
        return url_for("auth.login")
    return url_for("teacher.dashboard" if user["role"] == "teacher" else "student.dashboard")


@bp.route("/")
def index():
    return redirect(home_for(g.user))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None and request.method == "GET":
        return redirect(home_for(g.user))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "danger")
            return render_template("login.html", username=username), 401
        # Prevent session fixation: start a fresh session on login.
        session.clear()
        session["user_id"] = user["id"]
        nxt = request.args.get("next", "")
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(home_for(user))
    return render_template("login.html")


USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
ROLES = ("student", "teacher")


def _validate_registration(form) -> tuple[dict, list]:
    values = {
        "full_name": " ".join(form.get("full_name", "").split())[:100],
        "username": form.get("username", "").strip(),
        "role": form.get("role", "student"),
    }
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")
    errors = []
    if len(values["full_name"]) < 2:
        errors.append("Please enter your full name.")
    if not USERNAME_RE.match(values["username"]):
        errors.append("Username must be 3–30 characters: letters, numbers, dot, dash or underscore.")
    if values["role"] not in ROLES:
        errors.append("Choose a valid role.")
        values["role"] = "student"
    if len(password) < 8 or len(password) > 128:
        errors.append("Password must be 8–128 characters long.")
    elif not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        errors.append("Password must contain at least one letter and one number.")
    elif password.lower() == values["username"].lower():
        errors.append("Password must be different from the username.")
    if password != confirm:
        errors.append("Passwords do not match.")

    if values["role"] == "teacher":
        expected = current_app.config.get("TEACHER_REGISTRATION_CODE", "")
        if not expected:
            errors.append("Teacher self-registration is disabled. Ask the administrator for a "
                          "teacher account.")
        elif not hmac.compare_digest(form.get("teacher_code", ""), expected):
            errors.append("The teacher registration code is incorrect.")
    return {**values, "password": password}, errors


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(home_for(g.user))
    teacher_enabled = bool(current_app.config.get("TEACHER_REGISTRATION_CODE"))
    if request.method == "POST":
        limiter = current_app.extensions["ai_rate_limiter"]
        if not limiter.allow(f"register:{request.remote_addr}",
                             int(current_app.config.get("REGISTRATION_LIMIT_PER_HOUR", 10)), 3600):
            abort(429, description="Too many registration attempts. Please try again later.")
        values, errors = _validate_registration(request.form)
        db = get_db()
        if not errors and db.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)",
                                     (values["username"],)).fetchone():
            errors.append("That username is already taken.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form=values,
                                   teacher_enabled=teacher_enabled), 400
        try:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (?, ?, ?, ?)",
                (values["username"], generate_password_hash(values["password"]),
                 values["full_name"], values["role"]),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "danger")
            return render_template("register.html", form=values,
                                   teacher_enabled=teacher_enabled), 400
        session.clear()
        session["user_id"] = cur.lastrowid
        flash(f"Welcome, {values['full_name']}! Your {values['role']} account has been created.", "success")
        return redirect(url_for("teacher.dashboard" if values["role"] == "teacher" else "student.dashboard"))
    return render_template("register.html", form={"role": "student"}, teacher_enabled=teacher_enabled)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
