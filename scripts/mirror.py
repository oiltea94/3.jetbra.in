"""Build a self-contained, validated snapshot without executing upstream code."""

import argparse
import hashlib
import io
import json
import posixpath
import shutil
import sys
import tempfile
import time
import zipfile
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import tinycss2
from bs4 import BeautifulSoup
from curl_cffi import requests
from tinycss2.serializer import serialize_string_value


SOURCE = "https://3.jetbra.in/"
REQUIRED_ZIP = "files/jetbra-5a50fc03d68a014f893b7fc3aa465380d59f9095.zip"
GENERATOR = "jetbra-static-mirror"
MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_FILES = 1000
TEMPLATES = Path(__file__).resolve().parents[1] / "static"
RESERVED = {"snapshot.json", ".nojekyll", "_headers", "_redirects", "_worker.js", "404.html"}


class MirrorError(RuntimeError):
    pass


def safe_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(part in {".", ".."} or part.endswith((".", " "))
                   or any(c in part for c in '<>:"\\|?*\x00')
                   for part in path.parts)):
        raise MirrorError(f"Unsafe asset path: {value!r}")
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_zip(data, name):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if not archive.infolist():
                raise MirrorError(f"Empty ZIP: {name}")
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise MirrorError(f"ZIP exceeds 100 MiB expanded size: {name}")
            if archive.testzip() is not None:
                raise MirrorError(f"ZIP CRC mismatch: {name}")
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise MirrorError(f"Invalid ZIP {name}: {exc}") from exc


def read_manifest(output):
    try:
        manifest = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
        if manifest["generator"] != GENERATOR or manifest["schema_version"] != 1:
            raise ValueError("unknown generator or schema")
        if not isinstance(manifest["files"], dict):
            raise ValueError("invalid file manifest")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MirrorError(f"Unmanaged output directory or invalid manifest: {output}") from exc


def verify_snapshot(output):
    output = Path(output).resolve()
    manifest = read_manifest(output)
    required = {"index.html", REQUIRED_ZIP, ".nojekyll", "404.html"}
    if not required.issubset(manifest["files"]):
        raise MirrorError("Snapshot is missing required files")
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"snapshot.json"}:
        raise MirrorError("Snapshot file inventory does not match manifest")
    for name, entry in manifest["files"].items():
        path = output / safe_path(name)
        if not path.resolve().is_relative_to(output) or path.is_symlink():
            raise MirrorError(f"Unsafe snapshot path: {name}")
        data = path.read_bytes()
        if digest(data) != entry["sha256"] or len(data) != entry["bytes"]:
            raise MirrorError(f"Checksum mismatch: {name}")
        if len(data) > MAX_FILE_SIZE:
            raise MirrorError(f"File exceeds crawler 25 MiB limit: {name}")
        if name.endswith(".zip"):
            check_zip(data, name)
    return manifest


class Crawler:
    def __init__(self, source, stage, home_url="./"):
        parsed = urlsplit(source)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise MirrorError("Source must be an HTTP(S) origin, such as https://3.jetbra.in/")
        self.source = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        self.origin = (parsed.scheme, parsed.netloc)
        self.stage = stage
        self.home_url = home_url
        self.queue = deque()
        self.urls = {}
        self.files = {}
        self.session = requests.Session(impersonate="chrome")

    def reference(self, value, base, owner, navigation=False):
        value = value.strip()
        if not value or value.startswith(("#", "data:", "mailto:", "tel:")):
            return value
        absolute, fragment = urldefrag(urljoin(base, value))
        parsed = urlsplit(absolute)
        if (parsed.scheme, parsed.netloc) != self.origin:
            if navigation:
                return value
            raise MirrorError(f"External runtime dependency requires review: {absolute}")
        name = unquote(parsed.path).lstrip("/") or "index.html"
        if parsed.path.endswith("/") and name != "index.html":
            name += "/index.html"
        safe_path(name)
        if name in RESERVED:
            raise MirrorError(f"Unsafe reserved asset path: {name}")
        if name in self.urls and self.urls[name] != absolute:
            raise MirrorError(f"URL collision at {name}: {self.urls[name]} and {absolute}")
        if name not in self.urls:
            if len(self.urls) >= MAX_FILES:
                raise MirrorError(f"Snapshot exceeds {MAX_FILES} files")
            self.urls[name] = absolute
            self.queue.append((absolute, name))
        local = quote(posixpath.relpath(name, posixpath.dirname(owner) or "."), safe="/.-_")
        return local + ("#" + fragment if fragment else "")

    def css(self, text, base, owner):
        tokens = tinycss2.parse_component_value_list(text)

        def update_string(token):
            token.value = self.reference(token.value, base, owner)
            token.representation = '"' + serialize_string_value(token.value) + '"'

        def walk(items):
            importing = False
            for token in items:
                if token.type == "error":
                    raise MirrorError(f"Invalid CSS in {owner}: {token.message}")
                if token.type in {"whitespace", "comment"}:
                    continue
                if importing and token.type == "string":
                    update_string(token)
                importing = token.type == "at-keyword" and token.lower_value == "import"
                if token.type == "url":
                    token.value = self.reference(token.value, base, owner)
                    token.representation = 'url("' + serialize_string_value(token.value) + '")'
                elif token.type == "function" and token.lower_name == "url":
                    args = [t for t in token.arguments if t.type not in {"whitespace", "comment"}]
                    if len(args) != 1 or args[0].type != "string":
                        raise MirrorError(f"Unsupported CSS URL in {owner}")
                    update_string(args[0])
                else:
                    for attr in ("arguments", "content"):
                        if hasattr(token, attr):
                            walk(getattr(token, attr))

        walk(tokens)
        return tinycss2.serialize(tokens)

    def html(self, data, base, owner):
        soup = BeautifulSoup(data.decode("utf-8-sig"), "html.parser")
        if owner == "index.html":
            if not soup.select("article[data-sequence]") or not any("jbKeys" in s.get_text() for s in soup.find_all("script")):
                raise MirrorError("Upstream page is missing expected cards or jbKeys; possible error/challenge page")
            if not any(urlsplit(a.get("href", "")).path.endswith(".zip") for a in soup.find_all("a")):
                raise MirrorError("Upstream page is missing its ZIP download link")
        if soup.find("base"):
            raise MirrorError("Upstream introduced a base element; review URL handling")
        for script in list(soup.find_all("script", src=True)):
            if urlsplit(urljoin(base, script["src"])).hostname == "static.cloudflareinsights.com":
                script.decompose()
        for element in soup.find_all(True):
            if element.has_attr("srcset"):
                raise MirrorError("Upstream introduced srcset; add explicit support before publishing")
            for attr in ("src", "poster"):
                if element.has_attr(attr):
                    element[attr] = self.reference(element[attr], base, owner)
            if element.name in {"link", "a"} and element.has_attr("href"):
                element["href"] = self.reference(element["href"], base, owner, navigation=element.name == "a")
            if element.has_attr("style"):
                element["style"] = self.css(element["style"], base, owner)
            if element.name == "style" and element.string:
                element.string.replace_with(self.css(str(element.string), base, owner))
        return soup.encode("utf-8", formatter="minimal")

    def download(self, url):
        for attempt in range(4):
            try:
                with self.session.stream("GET", url, timeout=(15, 60)) as response:
                    if response.headers.get("cf-mitigated") == "challenge":
                        raise MirrorError(f"Cloudflare browser challenge for {url} (HTTP {response.status_code}); previous snapshot retained")
                    response.raise_for_status()
                    final = urlsplit(response.url)
                    if (final.scheme, final.netloc) != self.origin:
                        raise MirrorError(f"Unexpected cross-origin redirect: {url} -> {response.url}")
                    chunks, size = [], 0
                    for chunk in response.iter_content():
                        size += len(chunk)
                        if size > MAX_FILE_SIZE:
                            raise MirrorError(f"File exceeds crawler 25 MiB limit: {url}")
                        chunks.append(chunk)
                    if not size:
                        raise MirrorError(f"Empty upstream response: {url}")
                    return b"".join(chunks), response.headers.get("Content-Type", "").split(";", 1)[0].lower(), response.url
            except requests.exceptions.RequestException as exc:
                response = exc.response
                status = response.status_code if response is not None else None
                if attempt == 3 or (isinstance(exc, requests.exceptions.HTTPError) and status not in {429, 500, 502, 503, 504}):
                    raise MirrorError(f"Download failed for {url}: {exc}") from exc
                delay = 2 ** attempt
                retry_after = response.headers.get("Retry-After", "") if response is not None else ""
                if retry_after:
                    try:
                        delay = int(retry_after) if retry_after.isdigit() else (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        pass
                # Bound server-directed delays so an unavailable source cannot stall CI.
                delay = min(60, max(0, delay))
                print(f"Retrying {url} in {delay:g}s ({attempt + 1}/3)", flush=True)
                time.sleep(delay)

    def store(self, name, data, source):
        if len(data) > MAX_FILE_SIZE:
            raise MirrorError(f"Generated file exceeds crawler 25 MiB limit: {name}")
        path = self.stage / safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[name] = {"source": source, "bytes": len(data), "sha256": digest(data)}

    def build(self):
        self.reference(self.source, self.source, "index.html")
        self.reference(REQUIRED_ZIP, self.source, "index.html")
        try:
            while self.queue:
                url, name = self.queue.popleft()
                print(f"Fetching {name}", flush=True)
                data, mime, final_url = self.download(url)
                suffix = PurePosixPath(name).suffix.lower()
                if suffix in {".html", ".htm"}:
                    if mime not in {"text/html", "application/xhtml+xml"}:
                        raise MirrorError(f"Expected HTML for {name}, received {mime}")
                    data = self.html(data, final_url, name)
                else:
                    if mime in {"text/html", "application/xhtml+xml"} or data.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                        raise MirrorError(f"Received an HTML error page instead of {name}")
                    if suffix == ".zip":
                        check_zip(data, name)
                    elif suffix == ".css":
                        data = self.css(data.decode("utf-8-sig"), final_url, name).encode("utf-8")
                self.store(name, data, url)
            self.store(".nojekyll", b"", None)
            error_page = BeautifulSoup((TEMPLATES / "404.html").read_text(encoding="utf-8"), "html.parser")
            error_page.find("a", id="home-link")["href"] = self.home_url
            self.store("404.html", error_page.encode("utf-8"), None)
            return {
                "generator": GENERATOR,
                "schema_version": 1,
                "source": self.source,
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "files": dict(sorted(self.files.items())),
            }
        finally:
            self.session.close()


def mirror(source=SOURCE, output=Path("public"), home_url="./"):
    output = Path(output).absolute()
    if output.is_symlink() or output.resolve() in {Path.cwd().resolve(), Path(output.anchor)}:
        raise MirrorError(f"Unsafe output directory: {output}")
    old = None
    if output.exists() and any(output.iterdir()):
        old = read_manifest(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mirror-stage-", dir=output.parent) as temporary:
        stage = Path(temporary) / "public"
        stage.mkdir()
        manifest = Crawler(source, stage, home_url).build()
        (stage / "snapshot.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        verify_snapshot(stage)
        if old and old["source"] == manifest["source"] and old["files"] == manifest["files"]:
            try:
                verify_snapshot(output)
                return False
            except MirrorError:
                pass  # Rebuild a corrupted local snapshot even when upstream has not changed.
        # Keep the only previous snapshot outside automatic staging cleanup.
        backup = output.parent / f".mirror-backup-{uuid4().hex}"
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError as publish_error:
            if backup.exists():
                try:
                    backup.rename(output)
                except OSError as rollback_error:
                    raise MirrorError(f"Publish and rollback failed. Previous snapshot preserved at {backup}: {rollback_error}") from publish_error
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=SOURCE)
    parser.add_argument("--output", type=Path, default=Path("public"))
    parser.add_argument("--home-url", default="./", help="Site homepage URL for nested 404 recovery; supplied by GitHub Pages in CI")
    parser.add_argument("--verify-only", action="store_true", help="Verify the saved inventory, checksums, and ZIPs without network access")
    args = parser.parse_args()
    try:
        if args.verify_only:
            manifest = verify_snapshot(args.output)
            print(f"Verified {len(manifest['files'])} files; snapshot {manifest['captured_at']}")
        else:
            changed = mirror(args.source, args.output, args.home_url)
            print("Snapshot updated." if changed else "Snapshot unchanged.")
        return 0
    except (MirrorError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
