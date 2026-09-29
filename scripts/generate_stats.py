#!/usr/bin/env python3
"""Render self-hosted GitHub profile stat cards as SVG.

Replaces the public github-readme-stats instance, which is rate limited and
frequently renders as a broken image. Runs in GitHub Actions with the
built-in GITHUB_TOKEN and writes:

  assets/stats.svg      - headline numbers
  assets/languages.svg  - top languages across public, non-fork repos

Usage:
  GITHUB_TOKEN=... python scripts/generate_stats.py rosswickman
  python scripts/generate_stats.py rosswickman --mock   # offline preview
"""

import argparse
import json
import os
import sys
import urllib.request
from html import escape
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "assets"

QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar { totalContributions }
    }
    repositoriesContributedTo(
      contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, REPOSITORY]
    ) { totalCount }
    repositories(
      first: 100
      ownerAffiliations: OWNER
      isFork: false
      privacy: PUBLIC
      orderBy: { field: STARGAZERS, direction: DESC }
    ) {
      totalCount
      nodes {
        stargazerCount
        forkCount
        languages(first: 10, orderBy: { field: SIZE, direction: DESC }) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

MOCK = {
    "name": "Mock User",
    "login": "mock",
    "createdAt": "2012-04-07T16:42:25Z",
    "followers": {"totalCount": 17},
    "pullRequests": {"totalCount": 42},
    "issues": {"totalCount": 12},
    "contributionsCollection": {
        "totalCommitContributions": 321,
        "restrictedContributionsCount": 150,
        "contributionCalendar": {"totalContributions": 512},
    },
    "repositoriesContributedTo": {"totalCount": 9},
    "repositories": {
        "totalCount": 3,
        "nodes": [
            {
                "stargazerCount": 8,
                "forkCount": 2,
                "languages": {
                    "edges": [
                        {"size": 50000, "node": {"name": "HCL", "color": "#844FBA"}},
                        {"size": 30000, "node": {"name": "Python", "color": "#3572A5"}},
                    ]
                },
            },
            {
                "stargazerCount": 4,
                "forkCount": 1,
                "languages": {
                    "edges": [
                        {"size": 20000, "node": {"name": "Shell", "color": "#89e051"}},
                        {"size": 9000, "node": {"name": "PowerShell", "color": "#012456"}},
                    ]
                },
            },
            {
                "stargazerCount": 1,
                "forkCount": 0,
                "languages": {
                    "edges": [
                        {"size": 8000, "node": {"name": "TypeScript", "color": "#3178c6"}},
                        {"size": 4000, "node": {"name": "JavaScript", "color": "#f1e05a"}},
                    ]
                },
            },
        ],
    },
}

# Palette: GitHub dark surface with AWS "smile" orange accent. A solid card
# background keeps it legible on both light and dark GitHub themes.
BG = "#0d1117"
BORDER = "#30363d"
TITLE = "#FF9900"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"

WIDTH = 420
HEIGHT = 200


def fetch(login: str, token: str) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user"]


def fmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def card(title: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(title)}">
  <title>{escape(title)}</title>
  <style>
    .title {{ font: 600 17px {FONT}; fill: {TITLE}; }}
    .label {{ font: 400 13px {FONT}; fill: {MUTED}; }}
    .value {{ font: 700 13px {FONT}; fill: {TEXT}; }}
    .row {{ opacity: 0; animation: fade .4s ease-out forwards; }}
    @keyframes fade {{ to {{ opacity: 1; }} }}
  </style>
  <rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="8" fill="{BG}" stroke="{BORDER}"/>
  <text x="24" y="34" class="title">{escape(title)}</text>
{body}
</svg>
"""


def stats_svg(u: dict) -> str:
    repos = u["repositories"]["nodes"]
    cc = u["contributionsCollection"]
    rows = [
        ("Stars earned", sum(r["stargazerCount"] for r in repos)),
        ("Contributions (last year)", cc["contributionCalendar"]["totalContributions"]),
        ("Pull requests", u["pullRequests"]["totalCount"]),
        ("Issues", u["issues"]["totalCount"]),
        ("Public repos", u["repositories"]["totalCount"]),
        ("Contributed to", u["repositoriesContributedTo"]["totalCount"]),
    ]
    body = []
    for i, (label, value) in enumerate(rows):
        y = 64 + i * 22
        body.append(
            f'  <g class="row" style="animation-delay:{i * 120}ms">'
            f'<text x="24" y="{y}" class="label">{escape(label)}</text>'
            f'<text x="{WIDTH - 24}" y="{y}" class="value" text-anchor="end">{fmt(value)}</text></g>'
        )
    return card("GitHub Stats", "\n".join(body))


def visible(color: str) -> str:
    """Swap near-black language colors (e.g. PowerShell) for one readable on the dark card."""
    h = color.lstrip("#")
    if len(h) != 6:
        return MUTED
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return MUTED if 0.2126 * r + 0.7152 * g + 0.0722 * b < 60 else color


def languages_svg(u: dict, top: int = 6) -> str:
    totals: dict[str, list] = {}
    for repo in u["repositories"]["nodes"]:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = totals.setdefault(name, [0, visible(edge["node"]["color"] or "")])
            entry[0] += edge["size"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)[:top]
    grand = sum(size for _, (size, _) in ranked) or 1

    # Stacked progress bar
    bar_x, bar_y, bar_w = 24, 52, WIDTH - 48
    parts = [f'  <clipPath id="bar"><rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="8" rx="4"/></clipPath>']
    parts.append('  <g clip-path="url(#bar)">')
    x = float(bar_x)
    for name, (size, color) in ranked:
        w = bar_w * size / grand
        parts.append(f'    <rect x="{x:.2f}" y="{bar_y}" width="{w:.2f}" height="8" fill="{color}"/>')
        x += w
    parts.append("  </g>")

    # Two-column legend
    col_w = (WIDTH - 48) / 2
    for i, (name, (size, color)) in enumerate(ranked):
        col, row = i % 2, i // 2
        lx = 24 + col * col_w
        ly = 92 + row * 30
        pct = 100 * size / grand
        parts.append(
            f'  <g class="row" style="animation-delay:{i * 120}ms">'
            f'<circle cx="{lx + 5:.0f}" cy="{ly - 4}" r="5" fill="{color}"/>'
            f'<text x="{lx + 16:.0f}" y="{ly}" class="value">{escape(name)}</text>'
            f'<text x="{lx + col_w - 20:.0f}" y="{ly}" class="label" text-anchor="end">{pct:.1f}%</text></g>'
        )
    if not ranked:
        parts.append(f'  <text x="24" y="92" class="label">No language data yet</text>')
    return card("Top Languages", "\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("login")
    parser.add_argument("--mock", action="store_true", help="render sample data offline")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    if args.mock:
        user = MOCK
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("GITHUB_TOKEN is required (or pass --mock)")
        user = fetch(args.login, token)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "stats.svg").write_text(stats_svg(user))
    (args.out / "languages.svg").write_text(languages_svg(user))
    print(f"Wrote {args.out / 'stats.svg'} and {args.out / 'languages.svg'}")


if __name__ == "__main__":
    main()
