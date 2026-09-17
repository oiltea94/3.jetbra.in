import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from scripts.mirror import GENERATOR, MirrorError, REQUIRED_ZIP
from scripts.package_release import ReleaseError, package_snapshot, release_version
from scripts import package_release


@pytest.fixture
def snapshot(tmp_path):
    public = tmp_path / "public"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("readme.txt", "test archive")
    files = {
        "index.html": f'<html><a href="{REQUIRED_ZIP}">jetbra.zip</a> (260914)</html>'.encode(),
        REQUIRED_ZIP: archive.getvalue(),
        ".nojekyll": b"",
        "404.html": b"Not found",
        "images/icon.svg": b"<svg/>",
        "scripts/app.js": b"window.example = true;",
    }
    manifest = {"generator": GENERATOR, "schema_version": 1, "source": "https://3.jetbra.in/", "captured_at": "2026-09-17T00:00:00+00:00", "files": {}}
    for name, data in files.items():
        path = public / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest["files"][name] = {"source": None, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (public / "snapshot.json").write_text(json.dumps(manifest), encoding="utf-8")
    return public


@pytest.mark.parametrize("label", [" (260914), configure below", " <span>(260914)</span>", "\n ( 260914 )"])
def test_version_comes_from_zip_annotation(label):
    html = f'<p>Other date 250101 <a href="files/archive.zip">Download</a>{label}</p>'
    assert release_version(html) == "260914"


@pytest.mark.parametrize("html", [
    '<a href="files/archive.zip">Download</a> no date <p>(260914)</p>',
    '<a href="files/archive.zip">Download</a> (260231)',
    '<a href="files/archive.zip">Download</a> (20260914)',
    '<a href="files/archive.zip">Download</a><br>(260914)',
    '<a href="files/archive.zip">Download</a> (260914)<a href="files/other.zip">Other</a> (260915)',
    '<a href="other.html">Other</a> (260914)',
])
def test_missing_invalid_or_ambiguous_dates_are_rejected(html):
    with pytest.raises(ReleaseError):
        release_version(html)


def test_package_contains_exactly_public_contents_at_archive_root(snapshot, tmp_path):
    path = package_snapshot(snapshot, tmp_path / "dist")
    assert path.name == "260914.zip"
    expected = {p.relative_to(snapshot).as_posix(): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == set(expected)
        assert archive.testzip() is None
        for name, data in expected.items():
            assert archive.read(name) == data


def test_package_is_stable_when_file_timestamps_change(snapshot, tmp_path):
    first = package_snapshot(snapshot, tmp_path / "dist").read_bytes()
    for path in snapshot.rglob("*"):
        if path.is_file():
            os.utime(path, (1000000000, 1000000000))
    second = package_snapshot(snapshot, tmp_path / "dist").read_bytes()
    assert first == second


def test_corrupted_snapshot_is_not_packaged(snapshot, tmp_path):
    (snapshot / REQUIRED_ZIP).write_bytes(b"corrupted")
    with pytest.raises(MirrorError, match="Checksum"):
        package_snapshot(snapshot, tmp_path / "dist")
    assert not list((tmp_path / "dist").glob("*.zip"))


def test_package_output_cannot_modify_snapshot(snapshot):
    with pytest.raises(ReleaseError, match="outside"):
        package_snapshot(snapshot, snapshot / "releases")
    assert not (snapshot / "releases").exists()


@pytest.fixture
def github(monkeypatch, tmp_path):
    class GitHub:
        release = None
        lookup_error = None
        list_error = None
        upload_error = False
        tag = None
        annotated_target = None

        def __init__(self):
            self.calls = []
            self.notes = []
            self.archive = tmp_path / "260914.zip"
            self.archive.write_bytes(b"package")

        def run(self, args, **kwargs):
            self.calls.append(args)
            code, output, error = 0, "", ""
            if args[1] == "api":
                if "/git/ref/" in args[-1]:
                    if self.tag is None:
                        code, output, error = 1, '{"status":"404"}', "Not Found"
                    else:
                        output = json.dumps({"object": self.tag})
                elif "/git/tags/" in args[-1]:
                    output = json.dumps({"object": self.annotated_target})
                elif "--slurp" in args:
                    if self.list_error:
                        code, output, error = self.list_error
                    else:
                        output = json.dumps([[self.release] if self.release else []])
                elif self.lookup_error:
                    code, output, error = self.lookup_error
                elif self.release is None or self.release["draft"]:
                    code, output, error = 1, '{"status":"404"}', "gh: Not Found (HTTP 404)"
                else:
                    output = json.dumps(self.release)
            if args[1:3] == ["release", "upload"] and self.upload_error:
                code, error = 1, "Upload failed"
            if "--notes-file" in args:
                self.notes.append(Path(args[args.index("--notes-file") + 1]).read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(args, code, output, error)

        def publish(self):
            return package_release.publish_package(self.archive, "owner/repo", "a" * 40)

        @property
        def mutations(self):
            return [call for call in self.calls if call[1] == "release"]

    fake = GitHub()
    monkeypatch.setattr(package_release, "subprocess", subprocess, raising=False)
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


def test_release_is_draft_until_upload_finishes(github):
    assert github.publish() == "published"
    create, upload, publish = github.mutations
    assert create[2:4] == ["create", "260914"]
    assert "--draft" in create
    assert create[create.index("--title") + 1] == "260914"
    assert create[create.index("--target") + 1] == "a" * 40
    assert upload[2:5] == ["upload", "260914", str(github.archive)]
    assert publish[2:4] == ["edit", "260914"]
    assert "--draft=false" in publish
    assert "<!-- jetbra-static-snapshot -->" in github.notes[0]


def test_upload_failure_does_not_publish(github):
    github.upload_error = True
    with pytest.raises(ReleaseError, match="Upload failed"):
        github.publish()
    assert [call[2] for call in github.mutations] == ["create", "upload"]


def test_managed_draft_can_resume(github):
    github.release = {"tag_name": "260914", "draft": True, "body": "<!-- jetbra-static-snapshot -->", "assets": []}
    assert github.publish() == "published"
    upload, publish = github.mutations
    assert upload[2] == "upload"
    assert "--clobber" in upload
    assert "--draft=false" in publish
    assert publish[publish.index("--target") + 1] == "a" * 40


def test_unrelated_draft_is_not_modified(github):
    github.release = {"tag_name": "260914", "draft": True, "body": "Manual release", "assets": []}
    with pytest.raises(ReleaseError, match="managed draft"):
        github.publish()
    assert not github.mutations


def test_published_date_keeps_first_package(github):
    github.release = {"draft": False, "assets": [{"name": "260914.zip", "state": "uploaded", "size": 123}]}
    assert github.publish() == "existing"
    assert not github.mutations


def test_incomplete_published_release_is_not_silently_skipped(github):
    github.release = {"draft": False, "assets": []}
    with pytest.raises(ReleaseError, match="missing"):
        github.publish()
    assert not github.mutations


@pytest.mark.parametrize("failure", [(1, '{"status":"401"}', "Bad credentials"), (1, "", "Connection failed")])
def test_api_errors_do_not_create_releases(github, failure):
    github.lookup_error = failure
    with pytest.raises(ReleaseError):
        github.publish()
    assert not github.mutations


def test_draft_list_errors_do_not_create_releases(github):
    github.list_error = (1, '{"status":"403"}', "Forbidden")
    with pytest.raises(ReleaseError, match="Forbidden"):
        github.publish()
    assert not github.mutations


def test_existing_tag_at_different_commit_is_not_published(github):
    github.tag = {"type": "commit", "sha": "b" * 40}
    with pytest.raises(ReleaseError, match="different snapshot"):
        github.publish()
    assert not github.mutations


@pytest.mark.parametrize("annotated", [False, True])
def test_matching_existing_tag_can_be_published(github, annotated):
    github.tag = {"type": "tag" if annotated else "commit", "sha": "a" * 40}
    github.annotated_target = {"type": "commit", "sha": "a" * 40}
    assert github.publish() == "published"


def test_annotated_tag_pointing_elsewhere_is_rejected(github):
    github.tag = {"type": "tag", "sha": "c" * 40}
    github.annotated_target = {"type": "commit", "sha": "b" * 40}
    with pytest.raises(ReleaseError, match="different snapshot"):
        github.publish()
    assert not github.mutations
