#!/usr/bin/env python3
"""Collector Agent: Fetch GitHub Trending AI repos for the week."""
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
import time
import sys

# Config
OUTPUT_PATH = "/root/wangxiao_ai/ai-knowledge-base/knowledge/raw/github-trending-20260428.json"
SINCE_DATE = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "AI-Knowledge-Base-Collector/1.0",
}

SEARCH_QUERIES = [
    # Query 1: AI/LLM/Agent broad search, sorted by stars
    "https://api.github.com/search/repositories?q={}&sort=stars&order=desc&per_page=10".format(
        urllib.parse.quote("AI OR LLM OR agent OR \"machine learning\" OR \"generative AI\"")
    ),
    # Query 2: Recent activity + high stars as fallback
    "https://api.github.com/search/repositories?q={}&sort=stars&order=desc&per_page=10".format(
        urllib.parse.quote("stars:>5000 language:Python AI")
    ),
]


def fetch(url: str) -> dict:
    """Fetch JSON from GitHub API with basic rate-limit handling."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print("Rate limited by GitHub API. Waiting 60s...", file=sys.stderr)
            time.sleep(60)
            return fetch(url)
        raise


def normalize_repo(item: dict) -> dict:
    """Extract required fields from a GitHub repo item."""
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "full_name": item.get("full_name"),
        "description": item.get("description") or "",
        "html_url": item.get("html_url"),
        "language": item.get("language"),
        "stargazers_count": item.get("stargazers_count"),
        "forks_count": item.get("forks_count"),
        "open_issues_count": item.get("open_issues_count"),
        "topics": item.get("topics", []),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "license": (item.get("license") or {}).get("spdx_id"),
    }


def main():
    repos = []
    seen_ids = set()

    for url in SEARCH_QUERIES:
        if len(repos) >= 10:
            break
        print(f"Fetching: {url}", file=sys.stderr)
        try:
            data = fetch(url)
            items = data.get("items", [])
            for item in items:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    repos.append(normalize_repo(item))
                if len(repos) >= 10:
                    break
        except Exception as e:
            print(f"Error fetching {url}: {e}", file=sys.stderr)
            continue

    if len(repos) < 10:
        print(f"Warning: only collected {len(repos)} repos, expected 10.", file=sys.stderr)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(repos, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(repos)} repos to {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
