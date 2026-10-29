"""Guards for readability issues that can't be seen without a browser."""
import os
import re

CSS = open(os.path.join(os.path.dirname(__file__), "..", "app", "static", "css", "app.css"), encoding="utf-8").read()


def test_sidebar_background_survives_bootstrap_offcanvas():
    # Bootstrap's .offcanvas-lg sets background-color: transparent !important on
    # wide screens; the sidebar must override it or its text becomes unreadable.
    rule = re.search(r"\.sidebar\.offcanvas-lg[^{]*\{([^}]*)\}", CSS)
    assert rule and "background-color" in rule.group(1) and "!important" in rule.group(1)


def test_sidebar_uses_dark_text_on_light_background():
    assert re.search(r"\.sidebar, \.sidebar\.offcanvas-lg \{[^}]*var\(--surface\)", CSS)
    assert "color: var(--ink-2)" in CSS  # nav links


def test_no_white_close_button_on_light_sidebar(student):
    assert b"btn-close-white" not in student.get("/student/dashboard").data
