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
- [ ] Run tests, inspect a real package, lint the workflow, review, and push.
  Verify an actual GitHub release and a repeated run for the same date.
