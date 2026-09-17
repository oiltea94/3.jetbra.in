"""Package a verified static snapshot using the upstream ZIP annotation date."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .mirror import MirrorError, verify_snapshot


DRAFT_MARKER = "<!-- jetbra-static-snapshot -->"


class ReleaseError(Exception):
    pass


def release_version(html):
    versions = set()
    inline = {"span", "b", "strong", "i", "em", "small", "time"}
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        if not urlsplit(anchor["href"]).path.lower().endswith(".zip"):
            continue
        parts = []
        for sibling in anchor.next_siblings:
            if isinstance(sibling, Comment):
                continue
            if isinstance(sibling, NavigableString):
                parts.append(str(sibling))
            elif isinstance(sibling, Tag) and sibling.name in inline:
                if any(tag.name not in inline for tag in sibling.find_all(True)):
                    break
                parts.append(sibling.get_text())
            else:
                break
        match = re.match(r"^\s*\(\s*([0-9]{6})\s*\)", "".join(parts))
        if match:
            version = match[1]
            try:
                datetime.strptime("20" + version, "%Y%m%d")
            except ValueError as exc:
                raise ReleaseError(f"Invalid ZIP annotation date: {version}") from exc
            versions.add(version)
    if len(versions) != 1:
        raise ReleaseError("Expected one unambiguous six-digit date after the ZIP link")
    return versions.pop()


def package_snapshot(public, destination):
    public = Path(public).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(public):
        raise ReleaseError("Package destination must be outside the public directory")
    manifest = verify_snapshot(public)
    version = release_version((public / "index.html").read_text(encoding="utf-8"))
    date = datetime.strptime("20" + version, "%Y%m%d")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{version}.zip"
    with tempfile.TemporaryDirectory(prefix=".package-", dir=destination) as temporary:
        archive_path = Path(temporary) / target.name
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name in sorted(set(manifest["files"]) | {"snapshot.json"}):
                entry = zipfile.ZipInfo(name, (date.year, date.month, date.day, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, (public / name).read_bytes())
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ReleaseError("Generated package failed ZIP integrity verification")
        archive_path.replace(target)
    return target


def run_gh(arguments):
    result = subprocess.run(["gh", *arguments], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise ReleaseError(result.stderr.strip() or "GitHub CLI command failed")
    return result.stdout


def github_json(endpoint, missing_ok=False):
    result = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True, encoding="utf-8",
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError(result.stderr.strip() or "Invalid GitHub API response") from exc
    if result.returncode:
        if missing_ok and str(data.get("status")) == "404":
            return None
        raise ReleaseError(result.stderr.strip() or "GitHub API request failed")
    return data


def publish_package(archive, repository, target):
    archive = Path(archive)
    version = archive.stem
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseError("Expected repository in owner/repo format")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", target):
        raise ReleaseError("Release target must be the full snapshot commit SHA")
    if not re.fullmatch(r"[0-9]{6}\.zip", archive.name) or not archive.is_file():
        raise ReleaseError("Expected an existing six-digit-date ZIP package")

    release = github_json(f"repos/{repository}/releases/tags/{version}", missing_ok=True)
    if release is None:
        # The by-tag endpoint excludes drafts; the authenticated list includes them.
        try:
            pages = json.loads(run_gh([
                "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100",
            ]))
        except json.JSONDecodeError as exc:
            raise ReleaseError("Invalid release list API response") from exc
        matches = [item for page in pages for item in page if item["tag_name"] == version]
        if len(matches) > 1:
            raise ReleaseError(f"Multiple releases found for {version}")
        release = matches[0] if matches else None
    if release and not release["draft"]:
        if not any(
            asset["name"] == archive.name and asset["state"] == "uploaded" and asset["size"] > 0
            for asset in release["assets"]
        ):
            raise ReleaseError(f"Published release {version} is missing its complete package")
        return "existing"
    if release and DRAFT_MARKER not in (release.get("body") or ""):
        raise ReleaseError(f"Release {version} is not a managed draft; refusing to modify it")

    tag = github_json(f"repos/{repository}/git/ref/tags/{version}", missing_ok=True)
    if tag:
        obj = tag["object"]
        seen = set()
        while obj["type"] == "tag":
            if obj["sha"] in seen or len(seen) >= 10:
                raise ReleaseError("Cannot resolve annotated release tag")
            seen.add(obj["sha"])
            obj = github_json(f"repos/{repository}/git/tags/{obj['sha']}")["object"]
        if obj["type"] != "commit" or obj["sha"].lower() != target.lower():
            raise ReleaseError(f"Tag {version} already points to a different snapshot")

    common = ["--repo", repository]
    with tempfile.TemporaryDirectory(prefix="jetbra-release-") as temporary:
        notes = Path(temporary) / "notes.md"
        notes.write_text(
            f"{DRAFT_MARKER}\nContents of `public/` for upstream version `{version}`.\n"
            f"\nSnapshot commit: `{target}`.\n", encoding="utf-8",
        )
        if release is None:
            run_gh([
                "release", "create", version, *common, "--draft", "--title", version,
                "--target", target, "--notes-file", str(notes),
            ])
        # Only drafts may replace an asset left by an interrupted upload.
        run_gh(["release", "upload", version, str(archive), *common, "--clobber"])
        run_gh([
            "release", "edit", version, *common, "--draft=false", "--title", version,
            "--target", target, "--notes-file", str(notes),
        ])
    return "published"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=Path("public"))
    parser.add_argument("--destination", type=Path, default=Path("dist"))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--target")
    args = parser.parse_args()
    if args.publish and (not args.repository or not args.target):
        parser.error("--publish requires --repository and --target")
    try:
        archive = package_snapshot(args.public, args.destination)
        print(f"Package: {archive}")
        if args.publish:
            status = publish_package(archive, args.repository, args.target)
            print(f"Release {archive.stem}: {status}")
    except (ReleaseError, MirrorError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
