"""The data files the virtualfilmtools.com download page reads (packaging/website/release_data.py)."""
import hashlib
import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location(
    "release_data", os.path.join(HERE, "..", "packaging", "website", "release_data.py"))
release_data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_data)

BODY = """## What's new in 0.7.0

### Projects
- A **start screen** with `.opproject` files
  that wrap onto a second line
- See [the docs](https://example.com/docs)

Plain paragraph.

---
**Install:** download `OperatorBoard-Setup-….exe` below and run it.
"""


def test_notes_leave_out_the_install_text():
    notes = release_data.notes_of(BODY)
    assert notes.startswith("## What's new in 0.7.0") and "Install:" not in notes
    html = release_data.markdown_to_html(notes)
    assert "<h3>What&#x27;s new in 0.7.0</h3>" in html or "<h3>What's new in 0.7.0</h3>" in html
    assert "<li>A <strong>start screen</strong> with <code>.opproject</code> files that wrap onto a second line</li>" in html
    assert '<a href="https://example.com/docs">the docs</a>' in html and "<p>Plain paragraph.</p>" in html
    assert release_data.markdown_to_html("<script>x</script>") == "<p>&lt;script&gt;x&lt;/script&gt;</p>"


def test_latest_and_releases(tmp_path):
    (tmp_path / "OperatorBoard-Setup-0.7.0.exe").write_bytes(b"setup")
    (tmp_path / "OperatorBoard-0.7.0-win64.zip").write_bytes(b"zipfile")
    releases = [
        {"tag_name": "v0.6.2", "published_at": "2026-10-20T10:00:00Z", "body": "## What's new in 0.6.2\n- Spinner",
         "html_url": "u62"},
        {"tag_name": "v0.7.0", "published_at": "2026-11-01T22:00:00Z", "body": BODY, "html_url": "u70"},
        {"tag_name": "v0.8.0", "published_at": "", "body": "draft", "draft": True},
    ]
    data = release_data.build(releases, "v0.7.0", str(tmp_path))
    latest = data["latest"]
    assert (latest["version"], latest["date"], latest["release_url"]) == ("0.7.0", "2026-11-01", "u70")
    assert latest["setup"] == {"file": "OperatorBoard-Setup-0.7.0.exe", "bytes": 5,
                               "sha256": hashlib.sha256(b"setup").hexdigest()}
    assert latest["zip"]["bytes"] == 7
    assert [r["version"] for r in data["releases"]["releases"]] == ["0.7.0", "0.6.2"]  # newest first, no drafts
    assert data["releases"]["latest"] == "0.7.0"
    with pytest.raises(SystemExit):
        release_data.build(releases, "v9.9.9", str(tmp_path))
    out = tmp_path / "out"
    api = tmp_path / "api.json"
    api.write_text(json.dumps(releases))
    assert release_data.main(["--releases", str(api), "--tag", "v0.7.0", "--files", str(tmp_path),
                              "--out", str(out)]) == 0
    assert json.load(open(out / "latest.json", encoding="utf-8"))["version"] == "0.7.0"
