# Static Mirror Implementation Plan

This records the initial implementation. Hosting has since been migrated to
GitHub Pages; see `2026-09-17-github-pages.md` for the current deployment plan.

**Goal:** Produce a current, locally usable static snapshot and an automated
GitHub-to-Cloudflare Pages synchronization workflow.

**Architecture:** One targeted Python crawler owns the staged build and manifest.
Static hosting templates live separately from generated output. CI tests use a
local HTTP fixture and never depend on the upstream site's availability.

**Tech Stack:** Python 3.10+, requests, Beautiful Soup, tinycss2, pytest,
GitHub Actions, Wrangler 4.

## Tasks

- [x] Add local HTTP integration tests in `tests/test_mirror.py` for a page with
  CSS imports, images, a script, analytics, and the required ZIP. Run
  `python -m pytest -q` and confirm the missing implementation causes failure.
- [x] Implement `scripts/mirror.py`, `requirements.txt`, and hosting templates
  under `static/`. Run the integration tests, including rollback and idempotence.
- [x] Add `.github/workflows/check.yml` for tests and
  `.github/workflows/sync.yml` for sync, generated-file commits, and optional
  Cloudflare deployment. Document secrets, initial project creation, branch
  permissions, schedule behavior, and commands in `README.md`.
- [x] Run `python scripts/mirror.py` against the live source, then
  `python scripts/mirror.py --verify-only` and repeat the sync to check stability.
  Validate workflow syntax and inspect the page in a browser when available.
- [x] Review all changes and update these task statuses with observed results.

## Verification Results

- Local pytest suite: 15 passed. Covers complete traversal, unchanged snapshots,
  broken downloads/archives, path validation, query collisions, required archive
  retention, failed replacement, failed rollback, and local corruption repair.
- Live capture: 23 files verified by manifest checksums and ZIP CRC validation.
  A second live capture reported `Snapshot unchanged.`
- Actionlint 1.7.12: both workflows pass. YAML parsing also passes.
- Playwright with installed Chrome: desktop 1440x1000 and mobile 390x844 both
  render 658 cards with copy success feedback, no JavaScript errors, failed
  requests, external requests, or horizontal overflow. Screenshots inspected.
- Independent review: corrected backup lifetime on rollback failure and inclusion
  of hidden public assets in deployment artifacts. Follow-up found no more issues.
- GitHub Actions and Cloudflare deployment have not run remotely. The repository
  has not been committed or pushed; project name and account secrets are configured
  by the owner using the README instructions.
