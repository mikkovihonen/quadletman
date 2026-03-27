# Security Policy

## Reporting a Vulnerability

This is a hobby project maintained by a single person.
If you discover a security vulnerability in quadletman, you may report it, but there is no guarantee of it being fixed.

**Do not open a public GitHub issue for security vulnerabilities.**

Please use
[GitHub's private vulnerability reporting](https://github.com/mikkovihonen/quadletman/security/advisories/new).

Include:
- A description of the vulnerability
- Steps to reproduce
- The potential impact
- Any suggested fix (optional)

## Supported Versions

This is a side project maintained by a single developer. There are no official supported versions.
See also [LICENSE](https://github.com/mikkovihonen/quadletman/LICENSE).

## Security Model

quadletman runs as a dedicated `quadletman` system user (or root for legacy
installations) and manages Podman containers via per-compartment Linux users.
Admin operations escalate via the authenticated user's sudo credentials.

Key security controls:
- PAM-based authentication restricted to sudo/wheel group members
- Branded-type input validation at every layer boundary
- Session credentials stored in the Linux kernel keyring (when available)
- CSRF protection via double-submit cookie
- CSP headers blocking all external resource loading
- All host mutations routed through audited wrappers
