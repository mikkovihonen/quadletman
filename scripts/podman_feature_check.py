#!/usr/bin/env python3
"""Check for new Podman releases with Quadlet-relevant changes.

Fetches recent Podman releases from GitHub, diffs the Quadlet man page
between releases to find new/removed unit-file keys, and filters release
notes for Quadlet-relevant entries.

Output is GitHub-flavored markdown suitable for a GitHub issue body.

Requires no external dependencies (Python stdlib only).

Usage:
    # Check latest release vs previous
    python scripts/podman_feature_check.py

    # Check a specific release
    python scripts/podman_feature_check.py --tag v5.4.0

    # Compare two specific versions
    python scripts/podman_feature_check.py --tag v5.4.0 --previous v5.3.0

    # JSON output (for CI scripting)
    python scripts/podman_feature_check.py --output json
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

PODMAN_REPO = "containers/podman"
MANPAGE_PATH = "docs/source/markdown/podman-systemd.unit.5.md"

# Keywords signalling Quadlet relevance in release notes
QUADLET_KEYWORDS = re.compile(
    r"quadlet|\.container|\.image|\.network|\.volume|\.pod|\.build|\.kube|"
    r"unit\s*file|systemd.*generator|podman.*systemd",
    re.IGNORECASE,
)

# Section header in the man page: ## Foo units [Bar] or ## Foo [.bar]
SECTION_RE = re.compile(
    r"^##\s+[^#\n]*?\[\.?(\w+)\]",
    re.MULTILINE,
)

# Key definitions — multiple formats seen across Podman versions
KEY_PATTERNS = [
    re.compile(r"^#{2,4}\s+`(\w+=)`", re.MULTILINE),  # ### `Key=`
    re.compile(r"^\*\*(\w+=)\*\*", re.MULTILINE),  # **Key=**
    re.compile(r"^\|\s*\*\*(\w+=)\*\*", re.MULTILINE),  # | **Key=** (table)
    re.compile(r"^#{2,4}\s+\*\*(\w+=)\*\*", re.MULTILINE),  # ### **Key=**
]

# Feature flag line in podman.py
FLAG_RE = re.compile(r"^\s+(\w+):\s*bool\s*#\s*(.+)$")


def _github_request(url: str, *, accept: str = "application/vnd.github+json") -> str:
    """Fetch a URL with optional GitHub token auth."""
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "quadletman-podman-watch")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def github_api(path: str) -> dict | list:
    """GET from GitHub REST API."""
    url = f"https://api.github.com/{path}"
    return json.loads(_github_request(url))


def github_raw(repo: str, ref: str, path: str) -> str | None:
    """Fetch a raw file from GitHub. Returns None on 404."""
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    try:
        return _github_request(url, accept="text/plain")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def get_releases(count: int = 20) -> list[dict]:
    """Fetch recent non-draft, non-prerelease Podman releases."""
    releases = github_api(f"repos/{PODMAN_REPO}/releases?per_page={count}")
    return [r for r in releases if not r.get("draft") and not r.get("prerelease")]


def parse_manpage_keys(content: str) -> dict[str, set[str]]:
    """Parse Quadlet man page into {unit_type: {key, ...}}.

    Returns a dict mapping unit type (e.g. "container", "volume") to
    a set of key names (e.g. {"AddCapability=", "AutoUpdate="}).
    Keys found before the first section header go into "global".
    """
    # Find all section boundaries
    sections: list[tuple[str, int]] = []
    for m in SECTION_RE.finditer(content):
        unit_type = m.group(1).lower()
        sections.append((unit_type, m.start()))

    if not sections:
        sections = [("unknown", 0)]

    result: dict[str, set[str]] = {}

    for i, (unit_type, start) in enumerate(sections):
        end = sections[i + 1][1] if i + 1 < len(sections) else len(content)
        chunk = content[start:end]

        keys: set[str] = set()
        for pattern in KEY_PATTERNS:
            keys.update(pattern.findall(chunk))

        if keys:
            result[unit_type] = keys

    return result


def filter_release_notes(body: str | None) -> list[str]:
    """Extract Quadlet-relevant lines from release notes."""
    if not body:
        return []
    results: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and QUADLET_KEYWORDS.search(stripped):
            # Strip leading list markers (- , * , digits.) to avoid double-dash
            cleaned = re.sub(r"^[-*]\s+|^\d+\.\s+", "", stripped)
            results.append(cleaned)
    return results


def read_local_features() -> dict[str, str]:
    """Read current feature flags from local podman.py.

    Returns {flag_name: comment} dict.
    """
    version_file = Path(__file__).resolve().parent.parent / "quadletman" / "podman.py"
    if not version_file.exists():
        return {}

    features: dict[str, str] = {}
    for line in version_file.read_text().splitlines():
        m = FLAG_RE.match(line)
        if m:
            features[m.group(1)] = m.group(2).strip()
    return features


def diff_keys(
    latest: dict[str, set[str]], previous: dict[str, set[str]]
) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Diff two parsed key dicts.

    Returns (new_keys, removed_keys, new_unit_types).
    """
    all_sections = set(latest) | set(previous)
    new_keys: dict[str, set[str]] = {}
    removed_keys: dict[str, set[str]] = {}
    new_unit_types: set[str] = set(latest) - set(previous)

    for section in all_sections:
        cur = latest.get(section, set())
        prev = previous.get(section, set())
        added = cur - prev
        removed = prev - cur
        if added:
            new_keys[section] = added
        if removed:
            removed_keys[section] = removed

    return new_keys, removed_keys, new_unit_types


def build_report(
    *,
    tag: str,
    prev_tag: str | None,
    release_url: str,
    relevant_notes: list[str],
    new_keys: dict[str, set[str]],
    removed_keys: dict[str, set[str]],
    new_unit_types: set[str],
    current_features: dict[str, str],
    manpage_available: bool,
) -> str:
    """Build markdown report for a GitHub issue."""
    lines: list[str] = []

    lines.append(f"A new Podman release [{tag}]({release_url}) is available.")
    if prev_tag:
        lines.append(f"Compared against previous release: {prev_tag}.")
    lines.append("")

    # New unit types
    if new_unit_types:
        lines.append("## New unit types\n")
        for ut in sorted(new_unit_types):
            lines.append(f"- [ ] `.{ut}` — **new unit type** — evaluate for quadletman support")
        lines.append("")

    # Quadlet-relevant release notes
    if relevant_notes:
        lines.append("## Quadlet-relevant release notes\n")
        for note in relevant_notes:
            lines.append(f"- {note}")
        lines.append("")

    # New Quadlet keys
    has_new = any(new_keys.values())
    if has_new:
        lines.append("## New Quadlet keys\n")
        for section in sorted(new_keys):
            keys = new_keys[section]
            if keys:
                lines.append(f"### .{section}\n")
                for key in sorted(keys):
                    lines.append(f"- [ ] `{key}` — evaluate for quadletman support")
                lines.append("")

    # Removed keys
    has_removed = any(removed_keys.values())
    if has_removed:
        lines.append("## Removed Quadlet keys\n")
        for section in sorted(removed_keys):
            keys = removed_keys[section]
            if keys:
                lines.append(f"### .{section}\n")
                for key in sorted(keys):
                    lines.append(f"- `{key}`")
                lines.append("")

    if not relevant_notes and not has_new and not has_removed and not new_unit_types:
        if manpage_available:
            lines.append("No Quadlet-relevant changes detected in this release.\n")
        else:
            lines.append(
                "Could not fetch the Quadlet man page for diff — "
                f"review the [release notes]({release_url}) manually.\n"
            )

    # Current coverage
    if current_features:
        lines.append("## Current quadletman feature coverage\n")
        lines.append("| Flag | Description |")
        lines.append("|------|-------------|")
        for flag, desc in sorted(current_features.items()):
            lines.append(f"| `{flag}` | {desc} |")
        lines.append("")

    # Action items
    lines.append("## Action items\n")
    lines.append("- [ ] Review new keys and release notes for features to support")
    lines.append("- [ ] Add feature flags to `podman.py` if needed")
    lines.append("- [ ] Update `quadlet_writer.py` templates for new keys")
    lines.append("- [ ] Add version gates (UI + route + test) per CLAUDE.md")
    lines.append("- [ ] Update documentation")
    lines.append("")

    return "\n".join(lines)


def find_previous_tag(releases: list[dict], target_tag: str) -> str | None:
    """Find the release immediately before target_tag in the list."""
    found = False
    for r in releases:
        if found:
            return r["tag_name"]
        if r["tag_name"] == target_tag:
            found = True
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check for new Podman releases with Quadlet-relevant changes",
    )
    parser.add_argument(
        "--tag",
        help="Podman release tag to check (default: latest)",
    )
    parser.add_argument(
        "--previous",
        help="Previous tag to diff against (default: auto-detect)",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    args = parser.parse_args()

    try:
        releases = get_releases()
    except (URLError, OSError) as exc:
        print(f"ERROR: Could not fetch Podman releases: {exc}", file=sys.stderr)
        return 1

    if not releases:
        print("ERROR: No stable Podman releases found", file=sys.stderr)
        return 1

    # Determine target release
    if args.tag:
        target = next((r for r in releases if r["tag_name"] == args.tag), None)
        if not target:
            try:
                target = github_api(f"repos/{PODMAN_REPO}/releases/tags/{args.tag}")
            except (URLError, OSError) as exc:
                print(f"ERROR: Could not fetch release {args.tag}: {exc}", file=sys.stderr)
                return 1
    else:
        target = releases[0]

    tag = target["tag_name"]
    release_url = target["html_url"]

    # Determine previous tag
    prev_tag = args.previous if args.previous else find_previous_tag(releases, tag)

    print(f"Checking {tag} against {prev_tag or '(none)'}...", file=sys.stderr)

    # Fetch man pages
    latest_manpage = github_raw(PODMAN_REPO, tag, MANPAGE_PATH)
    prev_manpage = github_raw(PODMAN_REPO, prev_tag, MANPAGE_PATH) if prev_tag else None

    if latest_manpage:
        print(f"Fetched man page for {tag}", file=sys.stderr)
    else:
        print(f"Man page not found for {tag}", file=sys.stderr)

    # Parse and diff keys
    latest_keys = parse_manpage_keys(latest_manpage) if latest_manpage else {}
    prev_keys = parse_manpage_keys(prev_manpage) if prev_manpage else {}
    new_keys, removed_keys, new_unit_types = diff_keys(latest_keys, prev_keys)

    # Filter release notes
    relevant_notes = filter_release_notes(target.get("body"))

    # Read local feature flags
    current_features = read_local_features()

    if args.output == "json":
        result = {
            "tag": tag,
            "previous_tag": prev_tag,
            "release_url": release_url,
            "new_keys": {s: sorted(k) for s, k in new_keys.items()},
            "removed_keys": {s: sorted(k) for s, k in removed_keys.items()},
            "new_unit_types": sorted(new_unit_types),
            "relevant_notes": relevant_notes,
            "current_features": current_features,
        }
        print(json.dumps(result, indent=2))
    else:
        report = build_report(
            tag=tag,
            prev_tag=prev_tag,
            release_url=release_url,
            relevant_notes=relevant_notes,
            new_keys=new_keys,
            removed_keys=removed_keys,
            new_unit_types=new_unit_types,
            current_features=current_features,
            manpage_available=latest_manpage is not None,
        )
        print(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
