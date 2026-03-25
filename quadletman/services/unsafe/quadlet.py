"""Quadlet text-processing helpers that take plain ``str``.

These functions format Jinja2 render output and compare on-disk unit files.
They operate on internally generated strings, never user-supplied input.
"""

import difflib
import os
import re

_MULTI_BLANK = re.compile(r"\n{3,}")
_NOT_SET = object()  # sentinel: "caller did not provide on_disk_content"


def tidy(content: str) -> str:
    """Collapse runs of 3+ newlines to a single blank line."""
    return _MULTI_BLANK.sub("\n\n", content)


def render_unit(jinja_env, template_name: str, **ctx) -> str:
    """Render a Jinja2 quadlet template and tidy the result."""
    return tidy(jinja_env.get_template(template_name).render(**ctx))


def compare_file(path: str, expected: str, on_disk_content: str | None = _NOT_SET) -> dict | None:
    """Return a sync issue dict if the file is missing or differs, else None.

    *on_disk_content* is the pre-read file content.  Pass ``None`` when the
    file does not exist on disk (produces a "missing" issue).  When omitted,
    the file is read directly (only works when the process can access the path).
    """
    filename = os.path.basename(path)
    if on_disk_content is _NOT_SET:
        try:
            with open(path) as _f:
                on_disk_content = _f.read()
        except FileNotFoundError:
            on_disk_content = None
    if on_disk_content is None:
        diff = "".join(
            difflib.unified_diff(
                [],
                expected.splitlines(keepends=True),
                fromfile=f"{filename} (on disk)",
                tofile=f"{filename} (expected)",
            )
        )
        return {"file": filename, "status": "missing", "diff": diff or "(file missing)"}
    actual = tidy(on_disk_content)
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
