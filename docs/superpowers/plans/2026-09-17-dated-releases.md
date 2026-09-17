# Dated Snapshot Releases

The requested package is the entire verified public directory, not just the
upstream ZIP. The six-digit date immediately following the page's ZIP link is
the GitHub Release title and tag; the attached archive is named DATE.zip.

- [x] Add focused tests for date extraction, complete ZIP contents, deterministic
  packaging, and rejection of invalid snapshots or output inside public.
- [x] Implement `scripts/package_release.py` using the existing manifest verifier,
  Beautiful Soup, and Python zipfile. Missing or ambiguous dates fail explicitly.
- [x] Add release lifecycle tests and publishing through gh. Create a draft,
  upload the package, then publish. Published dates retain their first snapshot;
  interrupted managed drafts can be resumed without replacing published assets.
- [x] Add a separate release job after sync, checking out the exact snapshot
  commit. Keep Pages deployment independent of release publication failures.
- [x] Run tests, inspect a real package, lint the workflow, review, and push.
  Verify an actual GitHub release and a repeated run for the same date.

Validation: 47 tests and actionlint passed. Actions run `35193331435` synced,
deployed, and published `260914`. The downloaded `260914.zip` contains all 24
snapshot files with identical bytes. Repeating publication returned `existing`
and preserved asset ID `569687695` and its SHA-256 digest.
