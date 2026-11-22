#!/usr/bin/env python3
"""
Perky GitHub Stats Generator (CLI & Core Engine)
Generates customizable, zero-dependency SVG cards for GitHub profiles:
  - stats.svg      (Overview stats + animated rank ring)
  - streak.svg     (Total contributions, current streak, longest streak)
  - top-langs.svg  (Most used languages bar + percentage breakdown)
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

THEMES: dict[str, dict[str, str]] = {
    "perky": {
        "bg": "#0f1020",
        "border": "#2e2459",
        "title": "#a78bfa",
        "text": "#e5e7eb",
        "muted": "#9ca3af",
        "accent": "#f472b6",
        "ring": "#a78bfa",
        "divider": "#9ca3af",
    },
    "tokyo-night": {
        "bg": "#1a1b26",
        "border": "#414868",
        "title": "#7aa2f7",
        "text": "#c0caf5",
        "muted": "#565f89",
        "accent": "#bb9af7",
        "ring": "#7aa2f7",
        "divider": "#565f89",
    },
    "dracula": {
        "bg": "#282a36",
        "border": "#44475a",
        "title": "#bd93f9",
        "text": "#f8f8f2",
        "muted": "#6272a4",
        "accent": "#ff79c6",
        "ring": "#bd93f9",
        "divider": "#6272a4",
    },
    "catppuccin": {
        "bg": "#1e1e2e",
        "border": "#313244",
        "title": "#cba6f7",
        "text": "#cdd6f4",
        "muted": "#a6adc8",
        "accent": "#f5c2e7",
        "ring": "#cba6f7",
        "divider": "#585b70",
    },
    "github-dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "title": "#58a6ff",
        "text": "#c9d1d9",
        "muted": "#8b949e",
        "accent": "#f78166",
        "ring": "#58a6ff",
        "divider": "#30363d",
    },
}

DEFAULT_LANG_COLORS: dict[str, str] = {
    "HTML": "#e34c26",
    "Dart": "#00B4AB",
    "JavaScript": "#f1e05a",
    "CSS": "#663399",
    "C++": "#f34b7d",
    "C": "#555555",
    "CMake": "#DA3434",
    "Python": "#3572A5",
    "Java": "#b07219",
    "Rust": "#dea584",
    "PHP": "#4F5D95",
    "R": "#198CE7",
    "TypeScript": "#3178c6",
    "Shell": "#89e051",
    "Assembly": "#6E4C13",
}

USER_OVERVIEW_QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    pullRequests(states: [OPEN, CLOSED, MERGED]) {
      totalCount
    }
    issues(states: [OPEN, CLOSED]) {
      totalCount
    }
    repositoriesContributedTo(
      first: 1
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]
    ) {
      totalCount
    }
    repositories(
      first: 100
      ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]
      isFork: false
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      totalCount
      nodes {
        name
        isPrivate
        stargazerCount
        owner {
          login
        }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
  }
}
"""

YEAR_CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      totalRepositoriesWithContributedCommits
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def gql_request(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """Execute a GitHub GraphQL query."""
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "perky-github-stats-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        raise RuntimeError(f"GraphQL error: {payload['errors']}")
    return payload["data"]


def iso_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_date(d: date, include_year: bool = True) -> str:
    return f"{d:%b} {d.day}" + (f", {d.year}" if include_year else "")


def calculate_rank(
    commits: int,
    prs: int,
    issues: int,
    stars: int,
    contributed_to: int,
) -> tuple[str, float]:
    """
    Calculate a letter rank and percentile (0..100, lower is better)
    using an exponential CDF scoring model.
    """
    commits_score = 1.0 - math.exp(-commits / 250.0)
    prs_score = 1.0 - math.exp(-prs / 50.0)
    issues_score = 1.0 - math.exp(-issues / 25.0)
    stars_score = 1.0 - math.exp(-stars / 50.0)
    repos_score = 1.0 - math.exp(-contributed_to / 15.0)

    weighted = (
        commits_score * 0.35
        + prs_score * 0.30
        + repos_score * 0.15
        + issues_score * 0.10
        + stars_score * 0.10
    )
    percentile = max(1.0, min(99.0, (1.0 - weighted) * 100.0))

    thresholds = [
        (1.0, "S"),
        (12.5, "A+"),
        (25.0, "A"),
        (37.5, "A-"),
        (50.0, "B+"),
        (62.5, "B"),
        (75.0, "B-"),
        (87.5, "C+"),
        (100.0, "C"),
    ]
    level = "C"
    for limit, grade in thresholds:
        if percentile <= limit:
            level = grade
            break
    return level, percentile


def compute_streaks(
    daily_counts: dict[date, int], today: date
) -> tuple[int, date | None, date | None, int, date | None, date | None]:
    """Compute (longest_streak, l_start, l_end, current_streak, c_start, c_end)."""
    longest_streak = 0
    l_start: date | None = None
    l_end: date | None = None
    run = 0
    run_start: date | None = None

    if daily_counts:
        d_iter = min(daily_counts.keys())
        while d_iter <= today:
            if daily_counts.get(d_iter, 0) > 0:
                if run == 0:
                    run_start = d_iter
                run += 1
                if run > longest_streak:
                    longest_streak = run
                    l_start = run_start
                    l_end = d_iter
            else:
                run = 0
            d_iter += timedelta(days=1)

    c_end = today if daily_counts.get(today, 0) > 0 else today - timedelta(days=1)
    d_cur = c_end
    current_streak = 0
    while daily_counts.get(d_cur, 0) > 0:
        current_streak += 1
        d_cur -= timedelta(days=1)
    c_start = (d_cur + timedelta(days=1)) if current_streak > 0 else None
    return longest_streak, l_start, l_end, current_streak, c_start, (c_end if current_streak > 0 else None)


def fetch_github_stats(
    username: str,
    token: str,
    max_langs: int = 6,
    exclude_langs: set[str] | None = None,
) -> dict[str, Any]:
    """Fetch complete user stats, streak calendar, and language breakdown via GraphQL."""
    now = datetime.now(timezone.utc)
    today = now.date()
    excluded = {lang.strip().lower() for lang in (exclude_langs or set()) if lang.strip()}

    overview = gql_request(USER_OVERVIEW_QUERY, {"login": username}, token)["user"]
    display_name = overview.get("name") or overview.get("login") or username
    created_at_str = overview["createdAt"]
    created_date = date.fromisoformat(created_at_str[:10])
    created_year = created_date.year

    total_stars = 0
    lang_sizes: dict[str, int] = {}
    lang_colors: dict[str, str] = {}

    repo_nodes = overview.get("repositories", {}).get("nodes") or []
    for repo in repo_nodes:
        if not repo:
            continue
        owner_login = (repo.get("owner") or {}).get("login", "")
        if owner_login.lower() == username.lower():
            total_stars += int(repo.get("stargazerCount") or 0)

        edges = (repo.get("languages") or {}).get("edges") or []
        for edge in edges:
            if not edge:
                continue
            size = int(edge.get("size") or 0)
            node = edge.get("node") or {}
            lname = node.get("name")
            if not lname or size <= 0 or lname.lower() in excluded:
                continue
            lang_sizes[lname] = lang_sizes.get(lname, 0) + size
            if node.get("color"):
                lang_colors[lname] = node["color"]

    daily_counts: dict[date, int] = {}
    all_time_commits = 0
    all_time_prs_contrib = 0
    all_time_issues_contrib = 0
    max_repos_contributed = overview.get("repositoriesContributedTo", {}).get("totalCount") or 0

    for year in range(created_year, now.year + 1):
        start = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = min(datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc), now)
        if start > end:
            continue
        ydata = gql_request(
            YEAR_CONTRIBUTIONS_QUERY,
            {"login": username, "from": iso_utc(start), "to": iso_utc(end)},
            token,
        )["user"]["contributionsCollection"]

        all_time_commits += int(ydata.get("totalCommitContributions") or 0) + int(
            ydata.get("restrictedContributionsCount") or 0
        )
        all_time_prs_contrib += int(ydata.get("totalPullRequestContributions") or 0)
        all_time_issues_contrib += int(ydata.get("totalIssueContributions") or 0)

        cal = ydata.get("contributionCalendar") or {}
        for week in cal.get("weeks") or []:
            for day_entry in week.get("contributionDays") or []:
                d = date.fromisoformat(day_entry["date"])
                daily_counts[d] = int(day_entry["contributionCount"] or 0)

    last_year_start = now - timedelta(days=365)
    last_year_data = gql_request(
        YEAR_CONTRIBUTIONS_QUERY,
        {"login": username, "from": iso_utc(last_year_start), "to": iso_utc(now)},
        token,
    )["user"]["contributionsCollection"]

    commits_last_year = int(last_year_data.get("totalCommitContributions") or 0) + int(
        last_year_data.get("restrictedContributionsCount") or 0
    )
    repos_contributed_last_year = max(
        int(last_year_data.get("totalRepositoriesWithContributedCommits") or 0),
        int(max_repos_contributed),
    )

    total_contributions = sum(daily_counts.values())
    if commits_last_year == 0 and total_contributions > 0:
        cutoff = today - timedelta(days=365)
        commits_last_year = sum(c for d, c in daily_counts.items() if d >= cutoff)

    total_prs = max(
        int(overview.get("pullRequests", {}).get("totalCount") or 0),
        all_time_prs_contrib,
    )
    total_issues = max(
        int(overview.get("issues", {}).get("totalCount") or 0),
        all_time_issues_contrib,
    )

    longest_streak, l_start, l_end, current_streak, c_start, c_end = compute_streaks(
        daily_counts, today
    )

    total_range = f"{fmt_date(created_date, True)} - Present"
    if current_streak > 0 and c_start and c_end:
        cur_range = f"{fmt_date(c_start, False)} - {fmt_date(c_end, False)}"
    else:
        cur_range = "No active streak"

    if longest_streak > 0 and l_start and l_end:
        same_year = l_start.year == l_end.year == today.year
        long_range = f"{fmt_date(l_start, not same_year)} - {fmt_date(l_end, not same_year)}"
    else:
        long_range = "-"

    sorted_langs = sorted(lang_sizes.items(), key=lambda kv: kv[1], reverse=True)[:max_langs]
    total_lang_bytes = sum(size for _, size in sorted_langs) or 1
    languages_list = []
    for lname, size in sorted_langs:
        pct = round((size / total_lang_bytes) * 100.0, 2)
        color = lang_colors.get(lname) or DEFAULT_LANG_COLORS.get(lname, "#a78bfa")
        languages_list.append({"name": lname, "percent": pct, "color": color, "size": size})

    rank_level, rank_percentile = calculate_rank(
        commits=commits_last_year,
        prs=total_prs,
        issues=total_issues,
        stars=total_stars,
        contributed_to=repos_contributed_last_year,
    )

    return {
        "username": username,
        "name": display_name,
        "stars": total_stars,
        "commits_last_year": commits_last_year,
        "all_time_commits": all_time_commits,
        "prs": total_prs,
        "issues": total_issues,
        "contributed_to": repos_contributed_last_year,
        "rank_level": rank_level,
        "rank_percentile": round(rank_percentile, 2),
        "total_contributions": total_contributions,
        "total_range": total_range,
        "current_streak": current_streak,
        "current_range": cur_range,
        "longest_streak": longest_streak,
        "longest_range": long_range,
        "languages": languages_list,
    }


def render_stats_svg(data: dict[str, Any], theme: dict[str, str], hide_border: bool = True) -> str:
    """Render the Overview GitHub Stats SVG card (495x195)."""
    name = html.escape(str(data.get("name") or data.get("username") or "GitHub"))
    stars = int(data.get("stars", 0))
    commits = int(data.get("commits_last_year", 0))
    prs = int(data.get("prs", 0))
    issues = int(data.get("issues", 0))
    contribs = int(data.get("contributed_to", 0))
    rank_level = html.escape(str(data.get("rank_level", "B-")))
    percentile = float(data.get("rank_percentile", 63.5))

    circumference = 2.0 * math.pi * 40.0
    progress_fraction = max(0.08, min(1.0, (100.0 - percentile) / 100.0))
    dash_offset = circumference * (1.0 - progress_fraction)
    border_opacity = "0" if hide_border else "1"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="495" height="195" viewBox="0 0 495 195" fill="none" role="img" aria-labelledby="statsTitle statsDesc">
  <title id="statsTitle">{name}'s GitHub Stats, Rank: {rank_level}</title>
  <desc id="statsDesc">Total Stars Earned: {stars}, Total Commits (last year): {commits}, Total PRs: {prs}, Total Issues: {issues}, Contributed to (last year): {contribs}</desc>
  <style>
    .header {{
      font: 600 18px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme['title']};
      animation: fadeIn 0.6s ease-in-out forwards;
    }}
    .stat {{
      font: 600 14px 'Segoe UI', Ubuntu, "Helvetica Neue", Sans-Serif;
      fill: {theme['text']};
    }}
    .bold {{ font-weight: 700; }}
    .icon {{ fill: {theme['accent']}; }}
    .rank-text {{
      font: 800 24px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme['text']};
    }}
    .rank-rim {{
      stroke: {theme['ring']};
      fill: none;
      stroke-width: 6;
      opacity: 0.2;
    }}
    .rank-progress {{
      stroke: {theme['ring']};
      stroke-dasharray: {circumference:.2f};
      stroke-dashoffset: {dash_offset:.2f};
      fill: none;
      stroke-width: 6;
      stroke-linecap: round;
      opacity: 0.85;
      transform: rotate(-90deg);
      transform-origin: 405px 108px;
      animation: rankAnim 1s ease-in-out forwards;
    }}
    @keyframes rankAnim {{
      from {{ stroke-dashoffset: {circumference:.2f}; }}
      to {{ stroke-dashoffset: {dash_offset:.2f}; }}
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; }}
      to {{ opacity: 1; }}
    }}
  </style>

  <rect x="0.5" y="0.5" rx="6" width="494" height="194" fill="{theme['bg']}" stroke="{theme['border']}" stroke-opacity="{border_opacity}"/>

  <text x="25" y="35" class="header">{name}'s GitHub Stats</text>

  <!-- Rank Circle -->
  <circle class="rank-rim" cx="405" cy="108" r="40"/>
  <circle class="rank-progress" cx="405" cy="108" r="40"/>
  <text x="405" y="109" text-anchor="middle" dominant-baseline="central" class="rank-text">{rank_level}</text>

  <!-- Stat Rows -->
  <g transform="translate(25, 56)">
    <g transform="translate(0, 0)">
      <svg class="icon" viewBox="0 0 16 16" width="16" height="16">
        <path fill-rule="evenodd" d="M8 .25a.75.75 0 01.673.418l1.882 3.815 4.21.612a.75.75 0 01.416 1.279l-3.046 2.97.719 4.192a.75.75 0 01-1.088.791L8 12.347l-3.766 1.98a.75.75 0 01-1.088-.79l.72-4.194L.818 6.374a.75.75 0 01.416-1.28l4.21-.611L7.327.668A.75.75 0 018 .25zm0 2.445L6.615 5.5a.75.75 0 01-.564.41l-3.097.45 2.24 2.184a.75.75 0 01.216.664l-.528 3.084 2.769-1.456a.75.75 0 01.698 0l2.77 1.456-.53-3.084a.75.75 0 01.216-.664l2.24-2.183-3.096-.45a.75.75 0 01-.564-.41L8 2.694v.001z"/>
      </svg>
      <text class="stat bold" x="25" y="12.5">Total Stars Earned:</text>
      <text class="stat bold" x="235" y="12.5" data-testid="stars">{stars}</text>
    </g>

    <g transform="translate(0, 25)">
      <svg class="icon" viewBox="0 0 16 16" width="16" height="16">
        <path fill-rule="evenodd" d="M1.643 3.143L.427 1.927A.25.25 0 000 2.104V5.75c0 .138.112.25.25.25h3.646a.25.25 0 00.177-.427L2.715 4.215a6.5 6.5 0 11-1.18 4.458.75.75 0 10-1.493.154 8.001 8.001 0 101.6-5.684zM7.75 4a.75.75 0 01.75.75v2.992l2.028.812a.75.75 0 01-.557 1.392l-2.5-1A.75.75 0 017 8.25v-3.5A.75.75 0 017.75 4z"/>
      </svg>
      <text class="stat bold" x="25" y="12.5">Total Commits (last year):</text>
      <text class="stat bold" x="235" y="12.5" data-testid="commits">{commits}</text>
    </g>

    <g transform="translate(0, 50)">
      <svg class="icon" viewBox="0 0 16 16" width="16" height="16">
        <path fill-rule="evenodd" d="M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z"/>
      </svg>
      <text class="stat bold" x="25" y="12.5">Total PRs:</text>
      <text class="stat bold" x="235" y="12.5" data-testid="prs">{prs}</text>
    </g>

    <g transform="translate(0, 75)">
      <svg class="icon" viewBox="0 0 16 16" width="16" height="16">
        <path fill-rule="evenodd" d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM0 8a8 8 0 1116 0A8 8 0 010 8zm9 3a1 1 0 11-2 0 1 1 0 012 0zm-.25-6.25a.75.75 0 00-1.5 0v3.5a.75.75 0 001.5 0v-3.5z"/>
      </svg>
      <text class="stat bold" x="25" y="12.5">Total Issues:</text>
      <text class="stat bold" x="235" y="12.5" data-testid="issues">{issues}</text>
    </g>

    <g transform="translate(0, 100)">
      <svg class="icon" viewBox="0 0 16 16" width="16" height="16">
        <path fill-rule="evenodd" d="M2 2.5A2.5 2.5 0 014.5 0h8.75a.75.75 0 01.75.75v12.5a.75.75 0 01-.75.75h-2.5a.75.75 0 110-1.5h1.75v-2h-8a1 1 0 00-.714 1.7.75.75 0 01-1.072 1.05A2.495 2.495 0 012 11.5v-9zm10.5-1V9h-8c-.356 0-.694.074-1 .208V2.5a1 1 0 011-1h8zM5 12.25v3.25a.25.25 0 00.4.2l1.45-1.087a.25.25 0 01.3 0L8.6 15.7a.25.25 0 00.4-.2v-3.25a.25.25 0 00-.25-.25h-3.5a.25.25 0 00-.25.25z"/>
      </svg>
      <text class="stat bold" x="25" y="12.5">Contributed to (last year):</text>
      <text class="stat bold" x="235" y="12.5" data-testid="contribs">{contribs}</text>
    </g>
  </g>
</svg>
"""


def render_streak_svg(data: dict[str, Any], theme: dict[str, str], hide_border: bool = True) -> str:
    """Render the GitHub Streak Stats SVG card (495x195)."""
    total = int(data.get("total_contributions", 0))
    total_range = html.escape(str(data.get("total_range", "-")))
    cur = int(data.get("current_streak", 0))
    cur_range = html.escape(str(data.get("current_range", "-")))
    longest = int(data.get("longest_streak", 0))
    long_range = html.escape(str(data.get("longest_range", "-")))
    border_opacity = "0" if hide_border else "1"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="495" height="195" viewBox="0 0 495 195" role="img" aria-labelledby="streakTitle streakDesc">
  <title id="streakTitle">GitHub Streak Stats</title>
  <desc id="streakDesc">Total Contributions: {total} ({total_range}), Current Streak: {cur} ({cur_range}), Longest Streak: {longest} ({long_range})</desc>
  <defs>
    <mask id="streakRingMask">
      <rect width="495" height="195" fill="white"/>
      <ellipse cx="247.5" cy="32" rx="13" ry="18" fill="black"/>
    </mask>
  </defs>

  <rect x="0.5" y="0.5" rx="6" width="494" height="194" fill="{theme['bg']}" stroke="{theme['border']}" stroke-opacity="{border_opacity}"/>

  <line x1="165" y1="28" x2="165" y2="170" stroke="{theme['divider']}" stroke-opacity="0.35" stroke-width="1"/>
  <line x1="330" y1="28" x2="330" y2="170" stroke="{theme['divider']}" stroke-opacity="0.35" stroke-width="1"/>

  <g font-family="'Segoe UI', Ubuntu, sans-serif" text-anchor="middle">
    <!-- Column 1: Total Contributions -->
    <text x="82.5" y="80" font-size="28" font-weight="700" fill="{theme['title']}">{total}</text>
    <text x="82.5" y="116" font-size="14" font-weight="400" fill="{theme['title']}">Total Contributions</text>
    <text x="82.5" y="146" font-size="12" font-weight="400" fill="{theme['muted']}">{total_range}</text>

    <!-- Column 2: Current Streak -->
    <g mask="url(#streakRingMask)">
      <circle cx="247.5" cy="71" r="40" fill="none" stroke="{theme['ring']}" stroke-width="5"/>
    </g>
    <g transform="translate(247.5, 19.5)">
      <path d="M 1.5 0.67 C 1.5 0.67 2.24 3.32 2.24 5.47 C 2.24 7.53 0.89 9.2 -1.17 9.2 C -3.23 9.2 -4.79 7.53 -4.79 5.47 L -4.76 5.11 C -6.78 7.51 -8 10.62 -8 13.99 C -8 18.41 -4.42 22 0 22 C 4.42 22 8 18.41 8 13.99 C 8 8.6 5.41 3.79 1.5 0.67 Z M -0.29 19 C -2.07 19 -3.51 17.6 -3.51 15.86 C -3.51 14.24 -2.46 13.1 -0.7 12.74 C 1.07 12.38 2.9 11.53 3.92 10.16 C 4.31 11.45 4.51 12.81 4.51 14.2 C 4.51 16.85 2.36 19 -0.29 19 Z" fill="{theme['accent']}"/>
    </g>
    <text x="247.5" y="80" font-size="28" font-weight="700" fill="{theme['text']}">{cur}</text>
    <text x="247.5" y="140" font-size="14" font-weight="700" fill="{theme['accent']}">Current Streak</text>
    <text x="247.5" y="166" font-size="12" font-weight="400" fill="{theme['muted']}">{cur_range}</text>

    <!-- Column 3: Longest Streak -->
    <text x="412.5" y="80" font-size="28" font-weight="700" fill="{theme['title']}">{longest}</text>
    <text x="412.5" y="116" font-size="14" font-weight="400" fill="{theme['title']}">Longest Streak</text>
    <text x="412.5" y="146" font-size="12" font-weight="400" fill="{theme['muted']}">{long_range}</text>
  </g>
</svg>
"""


def render_langs_svg(data: dict[str, Any], theme: dict[str, str], hide_border: bool = True) -> str:
    """Render the Most Used Languages SVG card (495x195)."""
    langs: list[dict[str, Any]] = data.get("languages") or []
    border_opacity = "0" if hide_border else "1"
    bar_width = 445.0

    bar_rects: list[str] = []
    x_offset = 0.0
    total_pct = sum(float(l.get("percent", 0.0)) for l in langs) or 100.0
    for idx, lang in enumerate(langs):
        pct = float(lang.get("percent", 0.0))
        color = html.escape(str(lang.get("color") or DEFAULT_LANG_COLORS.get(lang.get("name", ""), "#a78bfa")))
        seg_w = (pct / total_pct) * bar_width
        if idx == len(langs) - 1:
            seg_w = max(0.0, bar_width - x_offset)
        bar_rects.append(
            f'    <rect mask="url(#langBarMask)" x="{x_offset:.2f}" y="0" width="{seg_w:.2f}" height="10" fill="{color}"/>'
        )
        x_offset += seg_w

    legend_items: list[str] = []
    rows_per_col = 3
    for idx, lang in enumerate(langs[:6]):
        col = idx // rows_per_col
        row = idx % rows_per_col
        lx = col * 220
        ly = row * 28
        lname = html.escape(str(lang.get("name", "Unknown")))
        lpct = float(lang.get("percent", 0.0))
        color = html.escape(str(lang.get("color") or DEFAULT_LANG_COLORS.get(lname, "#a78bfa")))
        legend_items.append(
            f"""    <g transform="translate({lx}, {ly})">
      <circle cx="6" cy="6" r="6" fill="{color}"/>
      <text x="20" y="10" class="lang-name">{lname} <tspan class="lang-pct">{lpct:.2f}%</tspan></text>
    </g>"""
        )

    bar_svg = "\n".join(bar_rects)
    legend_svg = "\n".join(legend_items)
    desc_summary = ", ".join(f"{l.get('name')}: {float(l.get('percent', 0)):.2f}%" for l in langs)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="495" height="195" viewBox="0 0 495 195" fill="none" role="img" aria-labelledby="langsTitle langsDesc">
  <title id="langsTitle">Most Used Languages</title>
  <desc id="langsDesc">{html.escape(desc_summary)}</desc>
  <style>
    .header {{
      font: 600 18px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme['title']};
    }}
    .lang-name {{
      font: 600 13px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme['text']};
    }}
    .lang-pct {{
      font: 400 12.5px 'Segoe UI', Ubuntu, Sans-Serif;
      fill: {theme['muted']};
    }}
  </style>

  <rect x="0.5" y="0.5" rx="6" width="494" height="194" fill="{theme['bg']}" stroke="{theme['border']}" stroke-opacity="{border_opacity}"/>

  <text x="25" y="35" class="header">Most Used Languages</text>

  <!-- Multi-segment Language Bar -->
  <g transform="translate(25, 58)">
    <mask id="langBarMask">
      <rect x="0" y="0" width="{bar_width:.0f}" height="10" rx="5" fill="white"/>
    </mask>
    <rect x="0" y="0" width="{bar_width:.0f}" height="10" rx="5" fill="#1f2037"/>
{bar_svg}
  </g>

  <!-- Language Legend -->
  <g transform="translate(25, 92)">
{legend_svg}
  </g>
</svg>
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="perky-stats",
        description="Generate custom GitHub Stats, Streak, and Most Used Languages SVG cards.",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("GH_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "perky-boo25",
        help="GitHub username (defaults to GH_USER env var or perky-boo25)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "",
        help="GitHub Personal Access Token or Actions GITHUB_TOKEN",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "assets"),
        help="Directory to write generated SVG files (default: assets)",
    )
    parser.add_argument(
        "--theme",
        default=os.environ.get("THEME", "perky"),
        choices=sorted(THEMES.keys()),
        help="Color theme preset (default: perky)",
    )
    parser.add_argument(
        "--bg-color",
        default=os.environ.get("BG_COLOR", ""),
        help="Override background hex color (e.g. #0f1020)",
    )
    parser.add_argument(
        "--title-color",
        default=os.environ.get("TITLE_COLOR", ""),
        help="Override title and primary accent hex color (e.g. #a78bfa)",
    )
    parser.add_argument(
        "--accent-color",
        default=os.environ.get("ACCENT_COLOR", ""),
        help="Override secondary accent/icon hex color (e.g. #f472b6)",
    )
    parser.add_argument(
        "--text-color",
        default=os.environ.get("TEXT_COLOR", ""),
        help="Override main text hex color (e.g. #e5e7eb)",
    )
    parser.add_argument(
        "--max-langs",
        type=int,
        default=int(os.environ.get("MAX_LANGS", "6")),
        help="Maximum number of languages to display (default: 6)",
    )
    parser.add_argument(
        "--exclude-langs",
        default=os.environ.get("EXCLUDE_LANGS", ""),
        help="Comma-separated list of languages to exclude (e.g. 'CMake,Dockerfile')",
    )
    parser.add_argument(
        "--show-border",
        action="store_true",
        default=os.environ.get("SHOW_BORDER", "").lower() in {"1", "true", "yes"},
        help="Render a subtle border around the SVG cards",
    )
    parser.add_argument(
        "--from-json",
        help="Load stats data from a local JSON file instead of querying the GitHub API",
    )
    parser.add_argument(
        "--save-json",
        help="Save fetched stats data to a JSON file",
    )
    return parser.parse_args(argv)


def resolve_theme(args: argparse.Namespace) -> dict[str, str]:
    theme = dict(THEMES.get(args.theme, THEMES["perky"]))
    if args.bg_color:
        theme["bg"] = args.bg_color
    if args.title_color:
        theme["title"] = args.title_color
        theme["ring"] = args.title_color
    if args.accent_color:
        theme["accent"] = args.accent_color
    if args.text_color:
        theme["text"] = args.text_color
    return theme


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    theme = resolve_theme(args)
    hide_border = not args.show_border
    excluded = {s.strip() for s in args.exclude_langs.split(",") if s.strip()}

    if args.from_json:
        with open(args.from_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        if not args.token:
            raise SystemExit(
                "Error: A GitHub token is required to query the GraphQL API. "
                "Set GH_TOKEN / GITHUB_TOKEN or pass --token (or use --from-json for offline rendering)."
            )
        data = fetch_github_stats(
            args.user,
            args.token,
            max_langs=args.max_langs,
            exclude_langs=excluded,
        )

    if args.save_json:
        Path(args.save_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stats_svg = render_stats_svg(data, theme, hide_border=hide_border)
    streak_svg = render_streak_svg(data, theme, hide_border=hide_border)
    langs_svg = render_langs_svg(data, theme, hide_border=hide_border)

    (out_dir / "stats.svg").write_text(stats_svg, encoding="utf-8")
    (out_dir / "streak.svg").write_text(streak_svg, encoding="utf-8")
    (out_dir / "top-langs.svg").write_text(langs_svg, encoding="utf-8")

    print(
        f"Generated SVG cards in '{out_dir}':\n"
        f"  - stats.svg     (Commits: {data.get('commits_last_year')}, PRs: {data.get('prs')}, Rank: {data.get('rank_level')})\n"
        f"  - streak.svg    (Total: {data.get('total_contributions')}, Current: {data.get('current_streak')}, Longest: {data.get('longest_streak')})\n"
        f"  - top-langs.svg ({len(data.get('languages', []))} languages)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
