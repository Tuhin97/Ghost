"""
agents/calendar_agent.py — Google Calendar integration.

Read upcoming events and create new ones via the Google Calendar API.
Uses the same OAuth2 credentials as email_agent.py — no separate setup needed.

Actions supported:
  get_events(days)                          — fetch upcoming events
  create_event(title, start, end, description) — create a new event
"""

import logging
import os
import pickle
from datetime import datetime, timedelta, timezone
from typing import Optional

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config
from persona import sanitise_for_llm

logger = logging.getLogger(__name__)

# Calendar API needs its own scope — read/write access
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]

def _get_service():
    """
    Authenticate with Google and return a Calendar API client.

    Uses the same token.pickle as email_agent.py.
    If the token is expired, refreshes it automatically.
    If no token exists, opens browser for OAuth login.

    Returns:
        Google Calendar API service object
    """
    creds = None

    # Load existing token if it exists
    if os.path.exists(config.GMAIL_TOKEN_PATH):
        with open(config.GMAIL_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

    # If no valid credentials, refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Token expired — refresh it silently
            creds.refresh(Request())
        else:
            # No token at all — open browser for login
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_PATH,
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        # Save the refreshed/new token for next time
        with open(config.GMAIL_TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)

    # Build and return the Calendar API client
    # "calendar" = which Google API, "v3" = version 3
    return build("calendar", "v3", credentials=creds)

def get_events(days: int = 7) -> str:
    """
    Fetch upcoming calendar events for the next N days.

    Args:
        days: how many days ahead to look — default 7

    Returns:
        formatted string of upcoming events for Ghost's response
    """
    try:
        service = _get_service()

        # Start from now — in UTC ISO format
        # Google Calendar API requires this exact format
        now = datetime.now(timezone.utc).isoformat()

        # End time — N days from now
        end = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

        # Call the Calendar API
        # calendar="primary" means the user's main calendar
        events_result = service.events().list(
            calendarId="primary",
            timeMin=now,
            timeMax=end,
            singleEvents=True,        # expand recurring events
            orderBy="startTime",      # sort by start time
        ).execute()

        # Extract the list of events from the response
        events = events_result.get("items", [])

        if not events:
            return "No upcoming events found."

        # Format each event into a readable string
        lines = []
        for event in events:
            # Events can have dateTime (specific time) or date (all day)
            start = event["start"].get("dateTime", event["start"].get("date"))
            title = event.get("summary", "No title")
            description = event.get("description", "")

            line = f"- {start} | {title}"
            if description:
                line += f" | {description[:100]}"  # truncate long descriptions

            lines.append(line)

        result = "\n".join(lines)

        # Wrap in sanitise_for_llm — calendar data is external content
        return sanitise_for_llm("Google Calendar", result)

    except Exception as e:
        logger.error("Failed to fetch calendar events: %s", e)
        return f"Could not fetch calendar events: {e}"
    
def create_event(
    title: str,
    start: str,
    end: str,
    description: Optional[str] = None,
) -> str:
    """
    Create a new event in Google Calendar.

    Args:
        title:       event title — e.g. "Team standup"
        start:       start time in ISO format — e.g. "2026-04-27T09:00:00"
        end:         end time in ISO format — e.g. "2026-04-27T09:30:00"
        description: optional event notes

    Returns:
        confirmation string with event link
    """
    try:
        service = _get_service()

        # Build the event dictionary — this is what Google expects
        event = {
            "summary": title,
            "start": {
                "dateTime": start,
                "timeZone": "Australia/Adelaide",
            },
            "end": {
                "dateTime": end,
                "timeZone": "Australia/Adelaide",
            },
        }

        # Add description only if provided
        if description:
            event["description"] = description

        # Insert the event into the primary calendar
        created = service.events().insert(
            calendarId="primary",
            body=event,
        ).execute()

        # Google returns the created event with a link
        event_link = created.get("htmlLink", "")

        logger.info("Created calendar event: %s", title)
        return f"Event created: '{title}' on {start}. Link: {event_link}"

    except Exception as e:
        logger.error("Failed to create calendar event: %s", e)
        return f"Could not create event: {e}"


def format_events_for_display(events_str: str) -> str:
    """
    Clean up events string for display in Ghost's response.

    Args:
        events_str: raw output from get_events()

    Returns:
        cleaned string ready for Ghost's reply
    """
    if not events_str:
        return "No upcoming events."
    return events_str

