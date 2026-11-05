"""
agents/job_pipeline.py — Daily job application pipeline.

Flow:
  1. Search for jobs via the Adzuna API
  2. Score each job against CV
  3. For jobs above threshold: tailor resume + generate cover letter
  4. Upload tailored resume to Google Docs
  5. Build Excel tracker
  6. Email digest with Excel attachment

Run manually:  python3 -m agents.job_pipeline
Scheduled:     runs daily at configured hour via APScheduler in orchestrator
"""

import logging
from datetime import datetime

import config
from agents.job_agent import search_jobs, score_match, tailor_resume, draft_cover_letter, _read_cv
from agents.gdocs_agent import upload_resume_as_doc
from agents.excel_agent import build_tracker
from agents.email_agent import send_email

logger = logging.getLogger(__name__)


def _parse_score(score_text: str) -> int:
    """Extract numeric score from Claude's score_match response."""
    import re
    match = re.search(r"\b(\d{1,3})\s*/\s*100\b", score_text)
    if match:
        return int(match.group(1))
    # fallback — look for standalone number
    match = re.search(r"\b(\d{1,3})\b", score_text)
    if match:
        return min(int(match.group(1)), 100)
    return 0


def run_pipeline(claude_client) -> str:
    """
    Run the full job pipeline. Returns a summary string.

    Args:
        claude_client: Anthropic client from orchestrator
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    logger.info("=== Job pipeline starting: %s ===", date_str)

    # Step 1 — search Adzuna
    logger.info("Searching for jobs...")
    try:
        jobs = search_jobs(
            keywords=config.JOB_PIPELINE_KEYWORDS,
            location=config.JOB_PIPELINE_LOCATION,
        )[:config.JOB_PIPELINE_MAX_JOBS]
    except Exception as e:
        logger.error("Job search failed: %s", e)
        return f"Pipeline failed at job search: {e}"

    if not jobs:
        logger.warning("No jobs returned from Adzuna")
        return "No jobs found today."

    for job in jobs:
        job["apply_url"] = job.get("url", "")

    logger.info("Found %d jobs", len(jobs))

    # Step 2 — score, tailor, upload for each job
    processed = []
    for i, job in enumerate(jobs, 1):
        logger.info("Processing job %d/%d: %s at %s", i, len(jobs), job["title"], job["company"])

        try:
            # Step 1 — score master CV
            logger.info("Scoring master CV...")
            master_score_text = score_match(job, claude_client, config.MODEL_SMART)
            master_score = _parse_score(master_score_text)
            job["master_score"] = master_score
            logger.info("Master CV score: %d/100", master_score)

            # Step 2 — tailor resume + cover letter
            logger.info("Tailoring resume...")
            tailored = tailor_resume(job, claude_client, config.MODEL_SMART)
            # Strip the file path prefix that tailor_resume prepends
            if tailored.startswith("Tailored CV saved"):
                tailored = tailored.split("\n\n", 1)[-1]
            cover    = draft_cover_letter(job, claude_client, config.MODEL_SMART)

            # Step 3 — score tailored CV
            tailored_score_text = score_match(job, claude_client, config.MODEL_SMART, cv_text=tailored)
            tailored_score = _parse_score(tailored_score_text)
            job["match_score"]  = tailored_score
            job["score_text"]   = tailored_score_text
            logger.info("Tailored CV score: %d/100", tailored_score)

            # Step 4 — upload to Google Docs
            doc_title = f"Tuhin — {job['title']} at {job['company']} ({date_str})"
            gdoc_link = upload_resume_as_doc(doc_title, tailored)
            job["gdoc_link"]    = gdoc_link
            job["cover_letter"] = cover

            processed.append(job)

        except Exception as e:
            logger.error("Failed processing job %s: %s", job["title"], e)
            job["master_score"] = 0
            job["match_score"]  = 0
            job["gdoc_link"]    = ""
            job["cover_letter"] = ""
            processed.append(job)

    # Step 3 — build Excel tracker
    logger.info("Building Excel tracker...")
    try:
        excel_path = build_tracker(processed, date_str)
    except Exception as e:
        logger.error("Excel build failed: %s", e)
        excel_path = None

    # Step 4 — build email body
    above_threshold = [j for j in processed if j["match_score"] >= config.JOB_PIPELINE_SCORE_THRESHOLD]
    lines = [
        f"Ghost Job Pipeline — {date_str}",
        f"",
        f"Found {len(processed)} jobs. {len(above_threshold)} above {config.JOB_PIPELINE_SCORE_THRESHOLD}% match threshold.",
        f"",
    ]

    for j in sorted(processed, key=lambda x: x["match_score"], reverse=True):
        lines.append(f"{'★' if j['match_score'] >= config.JOB_PIPELINE_SCORE_THRESHOLD else '·'} {j['match_score']:>3}/100 — {j['title']} at {j['company']}")
        if j.get("gdoc_link"):
            lines.append(f"         Resume: {j['gdoc_link']}")
        if j.get("apply_url"):
            lines.append(f"         Apply:  {j['apply_url']}")
        lines.append("")

    email_body = "\n".join(lines)

    # Step 5 — send email
    logger.info("Sending digest email...")
    try:
        send_email(
            to=f"{config.JOB_PIPELINE_EMAIL}",
            subject=f"Ghost Job Digest — {date_str} ({len(above_threshold)} matches)",
            body=email_body,
            attachment_path=excel_path,
        )
        logger.info("Digest email sent")
    except Exception as e:
        logger.error("Email send failed: %s", e)

    summary = f"Pipeline complete: {len(processed)} jobs processed, {len(above_threshold)} strong matches. Excel saved to {excel_path}."
    logger.info(summary)
    return summary


if __name__ == "__main__":
    import anthropic
    logging.basicConfig(level=logging.INFO)
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    print(run_pipeline(client))