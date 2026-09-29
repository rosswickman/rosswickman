#!/usr/bin/env python3
"""Refresh the "Latest writing" section of README.md from a blog feed.

Pass either a feed URL or the site's home page; for a home page the feed is
auto-discovered from <link rel="alternate"> or common paths (/feed, /rss.xml,
...). Replaces everything between the BLOG-POST-LIST markers. If no feed can
be read, the README is left untouched so the rest of the workflow still runs.

Usage:
  python scripts/update_blog.py https://rosswickman.com [--count 5]
"""

import argparse
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

README = Path(__file__).resolve().parent.parent / "README.md"
START, END = "<!-- BLOG-POST-LIST:START -->", "<!-- BLOG-POST-LIST:END -->"
COMMON_PATHS = ["/feed", "/rss.xml", "/feed.xml", "/index.xml", "/atom.xml", "/rss"]
ATOM = "{http://www.w3.org/2005/Atom}"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "profile-readme-bot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def parse_feed(raw: bytes) -> list[tuple[str, str, str]]:
    """Return [(title, link, 'Mon YYYY')] for an RSS 2.0 or Atom document."""
    root = ET.fromstring(raw)
    posts = []
    for item in root.iter("item"):  # RSS
        date = item.findtext("pubDate") or ""
        try:
            date = parsedate_to_datetime(date).strftime("%b %Y")
        except (TypeError, ValueError):
            date = ""
        posts.append((item.findtext("title", "").strip(), item.findtext("link", "").strip(), date))
    for entry in root.iter(f"{ATOM}entry"):  # Atom
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        if link is None:  # Elements without children are falsy, so no `or` here
            link = entry.find(f"{ATOM}link")
        date = entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated") or ""
        try:
            date = datetime.fromisoformat(date.replace("Z", "+00:00")).strftime("%b %Y")
        except ValueError:
            date = ""
        posts.append(
            (entry.findtext(f"{ATOM}title", "").strip(), link.get("href", "") if link is not None else "", date)
        )
    return [p for p in posts if p[0] and p[1]]


def discover(url: str) -> list[tuple[str, str, str]]:
    candidates = [url]
    try:
        html = get(url).decode("utf-8", "replace")
        for tag in re.findall(r"<link[^>]+>", html, re.I):
            if re.search(r"rel=[\"']alternate", tag, re.I) and re.search(r"(rss|atom)\+xml", tag, re.I):
                href = re.search(r"href=[\"']([^\"']+)", tag, re.I)
                if href:
                    candidates.append(urljoin(url, href.group(1)))
    except Exception as exc:  # noqa: BLE001 - any failure just means "try the next candidate"
        print(f"warn: could not load {url}: {exc}")
    candidates += [url.rstrip("/") + p for p in COMMON_PATHS]

    for candidate in dict.fromkeys(candidates):
        try:
            posts = parse_feed(get(candidate))
        except Exception:  # noqa: BLE001
            continue
        if posts:
            print(f"Using feed {candidate}")
            return posts
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    posts = discover(args.url)[: args.count]
    if not posts:
        print(f"warn: no feed found for {args.url}; README left unchanged")
        return

    def md(title: str) -> str:
        return title.replace("[", r"\[").replace("]", r"\]")

    lines = [
        f"- [{md(title)}]({link})" + (f" <sub>{date}</sub>" if date else "")
        for title, link, date in posts
    ]
    text = README.read_text()
    if START not in text or END not in text:
        sys.exit("README.md is missing the BLOG-POST-LIST markers")
    head, rest = text.split(START, 1)
    if END not in rest:
        sys.exit("README.md has BLOG-POST-LIST:END before START")
    _, tail = rest.split(END, 1)
    README.write_text(f"{head}{START}\n" + "\n".join(lines) + f"\n{END}{tail}")
    print(f"Wrote {len(lines)} posts to README.md")


if __name__ == "__main__":
    main()
