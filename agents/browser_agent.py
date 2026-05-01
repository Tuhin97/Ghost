"""
agents/browser_agent.py — Web search via DuckDuckGo.

Searches the web and returns a summarised answer via Claude.

Flow:
  1. Search DuckDuckGo for top 5 results
  2. Extract title + snippet + URL from each result
  3. Pass results to Claude to summarise into one short answer
  4. Return summary to orchestrator

No API key needed — DuckDuckGo is free and privacy-friendly.
"""

import logging

from ddgs import DDGS

import config
from persona import sanitise_for_llm

logger = logging.getLogger(__name__)

# How many results to fetch — more results = better summary but more tokens
MAX_RESULTS = 5


def search(query: str) -> str:
    """
    Search DuckDuckGo and return top results as formatted text.

    Args:
        query: search query string

    Returns:
        formatted string of top results — title, snippet, URL
    """
    logger.info("Searching DuckDuckGo for: %s", query)

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=MAX_RESULTS))

        if not results:
            return "No results found."

        # Format each result into a readable block
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. {r['title']}\n"
                f"   {r['body']}\n"
                f"   {r['href']}\n"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error("DuckDuckGo search failed: %s", e)
        return f"Search failed: {e}"


def search_and_summarise(query: str, claude_client, model: str) -> str:
    """
    Search DuckDuckGo and summarise results using Claude.

    Args:
        query:         search query
        claude_client: Anthropic client instance from orchestrator
        model:         which Claude model to use

    Returns:
        short natural language summary of the search results
    """
    # Step 1 — get raw search results
    raw_results = search(query)

    # Step 2 — wrap in sanitise_for_llm — this is external web content
    safe_results = sanitise_for_llm(raw_results, source="DuckDuckGo")

    # Step 3 — ask Claude to summarise
    prompt = (
        f"The user searched for: {query}\n\n"
        f"Here are the top search results:\n\n"
        f"{safe_results}\n\n"
        f"Summarise the key information in 2-3 sentences. "
        f"Be direct and concise. Include the most relevant facts. "
        f"Do not mention that you are summarising search results."
    )

    try:
        response = claude_client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    except Exception as e:
        logger.error("Claude summarisation failed: %s", e)
        # Fall back to raw results if Claude fails
        return raw_results
