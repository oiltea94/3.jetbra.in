# Static Mirror Design

Deployment was subsequently changed to GitHub Pages by user request. See
`../plans/2026-09-17-github-pages.md` for the current hosting design. The original
Cloudflare-specific deployment and header choices below are superseded.

The approved approach is a complete snapshot of https://3.jetbra.in/ served by
Cloudflare Pages, including the explicitly requested ZIP.

A Python command parses HTML with Beautiful Soup and CSS with tinycss2, downloads
same-origin dependencies, rewrites their URLs to local relative paths, removes
the upstream Cloudflare analytics script, and preserves page behavior. CSS imports
and nested URLs are traversed. External navigation links remain navigation links;
unexpected external runtime dependencies fail the build for inspection. This is
a targeted static mirror, not a general JavaScript application crawler.

Every build starts in a sibling temporary directory. HTTP failures, invalid ZIPs,
unsafe paths, missing expected page markers, or files exceeding the Pages 25 MiB
limit prevent publication. A validated build replaces only a mirror-owned output
directory, with rollback if replacement fails. A manifest records source URLs,
stored bytes, SHA-256 hashes, and the last changed snapshot time. Unchanged content
does not change the manifest or Git history.

GitHub Actions tests pull requests without secrets. The main-branch synchronization
workflow runs on pushes, daily at 00:00 UTC, and manual dispatch. It validates a new
snapshot, commits changed generated files, and optionally deploys the exact output
to a pre-created Cloudflare Pages Direct Upload project. Deployment is enabled by
the CLOUDFLARE_PAGES_PROJECT repository variable; its credentials are secrets.
Failed uploads can be retried on any subsequent workflow run, even without changes.
Actions never force-push. The workflow does not create Cloudflare resources.

Verification covers recursive resources, relative links, duplicate URL handling,
ZIP integrity, failed-build rollback, stable unchanged snapshots, and deployment
configuration. A live snapshot and browser smoke test verify the actual upstream.
