import hashlib
import io
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

import pytest
from bs4 import BeautifulSoup

from scripts.mirror import MirrorError, REQUIRED_ZIP, mirror, verify_snapshot


def make_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "fixture archive, never executed")
    return buffer.getvalue()


@pytest.fixture
def upstream():
    files = {
        "/": ("text/html", b'''<!doctype html><html><head>
          <title>Some keys for testing - jetbra.in</title>
          <link rel="stylesheet" href="styles/main.css?v=1">
          <style>.icon { background: url('images/icon.svg?v=1') }</style>
          </head><body><main><article class="card" data-sequence="TEST">
          <button data-version="1">1</button></article></main>
          <a href="ZIP_PATH">Download</a>
          <a href="https://example.com/info">External navigation</a>
          <script src="scripts/app.js"></script>
          <script>const jbKeys = {TEST: {"1": "fixture"}};</script>
          <script src="https://static.cloudflareinsights.com/beacon.min.js"
          data-cf-beacon='{"token":"upstream"}'></script></body></html>'''
          .replace(b"ZIP_PATH", REQUIRED_ZIP.encode())),
        "/styles/main.css": ("text/css", b'@import "nested/other.css"; .x { background: url(../images/icon.svg?v=1) }'),
        "/styles/nested/other.css": ("text/css", b'.y { background: url("../../images/bg.png") }'),
        "/images/icon.svg": ("image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg"/>'),
        "/images/bg.png": ("image/png", b"fixture-image"),
        "/scripts/app.js": ("application/javascript", b"window.loaded = true;"),
        "/" + REQUIRED_ZIP: ("application/zip", make_zip()),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            item = files.get(self.path.split("?", 1)[0])
            if item is None:
                self.send_error(404)
                return
            if callable(item):
                item = item(self)
            mime, data, *options = item
            self.send_response(options[0] if options else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (options[1] if len(options) > 1 else {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/", files
    server.shutdown()
    server.server_close()
    thread.join()


def tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_complete_snapshot_is_local_and_verifiable(upstream, tmp_path):
    source, files = upstream
    output = tmp_path / "public"
    assert mirror(source, output) is True
    page = BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert not page.find("script", attrs={"data-cf-beacon": True})
    assert page.find("script", src=True)["src"] == "scripts/app.js"
    assert page.find("link")["href"] == "styles/main.css"
    assert page.find("a")["href"] == REQUIRED_ZIP
    assert page.find("a", href="https://example.com/info")
    assert (output / "styles/nested/other.css").is_file()
    assert (output / "images/bg.png").is_file()
    assert (output / REQUIRED_ZIP).read_bytes() == files["/" + REQUIRED_ZIP][1]
    manifest = verify_snapshot(output)
    entry = manifest["files"][REQUIRED_ZIP]
    assert entry["sha256"] == hashlib.sha256(files["/" + REQUIRED_ZIP][1]).hexdigest()
    assert (output / "404.html").is_file()
    assert (output / ".nojekyll").is_file()
    assert not (output / "_headers").exists()


@pytest.mark.parametrize("home_url", [
    "https://oiltea94.github.io/3.jetbra.in/",
    "https://mirror.example.com/",
])
def test_404_home_link_works_from_nested_missing_paths(upstream, tmp_path, home_url):
    source, _ = upstream
    output = tmp_path / "public"
    mirror(source, output, home_url=home_url)
    page = BeautifulSoup((output / "404.html").read_text(encoding="utf-8"), "html.parser")
    assert urljoin(home_url + "missing/deep/page", page.find("a")["href"]) == home_url
    verify_snapshot(output)


def test_unchanged_snapshot_preserves_all_bytes_and_timestamp(upstream, tmp_path):
    source, _ = upstream
    output = tmp_path / "public"
    mirror(source, output)
    original = tree(output)
    assert mirror(source, output) is False
    assert tree(output) == original


@pytest.mark.parametrize("failure", ["missing", "html_zip", "broken_zip", "challenge"])
def test_failed_fetch_preserves_previous_snapshot(upstream, tmp_path, failure):
    source, files = upstream
    output = tmp_path / "public"
    mirror(source, output)
    original = tree(output)
    if failure == "missing":
        del files["/images/bg.png"]
    elif failure == "html_zip":
        files["/" + REQUIRED_ZIP] = ("text/html", b"<html>error page</html>")
    elif failure == "broken_zip":
        files["/" + REQUIRED_ZIP] = ("application/zip", b"PKbroken")
    else:
        files["/"] = ("text/html", b"<html><title>Challenge</title></html>")
    with pytest.raises(MirrorError):
        mirror(source, output)
    assert tree(output) == original


def test_tampering_is_detected(upstream, tmp_path):
    source, _ = upstream
    output = tmp_path / "public"
    mirror(source, output)
    (output / REQUIRED_ZIP).write_bytes(b"tampered")
    with pytest.raises(MirrorError, match="[Hh]ash|[Cc]hecksum"):
        verify_snapshot(output)


def test_external_runtime_dependency_fails(upstream, tmp_path):
    source, files = upstream
    mime, html = files["/"]
    files["/"] = (mime, html.replace(b'src="scripts/app.js"', b'src="https://example.com/app.js"'))
    with pytest.raises(MirrorError, match="[Ee]xternal"):
        mirror(source, tmp_path / "public")


def test_encoded_traversal_is_rejected(upstream, tmp_path):
    source, files = upstream
    mime, html = files["/"]
    files["/"] = (mime, html.replace(b"scripts/app.js", b"/%2e%2e/outside.js"))
    with pytest.raises(MirrorError, match="[Uu]nsafe"):
        mirror(source, tmp_path / "public")
    assert not (tmp_path / "outside.js").exists()


def test_different_queries_cannot_silently_overwrite_same_path(upstream, tmp_path):
    source, files = upstream
    mime, html = files["/"]
    files["/"] = (mime, html.replace(b"</head>", b'<link rel="stylesheet" href="styles/main.css?v=2"></head>'))
    with pytest.raises(MirrorError, match="[Cc]ollision"):
        mirror(source, tmp_path / "public")


def test_unowned_output_directory_is_not_replaced(upstream, tmp_path):
    source, _ = upstream
    output = tmp_path / "important"
    output.mkdir()
    (output / "notes.txt").write_text("keep me")
    with pytest.raises(MirrorError, match="[Oo]wned|[Uu]nmanaged"):
        mirror(source, output)
    assert (output / "notes.txt").read_text() == "keep me"


def test_new_zip_is_mirrored_alongside_required_archive(upstream, tmp_path):
    source, files = upstream
    mime, html = files["/"]
    files["/"] = (mime, html.replace(REQUIRED_ZIP.encode(), b"files/new-release.zip"))
    files["/files/new-release.zip"] = ("application/zip", make_zip())
    output = tmp_path / "public"
    mirror(source, output)
    assert (output / "files/new-release.zip").is_file()
    assert (output / REQUIRED_ZIP).is_file()


def test_failed_publish_and_rollback_leave_recoverable_backup(upstream, tmp_path, monkeypatch):
    source, files = upstream
    output = tmp_path / "public"
    mirror(source, output)
    original = tree(output)
    files["/scripts/app.js"] = ("application/javascript", b"window.loaded = 'updated';")
    rename = Path.rename

    def fail_rename_to_output(path, target):
        if Path(target) == output:
            raise OSError("simulated directory lock")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_rename_to_output)
    with pytest.raises((MirrorError, OSError)):
        mirror(source, output)
    backups = list(tmp_path.glob(".mirror-backup-*"))
    assert len(backups) == 1, "A failed rollback must leave the old snapshot outside temporary cleanup"
    assert tree(backups[0]) == original


def test_failed_publish_restores_previous_snapshot(upstream, tmp_path, monkeypatch):
    source, files = upstream
    output = tmp_path / "public"
    mirror(source, output)
    original = tree(output)
    files["/scripts/app.js"] = ("application/javascript", b"window.loaded = 'updated';")
    rename = Path.rename

    def fail_publish(path, target):
        if path.parent.name.startswith(".mirror-stage-"):
            raise OSError("simulated failed publish")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="simulated"):
        mirror(source, output)
    assert tree(output) == original
    assert not list(tmp_path.glob(".mirror-backup-*"))


def test_unchanged_upstream_repairs_corrupted_local_snapshot(upstream, tmp_path):
    source, files = upstream
    output = tmp_path / "public"
    mirror(source, output)
    (output / REQUIRED_ZIP).write_bytes(b"corrupted locally")
    assert mirror(source, output) is True
    verify_snapshot(output)
    assert (output / REQUIRED_ZIP).read_bytes() == files["/" + REQUIRED_ZIP][1]


def test_download_uses_browser_compatible_request_profile(upstream, tmp_path):
    source, files = upstream
    original = files["/"]

    def browser_only(handler):
        if "Chrome/" in handler.headers.get("User-Agent", "") and handler.headers.get("Sec-Ch-Ua"):
            return original
        return "text/html", b"browser challenge", 403, {"cf-mitigated": "challenge"}

    files["/"] = browser_only
    assert mirror(source, tmp_path / "public") is True


def test_challenge_error_is_explicit_and_preserves_snapshot(upstream, tmp_path):
    source, files = upstream
    output = tmp_path / "public"
    mirror(source, output)
    original = tree(output)
    files["/"] = ("text/html", b"challenge", 403, {"cf-mitigated": "challenge"})
    with pytest.raises(MirrorError, match="Cloudflare.*challenge"):
        mirror(source, output)
    assert tree(output) == original


@pytest.mark.parametrize("status", [429, 503])
def test_transient_download_errors_retry_with_server_delay(upstream, tmp_path, monkeypatch, status):
    source, files = upstream
    original = files["/"]
    calls, sleeps = [], []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    def transient_error(handler):
        calls.append(handler.path)
        if len(calls) == 1:
            return "text/plain", b"retry later", status, {"Retry-After": "3"}
        return original

    files["/"] = transient_error
    assert mirror(source, tmp_path / "public") is True
    assert len(calls) == 2
    assert sleeps == [3]
