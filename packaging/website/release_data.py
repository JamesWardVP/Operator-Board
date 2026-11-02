"""Write the data files the virtualfilmtools.com download page reads, from the GitHub releases.

    latest.json     the version to download: version, date, the installer and zip (file, bytes,
                    SHA-256), and that release's notes
    releases.json   every published release, newest first: version, date, link and its "What's new"
                    notes (Markdown, and HTML ready to put on the page), for the release notes and
                    the earlier releases list

Usage (in the publish workflow):
    gh api "repos/OWNER/REPO/releases?per_page=100" > releases_api.json
    python release_data.py --releases releases_api.json --tag v0.7.0 --files out --out out

Standard library only, so it runs on any CI image.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
from typing import Any, Dict, List

INSTALL_SEPARATOR = re.compile(r"\n-{3,}\s*\n")  # the automatic install text after the notes


def notes_of(body: str) -> str:
    """A release body's own notes, without the install instructions appended by the build."""
    body = (body or "").replace("\r\n", "\n").strip()
    return INSTALL_SEPARATOR.split("\n" + body, maxsplit=1)[0].strip()


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\s][^*]*)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return text


def markdown_to_html(md: str) -> str:
    """The Markdown the release notes use (headings, bullet lists, paragraphs, bold, code, links)."""
    out: List[str] = []
    in_list = False
    paragraph: List[str] = []

    def flush() -> None:
        nonlocal in_list
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.split("\n"):
        line = raw.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if heading:
            flush()
            level = min(6, len(heading.group(1)) + 1)  # the page has its own h1/h2
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif bullet:
            if paragraph:
                out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
                paragraph.clear()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
        elif not line.strip():
            flush()
        elif in_list and raw.startswith((" ", "\t")):
            out[-1] = out[-1][:-5] + " " + _inline(line.strip()) + "</li>"  # a wrapped bullet
        else:
            if in_list:
                flush()
            paragraph.append(line.strip())
    flush()
    return "\n".join(out)


def file_info(folder: str, name: str) -> Dict[str, Any]:
    path = os.path.join(folder, name)
    with open(path, "rb") as fh:
        data = fh.read()
    return {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def build(releases: List[Dict[str, Any]], tag: str, files_dir: str) -> Dict[str, Any]:
    published = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    published.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    entries = []
    for r in published:
        notes = notes_of(r.get("body", ""))
        entries.append({"version": r["tag_name"].lstrip("v"), "tag": r["tag_name"],
                        "date": (r.get("published_at") or "")[:10], "url": r.get("html_url", ""),
                        "notes_markdown": notes, "notes_html": markdown_to_html(notes)})
    current = next((e for e in entries if e["tag"] == tag), None)
    if current is None:
        raise SystemExit(f"Release {tag} not found among the published releases")
    version = current["version"]
    latest = {"version": version, "date": current["date"],
              "setup": file_info(files_dir, f"OperatorBoard-Setup-{version}.exe"),
              "zip": file_info(files_dir, f"OperatorBoard-{version}-win64.zip"),
              "release_url": current["url"], "notes_markdown": current["notes_markdown"],
              "notes_html": current["notes_html"]}
    return {"latest": latest, "releases": {"latest": version, "releases": entries}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--releases", required=True, help="JSON from the GitHub releases API")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--files", required=True, help="folder holding the installer and zip")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    with open(args.releases, "r", encoding="utf-8") as fh:
        releases = json.load(fh)
    data = build(releases, args.tag, args.files)
    os.makedirs(args.out, exist_ok=True)
    for name, content in (("latest.json", data["latest"]), ("releases.json", data["releases"])):
        with open(os.path.join(args.out, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(content, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in data["latest"].items() if not k.startswith("notes")}, indent=2))
    print(f"releases.json: {len(data['releases']['releases'])} releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
