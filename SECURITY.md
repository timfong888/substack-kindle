# Security & Secrets Policy

This repository is **public**. The policy below is non-negotiable.

## No secrets in the repo — ever

The following are NEVER committed:

- Gmail OAuth client secret and tokens
- Postmark (transactional email / `whitelist_email`) credentials
- Any per-customer config values (Kindle addresses, recipient emails, tokens)
- Any API key, password, or private key of any kind

All secrets are supplied **at runtime** via environment variables or a secrets
manager. Claude Managed Agents provides credential management — use it. Do not
hardcode. Non-secret config (e.g. `.coderabbit.yaml`) MAY be committed. Code review
runs via the CodeRabbit GitHub App, so no review API key lives in the repo or env.

## Enforcement

- **`.gitignore`** covers `.env*` and common credential file patterns.
- **GitHub secret scanning + push protection** are enabled — an accidental secret
  commit is blocked at push.
- **Pre-commit hook (gitleaks)** is a local gate before push. Install with
  `pre-commit install`.

## If a secret is exposed

1. Revoke/rotate the credential immediately at its source.
2. Remove it from history (it is already public the moment it is pushed).
3. Treat the leaked value as compromised regardless of cleanup.

## Reporting

Open a private security advisory on this repo, or contact the owner directly.
Do not file a public issue for a vulnerability.
