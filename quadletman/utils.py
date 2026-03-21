"""Pure utility functions with no project dependencies.

Functions here must not import from any other quadletman module to avoid
circular imports.  They are safe to use from config/, routers/, services/,
and models/.
"""


def fmt_bytes(b: int) -> str:
    """Format a byte count as a human-readable string (binary / 1024-based)."""
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"
