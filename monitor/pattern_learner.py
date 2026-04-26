"""
monitor/pattern_learner.py — Usage pattern detection.

Reads past interactions from ChromaDB and identifies
habits — actions you repeat at similar times of day
or days of the week.

Ghost uses these patterns to be proactive:
  "You usually check your emails around now. Want me to pull them up?"

How it works:
  1. Fetch all stored interactions from ChromaDB
  2. Group by action type
  3. Check time-of-day and day-of-week frequency
  4. If an action repeats >= MIN_OCCURRENCES times in a
     similar time window, flag it as a habit
  5. Return habits relevant to the current moment
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from memory import get_memory

logger = logging.getLogger(__name__)

# How many times an action must repeat before we call it a habit
MIN_OCCURRENCES = 3

# How many hours either side counts as "same time of day"
# e.g. if you check email at 9:05, 9:15, 9:30 — all within 1 hour window
TIME_WINDOW_HOURS = 1

def _fetch_all_interactions() -> list[dict]:
    """
    Fetch all stored interactions from ChromaDB.

    Returns a list of metadata dicts — each one represents
    one past interaction with Ghost.

    Returns:
        list of metadata dicts with keys:
          action, device_id, ts (timestamp)
    """
    memory = get_memory()

    # get() with no filter returns everything in the collection
    # We only need metadata — not the document vectors
    result = memory._collection.get(
        include=["metadatas"]
    )

    # result["metadatas"] is a list of dicts
    # Filter out any that don't have action or ts fields
    interactions = []
    for meta in result.get("metadatas", []):
        if meta and meta.get("action") and meta.get("ts"):
            interactions.append(meta)

    return interactions
def _group_by_action(interactions: list[dict]) -> dict:
    """
    Group interactions by action type.

    Builds a dictionary where each key is an action name
    and the value is a list of hours-of-day when it happened.

    Example output:
    {
        "read_email": [9, 9, 8, 9, 10],
        "search_file": [14, 15, 14],
    }

    Args:
        interactions: list of metadata dicts from ChromaDB

    Returns:
        dict mapping action name to list of hours
    """
    # defaultdict(list) means — if key doesn't exist yet,
    # create it automatically with an empty list
    # No need to check if key exists before appending
    grouped = defaultdict(list)

    for meta in interactions:
        action = meta.get("action")
        ts_str = meta.get("ts")

        try:
            # Parse the ISO timestamp string back to a datetime object
            # e.g. "2026-04-26T09:15:00+00:00" → datetime object
            ts = datetime.fromisoformat(ts_str)

            # Extract just the hour — 0 to 23
            hour = ts.hour

            # Add this hour to the list for this action
            grouped[action].append(hour)

        except Exception:
            # Skip interactions with unparseable timestamps
            continue

    return dict(grouped)

def _find_habits(grouped: dict) -> list[dict]:
    """
    Analyse grouped interactions and identify habits.

    A habit is when the same action happens at least
    MIN_OCCURRENCES times within a similar time window.

    Example:
        "read_email" at hours [9, 9, 8, 9, 10]
        → 4 out of 5 times within 1 hour of 9am
        → habit: "You usually check email around 9am"

    Args:
        grouped: dict from _group_by_action()

    Returns:
        list of habit dicts with keys:
          action, typical_hour, occurrences, description
    """
    habits = []

    for action, hours in grouped.items():
        # Not enough data — skip
        if len(hours) < MIN_OCCURRENCES:
            continue

        # Count how many times each hour appears
        # e.g. [9, 9, 8, 9, 10] → {9: 3, 8: 1, 10: 1}
        hour_counts = defaultdict(int)
        for h in hours:
            hour_counts[h] += 1

        # Find the most common hour
        peak_hour = max(hour_counts, key=lambda h: hour_counts[h])

        # Count occurrences within TIME_WINDOW_HOURS of peak hour
        window_count = sum(
            1 for h in hours
            if abs(h - peak_hour) <= TIME_WINDOW_HOURS
        )

        # Only flag as habit if enough occurrences in the window
        if window_count >= MIN_OCCURRENCES:
            habits.append({
                "action":       action,
                "typical_hour": peak_hour,
                "occurrences":  window_count,
                "description":  (
                    f"You usually {action.replace('_', ' ')} "
                    f"around {peak_hour}:00. "
                    f"({window_count} times recorded)"
                ),
            })

    return habits

def get_current_habits() -> list[dict]:
    """
    Main public function — returns habits relevant right now.

    Fetches all interactions, finds habits, then filters
    to only those relevant to the current hour.

    Called by:
      - morning briefing (to suggest what Ghost should cover)
      - orchestrator (to add proactive suggestions to responses)

    Returns:
        list of habit dicts relevant to the current time
        empty list if no habits or no data yet
    """
    try:
        interactions = _fetch_all_interactions()

        if not interactions:
            logger.info("No interactions stored yet — skipping pattern analysis.")
            return []

        grouped  = _group_by_action(interactions)
        habits   = _find_habits(grouped)

        # Filter to habits relevant to current hour
        current_hour = datetime.now(timezone.utc).hour

        relevant = [
            h for h in habits
            if abs(h["typical_hour"] - current_hour) <= TIME_WINDOW_HOURS
        ]

        if relevant:
            logger.info(
                "Found %d relevant habit(s) for hour %d",
                len(relevant), current_hour
            )

        return relevant

    except Exception as e:
        logger.error("Pattern learner failed: %s", e)
        return []
