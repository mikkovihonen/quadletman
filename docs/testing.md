# Testing

## Unit and integration tests

The Python test suite lives under `tests/` and is run with pytest. It must **not** run as root.

```bash
uv run pytest                        # all unit + router tests
uv run pytest tests/routers/         # router tests only
uv run pytest tests/services/        # service-layer tests only
uv run pytest tests/e2e              # Playwright browser tests (needs a live server)
npm test                             # JavaScript unit tests (Node 20+ required)
```

Key rules:
- Every test that would invoke `subprocess.run`, `os.chown`, `pwd.getpwnam`, or similar
  system APIs **must mock those calls**. Tests must not create Linux users, touch
  `/var/lib/`, call `systemctl`, or write outside `/tmp`.
- JS tests load source files via `window.eval` in jsdom — no source changes needed.
  DOM-heavy code is covered by E2E tests.

---

## Smoke-test VMs (Fedora + Ubuntu)

Vagrant VMs build and install real packages on clean systems. Two VMs are available:

- **fedora** — RPM package on Fedora 41 with SELinux enforcing (`vagrant up fedora`)
- **ubuntu** — DEB package on Ubuntu 24.04 (`vagrant up ubuntu`)

See **[docs/packaging.md — Smoke testing](packaging.md#smoke-testing)** for the full
guide: prerequisites per OS, first-time setup, running/re-testing, inspecting the VMs,
and what the smoke tests verify.