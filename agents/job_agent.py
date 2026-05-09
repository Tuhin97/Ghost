"""
agents/job_agent.py — Job search via Adzuna API.

Searches for jobs matching keywords and location, ranks them by
relevance, and drafts tailored cover letters using Claude.

Flow:
  1. Search Adzuna for jobs matching keywords + location
  2. Format results — title, company, salary, location, URL
  3. Draft tailored cover letter for top match using Claude
  4. Return results to orchestrator
"""

import logging
from datetime import datetime

import requests
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Adzuna API base URL for Australia
ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/au/search/1"

# Your profile — used by Claude to tailor cover letters
USER_PROFILE = """
Name: Tuhin
Location: Adelaide, Australia
Skills: Python, Machine Learning, Data Science, AI, Cybersecurity, FastAPI
Looking for: Software Engineering, Data Science, AI/ML, Cybersecurity roles
Experience: Building Ghost — a self-hosted AI assistant with voice pipeline,
            multi-agent architecture, local LLM fine-tuning
"""


def search_jobs(keywords: list[str], location: str = "Adelaide", remote: bool = False) -> list[dict]:
    """
    Search Adzuna for jobs matching keywords and location.

    Args:
        keywords: list of search terms e.g. ["python", "machine learning"]
        location: city to search in — default Adelaide
        remote:   if True, include remote jobs

    Returns:
        list of job dicts with title, company, salary, location, url
    """
    if not config.ADZUNA_APP_ID or not config.ADZUNA_APP_KEY:
        raise RuntimeError("Adzuna API credentials not configured.")

    # Build query string from keywords
    query = " ".join(keywords)
    if remote:
        query += " remote"

    params = {
        "app_id":         config.ADZUNA_APP_ID,
        "app_key":        config.ADZUNA_APP_KEY,
        "results_per_page": 10,
        "what":           query,
        "where":          location,
        "content-type":   "application/json",
    }

    try:
        response = requests.get(ADZUNA_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        jobs = []
        for result in data.get("results", []):
            # Extract salary range if available
            salary_min = result.get("salary_min", 0)
            salary_max = result.get("salary_max", 0)
            if salary_min and salary_max:
                salary = f"${salary_min:,.0f} - ${salary_max:,.0f}"
            elif salary_min:
                salary = f"From ${salary_min:,.0f}"
            else:
                salary = "Not specified"

            jobs.append({
                "title":    result.get("title", "Unknown"),
                "company":  result.get("company", {}).get("display_name", "Unknown"),
                "location": result.get("location", {}).get("display_name", location),
                "salary":   salary,
                "url":      result.get("redirect_url", ""),
                "posted":   result.get("created", ""),
                "description": result.get("description", "")[:300],
            })

        return jobs

    except requests.RequestException as e:
        logger.error("Adzuna API error: %s", e)
        raise RuntimeError(f"Job search failed: {e}")


def format_jobs(jobs: list[dict]) -> str:
    """
    Format job listings into a readable string.

    Args:
        jobs: list of job dicts from search_jobs()

    Returns:
        formatted string ready to show to user
    """
    if not jobs:
        return "No jobs found matching your search."

    lines = [f"Found {len(jobs)} jobs:\n"]

    for i, job in enumerate(jobs, 1):
        lines.append(
            f"{i}. {job['title']} at {job['company']}\n"
            f"   Location: {job['location']}\n"
            f"   Salary:   {job['salary']}\n"
            f"   URL:      {job['url']}\n"
        )

    return "\n".join(lines)


def draft_cover_letter(job: dict, claude_client, model: str) -> str:
    """
    Draft a tailored cover letter for a specific job using Claude.

    Args:
        job:           job dict from search_jobs()
        claude_client: Anthropic client from orchestrator
        model:         which Claude model to use

    Returns:
        cover letter as string
    """
    prompt = (
        f"Write a concise, tailored cover letter for this job:\n\n"
        f"Job Title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Location: {job['location']}\n"
        f"Description: {job['description']}\n\n"
        f"Applicant profile:\n{USER_PROFILE}\n\n"
        f"Requirements:\n"
        f"- 3 short paragraphs maximum\n"
        f"- First paragraph: why this role and company specifically\n"
        f"- Second paragraph: most relevant skills and experience\n"
        f"- Third paragraph: brief closing\n"
        f"- Professional but not stiff\n"
        f"- Do not mention Ghost by name — describe it as 'a self-hosted AI assistant project'\n"
        f"- Do not use filler phrases like 'I am excited to apply'\n"
    )

    try:
        response = claude_client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.error("Cover letter generation failed: %s", e)
        return f"Could not generate cover letter: {e}"
    
async def fetch_job_from_url(url: str) -> str:
    """
    Fetch job description from a LinkedIn URL using Playwright.

    Logs into LinkedIn, navigates to the job page, extracts the
    full job description text.

    Args:
        url: LinkedIn job URL

    Returns:
        job description as plain text
    """
    from playwright.async_api import async_playwright

    if not config.LINKEDIN_EMAIL or not config.LINKEDIN_PASSWORD:
        raise RuntimeError("LinkedIn credentials not configured in .env")

    async with async_playwright() as p:
        # Launch headless browser — no visible window
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        try:
            # Step 1 — log into LinkedIn
            await page.goto("https://www.linkedin.com/login")
            await page.fill("#username", config.LINKEDIN_EMAIL)
            await page.fill("#password", config.LINKEDIN_PASSWORD)
            await page.click('[type="submit"]')
            await page.wait_for_load_state("networkidle")

            # Step 2 — navigate to job page
            await page.goto(url)
            await page.wait_for_load_state("networkidle")

            # Step 3 — click "Show more" if present to expand description
            try:
                await page.click(".jobs-description__footer-button", timeout=3000)
            except Exception:
                pass  # Button not always present

            # Step 4 — extract job description text
            description = await page.inner_text(".jobs-description__content")
            return description.strip()

        except Exception as e:
            raise RuntimeError(f"Could not fetch job from LinkedIn: {e}")
        finally:
            await browser.close()
    
def _read_cv() -> str:
    cv_path = Path(config.CV_PATH).expanduser()

    if not cv_path.exists():
        raise RuntimeError(
            f"CV not found at {cv_path}. "
            f"Create your CV at ~/Documents/resume.txt"
        )

    if cv_path.suffix.lower() == ".docx":
        from docx import Document
        doc = Document(str(cv_path))

        seen = set()
        lines = []

        # Extract from tables — CV content is in table cells
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    # Deduplicate — same cell appears multiple times in merged cells
                    if text and text not in seen:
                        seen.add(text)
                        lines.append(text)

        # Also extract any top-level paragraphs
        for para in doc.paragraphs:
            text = para.text.strip()
            if text and text not in seen:
                seen.add(text)
                lines.append(text)

        return "\n\n".join(lines)

    # Plain text
    try:
        return cv_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return cv_path.read_text(encoding="latin-1")


def score_match(job: dict, claude_client, model: str) -> str:
    """
    Score how well the user's CV matches a job description.

    Args:
        job:           job dict from search_jobs() or fetch_job_from_url()
        claude_client: Anthropic client from orchestrator
        model:         which Claude model to use

    Returns:
        match score and analysis as string
    """
    cv_text = _read_cv()

    prompt = (
        f"Analyse how well this CV matches the job description.\n\n"
        f"JOB TITLE: {job.get('title', 'Unknown')}\n"
        f"COMPANY: {job.get('company', 'Unknown')}\n"
        f"JOB DESCRIPTION:\n{job.get('description', '')}\n\n"
        f"CV:\n{cv_text}\n\n"
        f"Provide:\n"
        f"1. Match score out of 100\n"
        f"2. Top 3 matching skills/experience\n"
        f"3. Top 3 missing skills or gaps\n"
        f"4. One sentence recommendation\n"
        f"Be direct and honest. No fluff."
    )

    try:
        response = claude_client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        return f"Could not score match: {e}"


def tailor_resume(job: dict, claude_client, model: str) -> str:
    """
    Rewrite the CV tailored to a specific job description.

    Args:
        job:           job dict with title, company, description
        claude_client: Anthropic client from orchestrator
        model:         which Claude model to use

    Returns:
        tailored CV as string, also saved to ~/Documents/resume_tailored.txt
    """
    cv_text = _read_cv()

    prompt = (
        f"Rewrite this CV to be tailored for the following job.\n\n"
        f"JOB TITLE: {job.get('title', 'Unknown')}\n"
        f"COMPANY: {job.get('company', 'Unknown')}\n"
        f"JOB DESCRIPTION:\n{job.get('description', '')}\n\n"
        f"ORIGINAL CV:\n{cv_text}\n\n"
        f"Instructions:\n"
        f"- Keep all facts true — do not invent experience\n"
        f"- Reorder and reword to highlight most relevant skills first\n"
        f"- Use keywords from the job description naturally\n"
        f"- Keep the same sections but adjust emphasis\n"
        f"- Keep it concise — one page maximum\n"
        f"- Output the full tailored CV ready to copy and use\n"
    )

    try:
        response = claude_client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        tailored = response.content[0].text.strip()

        # Save tailored CV to disk
        output_path = Path("~/Documents/resume_tailored.txt").expanduser()
        output_path.write_text(tailored)
        logger.info("Tailored CV saved to %s", output_path)

        return f"Tailored CV saved to ~/Documents/resume_tailored.txt\n\n{tailored}"

    except Exception as e:
        return f"Could not tailor CV: {e}"