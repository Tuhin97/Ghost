"""
agents/excel_agent.py — Build Excel job tracker spreadsheet.

Creates a formatted Excel file with job details, match scores,
tailored resume links, and apply links.
"""

import logging
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path.home() / "Documents" / "JobApplications"


def build_tracker(jobs: list[dict], date_str: str = None) -> str:
    """
    Build an Excel job tracker from a list of processed job dicts.

    Each job dict should have:
        title, company, recruiter_email, salary, match_score,
        gdoc_link, linkedin_url, apply_url

    Returns:
        path to saved Excel file
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    output_path = OUTPUT_DIR / f"jobs_{date_str}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Job Applications"

    # ── Header row ────────────────────────────────────────────────────────────
    headers = [
    "Job Title",
    "Company",
    "Seniority",
    "Employment Type",
    "Workplace",
    "Location",
    "Posted At",
    "Applicants",
    "Company Size",
    "Company Website",
    "Recruiter Email",
    "Salary",
    "Master CV Score",
    "Tailored CV Score",
    "Tailored Resume",
    "LinkedIn Post",
    "Apply Link",
]
    header_font    = Font(bold=True, color="FFFFFF")
    header_fill    = PatternFill(fill_type="solid", fgColor="1F6FEB")
    header_align   = Alignment(horizontal="center", vertical="center")

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = header_align

    # ── Data rows ─────────────────────────────────────────────────────────────
    score_high = PatternFill(fill_type="solid", fgColor="1A7F37")   # green  ≥ 75
    score_mid  = PatternFill(fill_type="solid", fgColor="9E6A03")   # yellow 50–74
    score_low  = PatternFill(fill_type="solid", fgColor="DA3633")   # red    < 50

    for row, job in enumerate(jobs, 2):
        ws.cell(row=row, column=1, value=job.get("title", ""))
        ws.cell(row=row, column=2, value=job.get("company", ""))
        ws.cell(row=row, column=3, value=job.get("seniority", ""))
        ws.cell(row=row, column=4, value=job.get("employment_type", ""))
        ws.cell(row=row, column=5, value=job.get("workplace_type", ""))
        ws.cell(row=row, column=6, value=job.get("location", ""))
        ws.cell(row=row, column=7, value=job.get("posted_at", ""))
        ws.cell(row=row, column=8, value=job.get("applicants", ""))
        ws.cell(row=row, column=9, value=job.get("company_size", ""))
        ws.cell(row=row, column=10, value=job.get("company_website", ""))
        ws.cell(row=row, column=11, value=job.get("recruiter_email", ""))
        ws.cell(row=row, column=12, value=job.get("salary", "Not specified"))

        # Master score
        master_score = job.get("master_score", 0)
        ws.cell(row=row, column=13, value=f"{master_score}/100")

        # Tailored score with colour coding
        score = job.get("match_score", 0)
        score_cell = ws.cell(row=row, column=14, value=f"{score}/100")
        if score >= 75:
            score_cell.fill = score_high
            score_cell.font = Font(color="FFFFFF", bold=True)
        elif score >= 50:
            score_cell.fill = score_mid
            score_cell.font = Font(color="FFFFFF")
        else:
            score_cell.fill = score_low
            score_cell.font = Font(color="FFFFFF")

        # Hyperlinks — shift columns by 1
        gdoc = job.get("gdoc_link", "")
        if gdoc:
            cell = ws.cell(row=row, column=15, value="View Resume")
            cell.hyperlink = gdoc
            cell.font = Font(color="1F6FEB", underline="single")

        li_url = job.get("linkedin_url", "")
        if li_url:
            cell = ws.cell(row=row, column=16, value="LinkedIn")
            cell.hyperlink = li_url
            cell.font = Font(color="1F6FEB", underline="single")

        apply_url = job.get("apply_url", "")
        if apply_url:
            cell = ws.cell(row=row, column=17, value="Apply")
            cell.hyperlink = apply_url
            cell.font = Font(color="1F6FEB", underline="single")

    # ── Column widths ─────────────────────────────────────────────────────────
    col_widths = [35, 25, 15, 15, 12, 20, 15, 12, 15, 25, 25, 20, 14, 14, 14, 12, 12]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    wb.save(output_path)
    logger.info("Job tracker saved to %s", output_path)
    return str(output_path)