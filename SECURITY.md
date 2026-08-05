# Security policy

## Supported version

Security updates target the latest commit on `main`.

## Reporting a vulnerability

Do not include credentials, private addresses, backup metadata, or live service output in a public issue. Use GitHub's private vulnerability-reporting feature when available, or contact the repository owner privately through their GitHub profile.

## Deployment boundaries

PiRouter is a local operations console, not a hardened public web service. Run it only on a trusted LAN or authenticated tailnet. Keep `.env`, SQLite files, SSH keys, portable-export pairing tokens, and private operator notes out of Git.

Before publishing a change, run the production build and scan the complete tracked tree for secrets and personalized infrastructure values.
