"""Optional browser verification: pip install playwright; playwright install chromium."""

import argparse
import json
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


@contextmanager
def preview_url(root, prefix):
    if prefix is None:
        yield (root / "public/index.html").as_uri()
        return
    prefix = "/" + prefix.strip("/") + "/"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root / "public"), **kwargs)

        def do_GET(self):
            if not self.path.startswith(prefix):
                self.send_error(404)
                return
            self.path = "/" + self.path[len(prefix):]
            super().do_GET()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{prefix}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", help="Use an installed browser, for example chrome or msedge")
    parser.add_argument("--http-prefix", help="Run a temporary HTTP preview under a project path, for example /3.jetbra.in/")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    screenshots = root / "test-results"
    screenshots.mkdir(exist_ok=True)
    with preview_url(root, args.http_prefix) as target, sync_playwright() as playwright:
        origin = urlsplit(target).netloc
        browser = playwright.chromium.launch(channel=args.channel)
        try:
            for name, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
                page = browser.new_page(viewport={"width": width, "height": height})
                errors, failed, external, bad_status = [], [], [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("requestfailed", lambda request: failed.append(request.url))
                page.on("request", lambda request: external.append(request.url) if request.url.startswith(("http:", "https:")) and urlsplit(request.url).netloc != origin else None)
                page.on("response", lambda response: bad_status.append(response.url) if response.status >= 400 else None)
                page.goto(target, wait_until="networkidle")
                count = page.locator("article[data-sequence]").count()
                assert count > 0, "No product cards rendered"
                assert page.evaluate("typeof window._copyLicense") == "function"
                copy = page.locator('[onclick="_copyLicense(this)"]').first
                copy.click()
                assert copy.get_attribute("data-content") == "Copied!"
                if args.http_prefix:
                    zip_url = page.locator('a[href$=".zip"]').first.evaluate("a => a.href")
                    assert zip_url.startswith(target + "files/")
                    download = page.request.get(zip_url)
                    assert download.status == 200
                    assert download.body() == (root / "public" / zip_url.removeprefix(target)).read_bytes()
                page.mouse.move(0, 0)
                page.screenshot(path=str(screenshots / f"{name}.png"))
                overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                assert not errors, errors
                assert not failed, failed
                assert not external, external
                assert not bad_status, bad_status
                print(json.dumps({"viewport": name, "cards": count, "copy_feedback": "Copied!", "horizontal_overflow": overflow, "page_errors": errors, "failed_requests": failed, "external_requests": external, "http_errors": bad_status}))
                page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
