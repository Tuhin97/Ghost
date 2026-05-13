"""
agents/github_agent.py — GitHub monitoring for Ghost.

Monitors all your GitHub repos for:
  - New pull requests
  - PR merges and closes
  - New issues
  - CI/build failures
  - New commits on main

Uses GitHub REST API v3 — no scraping, official API.
Rate limit: 5000 requests/hour with a token — plenty for personal use.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

# GitHub API base URL
GITHUB_API = "https://api.github.com"

# Headers for every request — auth + API version
def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_all_repos() -> list[dict]:
    """
    Fetch all repos for the authenticated user.

    Returns:
        list of repo dicts with name, full_name, private, url
    """
    repos  = []
    page   = 1

    while True:
        response = requests.get(
            f"{GITHUB_API}/user/repos",
            headers=_headers(),
            params={
                "per_page": 100,
                "page":     page,
                "sort":     "updated",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        if not data:
            break

        for repo in data:
            repos.append({
                "name":      repo["name"],
                "full_name": repo["full_name"],
                "private":   repo["private"],
                "url":       repo["html_url"],
                "default_branch": repo.get("default_branch", "main"),
            })

        page += 1

    return repos


def get_open_prs(repo_full_name: str) -> list[dict]:
    """
    Get open pull requests for a repo.

    Args:
        repo_full_name: e.g. "tuhinanand/Ghost"

    Returns:
        list of PR dicts
    """
    response = requests.get(
        f"{GITHUB_API}/repos/{repo_full_name}/pulls",
        headers=_headers(),
        params={"state": "open", "per_page": 10},
        timeout=10,
    )
    response.raise_for_status()

    prs = []
    for pr in response.json():
        prs.append({
            "number":  pr["number"],
            "title":   pr["title"],
            "author":  pr["user"]["login"],
            "url":     pr["html_url"],
            "created": pr["created_at"],
            "draft":   pr.get("draft", False),
        })

    return prs


def get_open_issues(repo_full_name: str) -> list[dict]:
    """
    Get open issues for a repo (excludes PRs).

    Args:
        repo_full_name: e.g. "tuhinanand/Ghost"

    Returns:
        list of issue dicts
    """
    response = requests.get(
        f"{GITHUB_API}/repos/{repo_full_name}/issues",
        headers=_headers(),
        params={"state": "open", "per_page": 10},
        timeout=10,
    )
    response.raise_for_status()

    issues = []
    for issue in response.json():
        # GitHub returns PRs in the issues endpoint too — filter them out
        if "pull_request" in issue:
            continue
        issues.append({
            "number": issue["number"],
            "title":  issue["title"],
            "author": issue["user"]["login"],
            "url":    issue["html_url"],
            "created": issue["created_at"],
            "labels": [l["name"] for l in issue.get("labels", [])],
        })

    return issues


def get_recent_commits(repo_full_name: str, branch: str = "main", count: int = 5) -> list[dict]:
    """
    Get recent commits on a branch.

    Args:
        repo_full_name: e.g. "tuhinanand/Ghost"
        branch:         branch name — defaults to main
        count:          number of commits to return

    Returns:
        list of commit dicts
    """
    response = requests.get(
        f"{GITHUB_API}/repos/{repo_full_name}/commits",
        headers=_headers(),
        params={"sha": branch, "per_page": count},
        timeout=10,
    )
    response.raise_for_status()

    commits = []
    for commit in response.json():
        commits.append({
            "sha":     commit["sha"][:7],
            "message": commit["commit"]["message"].split("\n")[0],  # first line only
            "author":  commit["commit"]["author"]["name"],
            "date":    commit["commit"]["author"]["date"],
            "url":     commit["html_url"],
        })

    return commits


def get_ci_status(repo_full_name: str, branch: str = "main") -> dict:
    """
    Get the latest CI/workflow run status for a branch.

    Args:
        repo_full_name: e.g. "tuhinanand/Ghost"
        branch:         branch to check

    Returns:
        dict with status, conclusion, workflow name, url
    """
    response = requests.get(
        f"{GITHUB_API}/repos/{repo_full_name}/actions/runs",
        headers=_headers(),
        params={"branch": branch, "per_page": 1},
        timeout=10,
    )
    response.raise_for_status()

    runs = response.json().get("workflow_runs", [])

    if not runs:
        return {"status": "no_runs", "conclusion": None}

    run = runs[0]
    return {
        "workflow": run["name"],
        "status":   run["status"],       # queued, in_progress, completed
        "conclusion": run.get("conclusion"),  # success, failure, cancelled
        "url":      run["html_url"],
        "created":  run["created_at"],
    }


def get_repo_summary(repo_full_name: str, default_branch: str = "main") -> str:
    """
    Get a full summary of a repo — PRs, issues, CI, recent commits.

    Args:
        repo_full_name: e.g. "tuhinanand/Ghost"
        default_branch: main branch name

    Returns:
        formatted summary string
    """
    lines = [f"=== {repo_full_name} ==="]

    # PRs
    try:
        prs = get_open_prs(repo_full_name)
        if prs:
            lines.append(f"\nOpen PRs ({len(prs)}):")
            for pr in prs:
                draft = " [DRAFT]" if pr["draft"] else ""
                lines.append(f"  #{pr['number']} {pr['title']}{draft} by {pr['author']}")
        else:
            lines.append("\nNo open PRs.")
    except Exception as e:
        lines.append(f"\nPRs: error — {e}")

    # Issues
    try:
        issues = get_open_issues(repo_full_name)
        if issues:
            lines.append(f"\nOpen Issues ({len(issues)}):")
            for issue in issues:
                lines.append(f"  #{issue['number']} {issue['title']} by {issue['author']}")
        else:
            lines.append("\nNo open issues.")
    except Exception as e:
        lines.append(f"\nIssues: error — {e}")

    # CI
    try:
        ci = get_ci_status(repo_full_name, default_branch)
        if ci["status"] == "no_runs":
            lines.append("\nCI: No workflow runs found.")
        else:
            conclusion = ci["conclusion"] or ci["status"]
            lines.append(f"\nCI: {ci['workflow']} — {conclusion.upper()}")
            if conclusion == "failure":
                lines.append(f"  Failed run: {ci['url']}")
    except Exception as e:
        lines.append(f"\nCI: error — {e}")

    # Recent commits
    try:
        commits = get_recent_commits(repo_full_name, default_branch, count=3)
        if commits:
            lines.append(f"\nRecent commits:")
            for c in commits:
                lines.append(f"  {c['sha']} {c['message']} — {c['author']}")
    except Exception as e:
        lines.append(f"\nCommits: error — {e}")

    return "\n".join(lines)


def monitor_all_repos() -> str:
    """
    Get a summary of all repos — PRs, issues, CI failures.

    Returns:
        formatted report of all repos that need attention
    """
    try:
        repos = get_all_repos()
    except Exception as e:
        return f"Could not fetch repos: {e}"

    if not repos:
        return "No repos found."

    attention = []  # repos that need attention
    clean     = []  # repos that are fine

    for repo in repos:
        try:
            prs     = get_open_prs(repo["full_name"])
            issues  = get_open_issues(repo["full_name"])
            ci      = get_ci_status(repo["full_name"], repo["default_branch"])

            ci_failed = ci.get("conclusion") == "failure"

            if prs or issues or ci_failed:
                summary = f"\n{repo['full_name']}:"
                if prs:
                    summary += f"\n  {len(prs)} open PR(s)"
                if issues:
                    summary += f"\n  {len(issues)} open issue(s)"
                if ci_failed:
                    summary += f"\n  CI FAILED — {ci['url']}"
                attention.append(summary)
            else:
                clean.append(repo["name"])

        except Exception as e:
            logger.warning("Error checking %s: %s", repo["full_name"], e)
            continue

    lines = []
    if attention:
        lines.append(f"Repos needing attention ({len(attention)}):")
        lines.extend(attention)
    if clean:
        lines.append(f"\nClean repos ({len(clean)}): {', '.join(clean)}")

    return "\n".join(lines) if lines else "All repos are clean."