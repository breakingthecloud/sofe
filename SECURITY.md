# SOFE Security Checklist

Formal security checklist for anyone who deploys or operates SOFE (self-hosted
or the hosted SaaS). Built from a real audit of the SOFE stack (2026-09).

Severity legend: 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Good practice

---

## 1. Repositories & Supply Chain

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 1.1 | No secrets committed: `AKIA*`, `sk_*`, `AIzaSy*`, private keys, `.env`, `.dev.vars`, `wrangler.toml` `[vars]` with credentials | 🔴 | |
| 1.2 | No secrets in git history (`git log -p` / `trufflehog` / `gitleaks`) | 🔴 | |
| 1.3 | `build/`, `.terraform/`, `*.tfstate*`, `lambda.zip`, `node_modules/`, `.wrangler/`, `.DS_Store` are gitignored and NOT tracked | 🟡 | |
| 1.4 | CI workflows use least-privilege `permissions:` and no plaintext secrets | 🟠 | |
| 1.5 | Release artifacts (PyPI, GoReleaser, Docker) publish `checksums.txt` / SBOM | 🟢 | |
| 1.6 | Install scripts verify checksum/signature instead of bare `curl | bash` | 🟠 | |
| 1.7 | Only necessary collaborators with admin access on public repos | 🟡 | |

## 2. Authentication

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 2.1 | Bearer tokens (Firebase ID tokens / JWTs) are **signature-verified** (JWKS) with `iss`/`aud`/`exp` checks — never just base64-decoded | 🔴 | |
| 2.2 | API keys stored as SHA-256 hashes, not plaintext (even in a field named `keyHash`) | 🔴 | |
| 2.3 | Internal secrets (Lambda ↔ Worker) use a shared secret header and the backend rejects requests without it | 🔴 | |
| 2.4 | Device-code flow completes on the backend; browser never writes `device_codes`/`api_keys` via unauthenticated REST | 🔴 | |
| 2.5 | Admin-only endpoints check role via a trusted path (backend/service account), not client-supplied claims | 🟠 | |
| 2.6 | Rate limiting applied at every public entry point (incl. raw backend URLs, not just the edge) | 🟠 | |

## 3. Data Store (Firestore / DB)

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 3.1 | Security rules require `request.auth != null` — no anonymous reads/writes with only a public API key | 🔴 | |
| 3.2 | Owner-scoped collections (`users/{uid}/**`, `connectedAccounts`, `evaluations`) only readable/writable by owner or backend | 🔴 | |
| 3.3 | `api_keys` and `device_codes` (post-approval) never readable by other users | 🔴 | |
| 3.4 | Backend Workers authenticate with a service account (OAuth2 token), never the web API key | 🔴 | |
| 3.5 | Sensitive user fields (third-party AI API keys, external IDs) encrypted at rest | 🟠 | |
| 3.6 | Rules have a default-deny catch-all; `get()`/`exists()` used only for trusted checks | 🟢 | |

## 4. Compute (Lambda / Workers / Pages)

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 4.1 | No publicly reachable backend URL without auth (API Gateway catch-all `$default` + unauthenticated Lambda = 🔴) | 🔴 | |
| 4.2 | Lambda/IAM role follows least privilege: no `sts:AssumeRole Resource="*"`, no `List*`/`Describe*` beyond what's needed | 🔴 | |
| 4.3 | Cross-account endpoints (`/connect/test`, Bedrock proxy) validate ARN format and require an external ID | 🟠 | |
| 4.4 | Workers reject requests to internal endpoints unless a valid `x-sofe-internal-secret` is present | 🟠 | |
| 4.5 | `reload=True` and dev flags disabled in production | 🟡 | |
| 4.6 | WAF / throttling on public endpoints; function URLs not exposed | 🟢 | |

## 5. Secrets Management

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 5.1 | All credentials stored as Cloudflare secrets / AWS SSM / env — never in committed `[vars]`/`wrangler.toml` | 🔴 | |
| 5.2 | Rotation plan: API keys, external IDs, cron secrets, web API keys on any exposure | 🔴 | |
| 5.3 | `.dev.vars.example` committed instead of `.dev.vars` | 🟢 | |
| 5.4 | Service accounts scoped to one project with `datastore` (or narrower) access | 🟢 | |
| 5.5 | **Follow-up**: migrate Workers to Workload Identity Federation (OIDC) to avoid long-lived service-account keys (Google-recommended) | 🟡 | |

## 6. Client / CLI

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 6.1 | CLI config file (`~/.sofe/config.yaml`) written `0600` and API keys stored securely (or OS keychain) | 🟡 | |
| 6.2 | Remote templates/fixtures fetched over HTTPS and validated (no `http.Get` of untrusted URLs) | 🟠 | |
| 6.3 | CLI updater verifies artifact checksum | 🟠 | |

## 7. Documentation & Disclosure

| # | Check | Sev | Status |
|---|-------|-----|--------|
| 7.1 | No real credentials, internal URLs, account IDs, or external IDs in docs/README/blog | 🟠 | |
| 7.2 | `SECURITY.md` with a private disclosure contact and response expectations | 🟢 | |
| 7.3 | Public-facing blog posts about audits are anonymized (no real endpoints/secrets/account IDs) | 🟡 | |

---

## Remediation priority (if you're starting from 🔴)

1. **Firestore rules** → require auth; owner-scoped collections; default deny.
2. **Rotate** every exposed credential (API keys, external IDs, web API key, cron secret).
3. **Workers** → verify JWT signatures, hash API keys, use a service account for Firestore.
4. **Lambda** → require the internal secret, restrict `sts:AssumeRole`, remove the public catch-all.
5. **Supply chain** → checksummed installs, gitignore hygiene, no secrets in git history.