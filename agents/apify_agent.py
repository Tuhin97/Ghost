"""
agents/apify_agent.py — Apify LinkedIn Jobs Scraper integration.

Triggers the LinkedIn Jobs Scraper actor on Apify, polls until complete,
and returns structured job listings.
"""

import time
import logging
import requests
from urllib.parse import urlencode

import config

logger = logging.getLogger(__name__)

APIFY_BASE_URL = "https://api.apify.com/v2"
ACTOR_ID = "curious_coder~linkedin-jobs-scraper"


def _build_linkedin_url(keywords: list[str], location: str) -> str:
    """Build a LinkedIn public jobs search URL from keywords and location."""
    params = {
        "keywords": " ".join(keywords),
        "location": "Australia",
        "geoId":    "101452733",
        "position": 1,
        "pageNum":  0,
    }
    return f"https://www.linkedin.com/jobs/search?{urlencode(params)}"


def scrape_linkedin_jobs(keywords: list[str], location: str = "Adelaide", max_jobs: int = 10) -> list[dict]:
    """
    Trigger Apify LinkedIn scraper, poll until done, return job list.

    Returns:
        list of dicts with title, company, recruiter_email, salary,
        linkedin_url, apply_url, description
    """
    if not config.APIFY_API_TOKEN:
        raise RuntimeError("APIFY_API_TOKEN not set in .env")

    headers = {"Authorization": f"Bearer {config.APIFY_API_TOKEN}"}

    search_url = _build_linkedin_url(keywords, location)
    logger.info("LinkedIn search URL: %s", search_url)

    run_input = {
    "urls":          [search_url],
    "scrapeCompany": True,
    "count":         max_jobs,
    "splitByLocation": False,
    }

    # Step 1 — start the actor run
    logger.info("Starting Apify LinkedIn scrape for: %s in %s", keywords, location)
    response = requests.post(
        f"{APIFY_BASE_URL}/acts/{ACTOR_ID}/runs",
        headers=headers,
        json=run_input,
        timeout=30,
    )
    response.raise_for_status()
    run_id = response.json()["data"]["id"]
    logger.info("Apify run started: %s", run_id)

    # Step 2 — poll until complete (max 5 minutes)
    status_resp = None
    for _ in range(30):
        time.sleep(10)
        status_resp = requests.get(
            f"{APIFY_BASE_URL}/actor-runs/{run_id}",
            headers=headers,
            timeout=10,
        )
        status = status_resp.json()["data"]["status"]
        logger.info("Apify run status: %s", status)
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {status}")
    else:
        raise RuntimeError("Apify run timed out after 5 minutes")

    # Step 3 — fetch results from dataset
    dataset_id = status_resp.json()["data"]["defaultDatasetId"]
    results_resp = requests.get(
        f"{APIFY_BASE_URL}/datasets/{dataset_id}/items",
        headers=headers,
        params={"format": "json", "limit": max_jobs},
        timeout=30,
    )
    results_resp.raise_for_status()
    items = results_resp.json()

    # Step 4 — normalise fields
    jobs = []
    for item in items:
        jobs.append({
            "title":              item.get("title", "Unknown"),
            "company":            item.get("companyName", "Unknown"),
            "recruiter_email":    str(item.get("recruiterEmail", "")),
            "salary":             str(item.get("salary", "") or item.get("salaryInsights", "") or "Not specified"),
            "linkedin_url":       item.get("link", ""),
            "apply_url":          item.get("applyUrl", "") or item.get("link", ""),
            "description":        item.get("descriptionText", "")[:1000],
            "location":           str(item.get("location", location)),
            "posted_at":          str(item.get("postedAt", "")),
            "seniority":          str(item.get("seniorityLevel", "")),
            "employment_type":    str(item.get("employmentType", "")),
            "workplace_type":     ", ".join(item.get("workplaceTypes", [])) if isinstance(item.get("workplaceTypes"), list) else str(item.get("workplaceTypes", "")),
            "company_website":    str(item.get("companyWebsite", "")),
            "company_size":       str(item.get("companyEmployeesCount", "")),
            "applicants":         str(item.get("applicantsCount", "")),
        })

    logger.info("Apify returned %d jobs", len(jobs))
    return jobs