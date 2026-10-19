# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | Yes |

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Use GitHub's private vulnerability reporting on this repository:
**Security tab > Report a vulnerability** (or open a draft security advisory).
If that is unavailable, contact the repository owner through their GitHub profile.

Include:

- A description of the issue and its impact.
- Steps to reproduce (a proof of concept is ideal).
- Affected version or commit.
- Any suggested fix.

You can expect an acknowledgement within 7 days. Please allow time for a fix and release
before disclosing publicly.

## Token handling

GuildForge is designed so tokens are hard to leak:

- Tokens are read from `.env`, the environment, or hidden input.
- Tokens are never printed, logged, or written to disk by GuildForge.
- Log output is passed through a redaction layer that removes registered secrets.
- `.env` files, backup files, and the resume state file are in `.gitignore`.

If you believe a token can leak through a code path, treat it as a high-severity report.

## Out of scope

- Issues caused by sharing your own token in public (reset the token instead).
- False-positive antivirus detections for PyInstaller builds (see README FAQ).
- Discord-side rate limits, outages, or policy decisions.
