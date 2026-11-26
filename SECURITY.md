# Security policy

TTROS Core is **alpha software**. Treat it as untrusted-network-facing code: run it locally or in an isolated VM and bind it to `127.0.0.1`.

## Reporting a vulnerability
Please **do not open a public issue or pull request** for a vulnerability. Report it privately through GitHub:
**Security tab → "Report a vulnerability"** on this repository (private vulnerability reporting).
Include affected files or endpoints, steps to reproduce, impact, and any suggested fix. Do not include real credentials or customer data.

We aim to acknowledge reports within 5 working days and to agree a disclosure timeline with you. This is an alpha project
maintained by a small team, so these are targets rather than guarantees. There is no bug bounty.

## Scope
In scope: this repository's code, including permission and scope checks, Business Brain scoping, Connections handling,
the dashboard backend and the contribution gate. Out of scope: third-party services, your own deployment's hosting, and
social-engineering of maintainers.

## If you find a secret or private data
If a credential, private record or personal data appears in the repository or its history, report it privately at once.
Do not copy or redistribute it. Maintainers will rotate the credential and remove the content.

## Handling untrusted code
Never run a contributor's branch on a machine with real credentials or a real Business Brain. See `CONTRIBUTING.md`.
