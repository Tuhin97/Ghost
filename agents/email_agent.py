"""
agents/email_agent.py — Gmail integration for Ghost.

Capabilities:
  - Read unread emails (last N, with sender/subject/body)
  - Send emails with optional file attachments
  - OAuth2 authentication — token saved to token.pickle after first run

Security:
  - All email content wrapped with sanitise_for_llm() before sending to Claude
  - Never auto-sends bulk emails without confirmation
  - Attachment paths validated before reading
  - OAuth2 scopes limited to minimum needed
"""

import base64
import os
import pickle
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
from persona import sanitise_for_llm

# Gmail API scopes — minimum permissions needed
# modify = read + send + move to trash (no delete)
# If you change scopes, delete token.pickle and re-authenticate
SCOPES = ["https://www.googleapis.com/auth/gmail.modify",  "https://www.googleapis.com/auth/calendar",]

# Where to save the OAuth2 token after first login
TOKEN_PATH = Path(__file__).parent.parent / "token.pickle"


def _get_gmail_service():
    """
    Authenticate with Gmail and return a service object.

    Flow:
      1. Check if token.pickle exists (already authenticated)
      2. If token expired, refresh it automatically
      3. If no token, open browser for OAuth2 login
      4. Save token for next time

    Returns:
        Gmail API service object ready to make API calls
    """
    creds = None

    # Load existing token if available
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

    # If no valid credentials, get new ones
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token expired — refresh automatically, no browser needed
            creds.refresh(Request())
        else:
            # First time — open browser for OAuth2 login
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_PATH,
                SCOPES
            )
            # Opens browser on port 0 (random available port)
            creds = flow.run_local_server(port=0)

        # Save token for next run
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def read_emails(count: int = 10) -> list[dict]:
    """
    Read the most recent unread emails from Gmail inbox.

    Args:
        count: how many unread emails to fetch (default 10)

    Returns:
        list of dicts with keys: id, from, subject, snippet, body

    Each email body is wrapped with sanitise_for_llm() before
    being returned — never pass raw email content to Claude directly.
    """
    try:
        service = _get_gmail_service()

        # Search for unread emails in inbox
        results = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=count,
        ).execute()

        messages = results.get("messages", [])
        emails = []

        for msg in messages:
            # Fetch full message details
            full_msg = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="full",
            ).execute()

            # Extract headers
            headers = full_msg["payload"].get("headers", [])
            header_map = {h["name"]: h["value"] for h in headers}

            # Extract body
            body = _extract_body(full_msg["payload"])

            emails.append({
                "id":      msg["id"],
                "from":    header_map.get("From", "Unknown"),
                "subject": header_map.get("Subject", "No subject"),
                "date":    header_map.get("Date", ""),
                "snippet": full_msg.get("snippet", ""),
                # Wrap body in prompt injection defence
                "body":    sanitise_for_llm(body, source="gmail"),
            })

        return emails

    except HttpError as e:
        raise RuntimeError(f"Gmail API error: {e}")


def _extract_body(payload: dict) -> str:
    """
    Extract plain text body from a Gmail message payload.

    Gmail messages can be simple (just a body) or multipart
    (body + attachments + HTML version). We always prefer
    plain text over HTML.

    Args:
        payload: the message payload from Gmail API

    Returns:
        decoded plain text body string
    """
    # Simple message — body is directly in payload
    if "body" in payload and payload["body"].get("data"):
        return _decode_base64(payload["body"]["data"])

    # Multipart message — find the plain text part
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                if part.get("body", {}).get("data"):
                    return _decode_base64(part["body"]["data"])
            # Nested multipart — recurse
            if part.get("mimeType", "").startswith("multipart"):
                result = _extract_body(part)
                if result:
                    return result

    return ""


def _decode_base64(data: str) -> str:
    """
    Decode Gmail's URL-safe base64 encoded content.

    Gmail encodes email bodies in base64 for safe transmission.
    We decode it back to readable text.

    Args:
        data: base64url encoded string from Gmail API

    Returns:
        decoded UTF-8 string
    """
    # Gmail uses URL-safe base64 — replace - with + and _ with /
    padded = data.replace("-", "+").replace("_", "/")
    return base64.b64decode(padded).decode("utf-8", errors="replace")


def send_email(
    to: str,
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    attachment_bytes: Optional[tuple] = None,
) -> dict:
    """
    Send an email via Gmail, optionally with a file attachment.

    Args:
        to:              recipient email address
        subject:         email subject line
        body:            plain text email body
        attachment_path: optional path to file to attach
                        Must be within SAFE_SEARCH_PATHS — validated here

    Returns:
        dict with message id and status

    Raises:
        ValueError:  if attachment path is outside safe paths
        RuntimeError: if Gmail API call fails
    """
    # Validate attachment path if provided
    if attachment_path:
        attachment_path = str(Path(attachment_path).expanduser())
        _DIR_ALIASES = {
            "downloads": "Downloads",
            "documents": "Documents",
            "desktop":   "Desktop",
            "pictures":  "Pictures",
            "videos":    "Videos",
            "music":     "Music",
            "projects":  "Projects",
            "code":      "Code",
        }
        parts = Path(attachment_path).parts
        corrected = [_DIR_ALIASES.get(p.lower(), p) for p in parts]
        attachment_path = str(Path(*corrected))
        _validate_attachment_path(attachment_path)


    try:
        service = _get_gmail_service()

        # Build the email message
        message = _build_message(to, subject, body, attachment_path, attachment_bytes)

        # Send it
        sent = service.users().messages().send(
            userId="me",
            body={"raw": message},
        ).execute()

        return {
            "status":     "sent",
            "message_id": sent["id"],
            "to":         to,
            "subject":    subject,
        }

    except HttpError as e:
        raise RuntimeError(f"Gmail send error: {e}")


def _validate_attachment_path(path: str) -> None:
    """
    Ensure attachment path is within safe search paths.

    Prevents Ghost from attaching arbitrary files like .env or SSH keys.
    Only files within SAFE_SEARCH_PATHS can be attached.

    Args:
        path: file path to validate

    Raises:
        ValueError: if path is outside allowed directories
    """
    resolved = Path(path).resolve()

    # Expand safe paths for comparison
    safe_dirs = [
        Path(p.replace("~", f"/home/{config.LINUX_USER}")).resolve()
        for p in config.SAFE_SEARCH_PATHS
    ]

    # Check if the attachment is within any safe directory
    if not any(
        str(resolved).startswith(str(safe_dir))
        for safe_dir in safe_dirs
    ):
        raise ValueError(
            f"Attachment path '{path}' is outside allowed directories. "
            f"Only files in {config.SAFE_SEARCH_PATHS} can be attached."
        )


def _build_message(
    to: str,
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
    attachment_bytes: Optional[tuple] = None,  # (bytes, filename) from Mac
) -> str:
    """
    Build a MIME email message and encode it for the Gmail API.

    MIME (Multipurpose Internet Mail Extensions) is the standard
    format for emails with attachments. We build the message in
    parts — text body + optional attachment — then encode it
    in base64 for the Gmail API.

    Args:
        to:              recipient email
        subject:         subject line
        body:            plain text body
        attachment_path: optional file to attach

    Returns:
        base64url encoded message string ready for Gmail API
    """

    if attachment_bytes:
        # Attach file bytes from memory — used for Mac files streamed over SFTP
        message = MIMEMultipart()
        message.attach(MIMEText(body, "plain"))
        file_data, filename = attachment_bytes
        attachment = MIMEApplication(file_data, Name=filename)
        attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
        message.attach(attachment)

    elif attachment_path:
        # Multipart message — body + attachment
        message = MIMEMultipart()
        message.attach(MIMEText(body, "plain"))

        # Read and attach the file
        with open(attachment_path, "rb") as f:
            filename = Path(attachment_path).name
            attachment = MIMEApplication(f.read(), Name=filename)
            attachment["Content-Disposition"] = f'attachment; filename="{filename}"'
            message.attach(attachment)
    else:
        # Simple text message — no attachment
        message = MIMEText(body, "plain")

    # Set headers
    message["to"]      = to
    message["subject"] = subject

    # Encode for Gmail API — URL-safe base64
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return raw


def format_emails_for_display(emails: list[dict]) -> str:
    """
    Format a list of emails into a readable string for Ghost's response.

    Args:
        emails: list of email dicts from read_emails()

    Returns:
        formatted string ready to show to the user
    """
    if not emails:
        return "No unread emails."

    lines = [f"You have {len(emails)} unread email(s):\n"]

    for i, email in enumerate(emails, 1):
        lines.append(
            f"{i}. From:    {email['from']}\n"
            f"   Subject: {email['subject']}\n"
            f"   Date:    {email['date']}\n"
            f"   Preview: {email['snippet'][:100]}...\n"
        )

    return "\n".join(lines)


def format_emails_for_voice(emails: list[dict]) -> str:
    """
    Format emails for voice readout — From and Subject only.

    When Ghost reads emails aloud, we don't want the full preview
    and date — that takes too long to speak and hear. This gives
    a short, natural-sounding summary instead.

    Args:
        emails: list of email dicts from read_emails()

    Returns:
        short string suitable for text-to-speech
    """
    if not emails:
        return "You have no unread emails."

    # Opening line — natural spoken English
    lines = [f"You have {len(emails)} unread email{'s' if len(emails) != 1 else ''}."]

    for i, email in enumerate(emails, 1):
        # Strip angle brackets from "Name <email@example.com>" format
        # so Piper reads it naturally — "from John Smith" not "from John Smith less than..."
        sender = email["from"]
        if "<" in sender:
            # Extract just the display name — e.g. "John Smith"
            sender = sender.split("<")[0].strip().strip('"')

        lines.append(f"{i}. From {sender}: {email['subject']}.")

    return " ".join(lines)
