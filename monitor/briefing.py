"""
monitor/briefing.py — Daily morning briefing.

Runs as a background task. Every morning at the configured
hour, Ghost collects data from all agents and broadcasts
a summary to all connected devices via WebSocket.

Briefing includes:
  1. Calendar events for today
  2. Unread email count
  3. System health (disk, CPU, RAM)
  4. Habit suggestions from pattern learner

Only sends once per day — tracks last sent date to avoid
duplicate briefings on server restart.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone

import psutil

import config
from agents.calendar_agent import get_events
from agents.email_agent import read_emails
from monitor.pattern_learner import get_current_habits

logger = logging.getLogger(__name__)

# Tracks the last date a briefing was sent
# Prevents duplicate briefings if server restarts during briefing hour
_last_briefing_date: date = None

def _collect_briefing_data() -> dict:
    """
    Collect data from all agents for the morning briefing.

    Runs synchronously — called inside asyncio.to_thread()
    so it doesn't block the event loop.

    Returns:
        dict with keys: calendar, emails, disk, cpu, ram, habits
    """
    data = {}

    # --- Calendar ---
    try:
        # Get today's events only — 1 day
        data["calendar"] = get_events(days=1)
    except Exception as e:
        logger.error("Briefing: calendar failed: %s", e)
        data["calendar"] = "Could not fetch calendar."

    # --- Emails ---
    try:
        emails = read_emails(count=5)
        # Just count them — don't include full content in briefing
        email_count = len(emails) if isinstance(emails, list) else 0
        data["emails"] = f"{email_count} unread email(s)."
    except Exception as e:
        logger.error("Briefing: email failed: %s", e)
        data["emails"] = "Could not fetch emails."

    # --- System health ---
    try:
        data["disk"] = psutil.disk_usage("/").percent
        data["cpu"]  = psutil.cpu_percent(interval=1)
        data["ram"]  = psutil.virtual_memory().percent
    except Exception as e:
        logger.error("Briefing: system metrics failed: %s", e)
        data["disk"] = data["cpu"] = data["ram"] = 0.0

    # --- Habits ---
    try:
        habits = get_current_habits()
        data["habits"] = [h["description"] for h in habits]
    except Exception as e:
        logger.error("Briefing: habits failed: %s", e)
        data["habits"] = []

    return data

def _format_briefing(data: dict) -> str:
    """
    Format collected data into a readable morning briefing message.

    Args:
        data: dict from _collect_briefing_data()

    Returns:
        formatted string to broadcast to all devices
    """
    now = datetime.now(timezone.utc)
    lines = []

    lines.append(f"Good morning. Here's your briefing for {now.strftime('%A, %d %B %Y')}.")
    lines.append("")

    # Calendar
    lines.append("CALENDAR:")
    lines.append(data.get("calendar", "No calendar data."))
    lines.append("")

    # Emails
    lines.append("EMAILS:")
    lines.append(data.get("emails", "No email data."))
    lines.append("")

    # System health
    disk = data.get("disk", 0.0)
    cpu  = data.get("cpu",  0.0)
    ram  = data.get("ram",  0.0)
    lines.append("SYSTEM HEALTH:")
    lines.append(f"  Disk: {disk:.1f}%  CPU: {cpu:.1f}%  RAM: {ram:.1f}%")
    lines.append("")

    # Habits
    habits = data.get("habits", [])
    if habits:
        lines.append("PATTERNS:")
        for h in habits:
            lines.append(f"  {h}")
        lines.append("")

    return "\n".join(lines)

async def start_briefing(ws_manager) -> None:
    """
    Infinite loop that checks every minute if it's time
    to send the morning briefing.

    Started once at server startup via FastAPI lifespan.
    Runs for the lifetime of the server.

    Args:
        ws_manager: WebSocketManager — used to broadcast briefing
    """
    global _last_briefing_date

    logger.info(
        "Morning briefing scheduler started. "
        "Briefing hour: %s:00 UTC",
        config.BRIEFING_HOUR
    )

    while True:
        try:
            now  = datetime.now(timezone.utc)
            today = now.date()

            # Check if it's the right hour and we haven't sent today
            if (
                now.hour == config.BRIEFING_HOUR
                and _last_briefing_date != today
            ):
                logger.info("Sending morning briefing...")

                # Collect data in a thread — some calls are blocking
                data = await asyncio.to_thread(_collect_briefing_data)

                # Format into readable message
                message = _format_briefing(data)

                # Broadcast to all connected devices
                await ws_manager.broadcast(json.dumps({
                    "type":    "briefing",
                    "message": message,
                }))

                # Mark as sent for today
                _last_briefing_date = today
                logger.info("Morning briefing sent.")

        except Exception as e:
            logger.error("Briefing loop failed: %s", e)

        # Check every 60 seconds
        await asyncio.sleep(60)
