# GitHub Pages Migration

The user approved GitHub Actions synchronization plus GitHub Pages hosting.

- [x] Replace Cloudflare deployment with the official configure-pages,
  upload-pages-artifact, and deploy-pages actions. Keep the existing schedule,
  transactional crawler, generated-file commits, and same-run deployment.
- [x] Replace Cloudflare-specific hosting headers with .nojekyll. Render the
  404 home link from the configured Pages URL so nested 404s and custom domains
  work. Keep the 25 MiB bound as a conservative crawler limit.
- [x] Update setup instructions to require Pages Source = GitHub Actions and
  no user-managed deployment secrets.
- [x] Verify tests, workflow syntax, a regenerated live snapshot, and browser
  behavior under the /3.jetbra.in/ prefix. Record remote deployment status.

## Results

- 17 pytest tests passed, including nested 404 recovery for project URLs and
  custom domains.
- Both workflow files passed actionlint 1.7.12. Official GitHub action manifests
  and the current static-site starter workflow were checked for versions,
  permissions, metadata outputs, and hidden-file handling.
- The live snapshot was regenerated and all 23 files passed verification.
- Playwright/Chrome at desktop and mobile sizes passed under an HTTP
  /3.jetbra.in/ prefix: 658 cards, copy success feedback, no external requests,
  resource errors, JavaScript errors, or horizontal overflow. The ZIP downloaded
  through that prefix matched the saved file byte for byte.
- Independent review found no migration issues.
- Remote deployment has not run. Changes remain local and uncommitted; after
  pushing, enable Settings > Pages > Source = GitHub Actions and run the workflow.
