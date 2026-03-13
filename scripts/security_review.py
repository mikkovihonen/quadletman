#!/usr/bin/env python3
"""Security review prompt generator for quadletman.

Collects the current git diff and prints a formatted prompt for Claude Code.
Run via the VS Code task "Security Review" and paste the output into a Claude
Code chat in VS Code.

Usage:
    uv run python scripts/security_review.py              # diff against HEAD
    uv run python scripts/security_review.py --staged     # staged changes only
    uv run python scripts/security_review.py --branch     # diff since branch from main
"""

import argparse
import subprocess
import sys
import textwrap

# Files and directories that are security-relevant in this project.
# The review is skipped when none of these paths appear in the diff.
_SECURITY_PATHS = (
    "quadletman/routers/",
    "quadletman/auth.py",
    "quadletman/main.py",
    "quadletman/models.py",
    "quadletman/services/",
    "quadletman/session.py",
    "quadletman/database.py",
)

_PROMPT_HEADER = textwrap.dedent("""\
    You are doing a security review for **quadletman** — a FastAPI web application
    that runs as root and manages Podman container services on a Linux host.

    Review the git diff below. The app runs as root, so a missed security issue can
    directly affect the host system. Focus only on real issues, not style.

    Check for:

    **Routes (HTTP and WebSocket)**
    - Missing `Depends(require_auth)` on new routes
    - POST/PUT/DELETE without `X-CSRF-Token` header in the JS caller
    - WebSocket endpoints missing Origin header validation (browser CSRF via WebSocket)
    - WebSocket endpoints missing manual `qm_session` cookie validation

    **User input → filesystem**
    - Path traversal: user-supplied paths not resolved through `_resolve_vol_path()`
    - File writes not using `os.open(O_NOFOLLOW)` to block symlink-swap (TOCTOU)
    - Filenames from HTTP clients not sanitised with `re.sub(r"[^\\w.\\-]", "_", ...)`

    **Pydantic model fields**
    - Strings reaching unit files or shell commands without `_no_control_chars()`
    - Image references not validated against `_IMAGE_RE`
    - Bind-mount `host_path` not checked against `_BIND_MOUNT_DENYLIST`

    **subprocess**
    - `shell=True` with any user-controlled data
    - Command built by string concatenation rather than list

    **Archive/file upload**
    - Raw `extractall()` instead of the `_extract_zip` / `_extract_tar` helpers
    - Missing `_MAX_UPLOAD_BYTES` cap on uploads

    **Cookies / sessions**
    - Missing `httponly`, `samesite="strict"`, or `secure=settings.secure_cookies`

    For each finding: state severity (CRITICAL / HIGH / MEDIUM / LOW), file and line,
    and the specific concern.  If no issues are found, say "No security issues found."

""")


def get_diff(mode: str) -> str:
    if mode == "staged":
        cmd = ["git", "diff", "--cached", "--unified=5"]
    elif mode == "branch":
        # Find the merge-base with main to get only branch-specific changes
        base = subprocess.run(
            ["git", "merge-base", "HEAD", "main"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        cmd = (
            ["git", "diff", base, "HEAD", "--unified=5"]
            if base
            else ["git", "diff", "HEAD~1", "--unified=5"]
        )
    else:
        cmd = ["git", "diff", "HEAD", "--unified=5"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout


def is_security_relevant(diff: str) -> bool:
    return any(path in diff for path in _SECURITY_PATHS)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="Review staged changes only")
    group.add_argument(
        "--branch", action="store_true", help="Review all changes on current branch vs main"
    )
    args = parser.parse_args()

    mode = "staged" if args.staged else ("branch" if args.branch else "head")
    diff = get_diff(mode)

    if not diff.strip():
        print("No changes to review.", file=sys.stderr)
        return 0

    if not is_security_relevant(diff):
        print(
            "No security-relevant files changed (routes, auth, models, services).",
            file=sys.stderr,
        )
        print("Diff touches only non-security paths — review skipped.", file=sys.stderr)
        return 0

    # Cap diff at ~40 000 chars to stay within a reasonable context size
    truncated = len(diff) > 40_000
    diff_body = diff[:40_000]

    prompt = _PROMPT_HEADER
    if truncated:
        prompt += "(Note: diff was truncated to 40 000 characters.)\n\n"
    prompt += "--- DIFF ---\n"
    prompt += diff_body
    prompt += "\n--- END DIFF ---\n"

    separator = "=" * 72
    print(separator)
    print("QUADLETMAN SECURITY REVIEW PROMPT")
    print("Paste the text below into a Claude Code chat in VS Code.")
    print(separator)
    print()
    print(prompt)
    print(separator)

    return 0


if __name__ == "__main__":
    sys.exit(main())
