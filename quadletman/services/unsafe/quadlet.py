"""Quadlet text-processing helpers that take plain ``str``.

These functions format Jinja2 render output and compare on-disk unit files.
They operate on internally generated strings, never user-supplied input.
"""

import difflib
import os
import re

_MULTI_BLANK = re.compile(r"\n{3,}")


def tidy(content: str) -> str:
    """Collapse runs of 3+ newlines to a single blank line."""
    return _MULTI_BLANK.sub("\n\n", content)


def render_unit(jinja_env, template_name: str, **ctx) -> str:
    """Render a Jinja2 quadlet template and tidy the result."""
    return tidy(jinja_env.get_template(template_name).render(**ctx))


def compare_file(path: str, expected: str) -> dict | None:
    """Return a sync issue dict if the file is missing or differs, else None."""
    filename = os.path.basename(path)
    try:
        with open(path) as _f:
            actual = tidy(_f.read())
    except FileNotFoundError:
        diff = "".join(
            difflib.unified_diff(
                [],
                expected.splitlines(keepends=True),
                fromfile=f"{filename} (on disk)",
                tofile=f"{filename} (expected)",
            )
        )
        return {"file": filename, "status": "missing", "diff": diff or "(file missing)"}
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{filename} (on disk)",
                tofile=f"{filename} (expected)",
            )
        )
        return {"file": filename, "status": "changed", "diff": diff}
    return None
