"""
agents/gdocs_agent.py — Upload tailored resume to Google Docs.

Creates a new Google Doc with the tailored resume content and
returns a shareable link.
"""

import logging
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import pickle
from pathlib import Path

import config

logger = logging.getLogger(__name__)

TOKEN_PATH = Path(config.GMAIL_CREDENTIALS_PATH).parent / "token.pickle"


def _get_credentials() -> Credentials:
    with open(TOKEN_PATH, "rb") as f:
        return pickle.load(f)


def upload_resume_as_doc(title: str, content: str) -> str:
    """
    Create a Google Doc with the given content and return a shareable link.

    Args:
        title:   document title e.g. "Tuhin — Resume for Senior ML Engineer at Canva"
        content: plain text resume content

    Returns:
        shareable Google Docs URL
    """
    creds = _get_credentials()

    docs_service  = build("docs", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    # Step 1 — create empty doc
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]
    logger.info("Created Google Doc: %s", doc_id)

    # Step 2 — insert content
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": content,
                    }
                }
            ]
        },
    ).execute()

    # Step 3 — make it shareable (anyone with link can view)
    drive_service.permissions().create(
        fileId=doc_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info("Google Doc shareable link: %s", link)
    return link