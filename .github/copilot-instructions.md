# GitHub Copilot Instructions

See [CLAUDE.md](../CLAUDE.md) for full context. Key points for inline completions:

- Python 3.12+, FastAPI, async/await throughout
- 100-char line limit, double quotes, space indentation (enforced by ruff)
- All imports at the top of the file, sorted (stdlib → third-party → first-party)
- Use `contextlib.suppress()` instead of `try/except/pass`
- Use `with open(path) as f:` — never bare `open(path).read()`
- Chain exceptions: `raise HTTPException(...) from exc`
- Routes return HTMX template partials or JSON depending on `HX-Request` header
- Service operations go through `compartment_manager.py`, not lower-level modules directly
- Before committing, check the Doc Update Protocol in CLAUDE.md and update affected docs in the same commit
