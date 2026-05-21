# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest on `main` | Yes |
| Older releases | No |

## Reporting a Vulnerability

If you discover a security vulnerability in Laya, please report it responsibly.
**Do not open a public GitHub issue.**

### How to Report

Email **aayush@live.in** with:

- A description of the vulnerability
- Steps to reproduce
- Affected component (Engine, UI, Tauri, n8n workflows)
- Impact assessment (what an attacker could do)
- Any suggested fix, if you have one

### What to Expect

- **Acknowledgment** within 48 hours of your report
- **Status update** within 7 days with an assessment and timeline
- **Credit** in the release notes (unless you prefer to remain anonymous)

### Scope

The following are in scope:

- Laya Engine (Python backend) — API endpoints, authentication, data handling
- Laya UI (Svelte frontend) — XSS, injection, data exposure
- Tauri Shell (Rust) — IPC, sidecar management, native API misuse
- n8n Workflows — credential exposure, webhook security
- Credential storage — keychain integration, API key handling
- SQLite / ChromaDB — data access controls, injection

The following are out of scope:

- Vulnerabilities in upstream dependencies with no demonstrated exploit in Laya
- Self-hosted n8n instance configuration (user responsibility)
- Denial of service against local-only services
- Social engineering

## Security Design

Laya is a **local-first** application. Key security properties:

- **Credentials** are stored in the OS keychain, never in plaintext config files
- **LLM API keys** are per-space and stored via the system keychain
- **No cloud sync** — all data stays in `~/.laya/data/` on the user's machine
- **OAuth tokens** for connected platforms are managed through the connection
  broker with proper token refresh flows

## Disclosure Policy

We follow coordinated disclosure. We ask that you give us reasonable time to
address the issue before any public disclosure. We aim to release fixes within
30 days of a confirmed vulnerability.
