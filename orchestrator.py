"""
orchestrator.py — Ghost AI main server.

Every command flows through here:
  1. JWT authentication
  2. Rate limiting
  3. Memory recall from ChromaDB
  4. Claude intent parsing → structured JSON action
  5. Agent dispatch
  6. Memory storage
  7. Response back to device

Endpoints:
  GET  /health              — liveness check, no auth needed
  POST /command             — send a command, get a response
  WS   /ws/{device_id}     — persistent WebSocket connection
  GET  /dashboard           — live dashboard UI
"""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import anthropic
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import config
from auth import verify_token
from memory import get_memory
from persona import build_messages, get_system_prompt_with_cache
from transport.websocket_manager import WebSocketManager
from agents.file_agent import search_files, format_results
from agents.browser_agent import search_and_summarise
from agents.mac_agent import find_file, open_app, open_url, list_files, get_running_apps, get_mac_stats
from agents.email_agent import read_emails, send_email, format_emails_for_display
from agents.calendar_agent import get_events, create_event
from monitor.poller import start_poller
from monitor.mqtt_subscriber import start_subscriber
from monitor.briefing import start_briefing


# ── Rate limiter ──────────────────────────────────────────────────────────────
# get_remote_address extracts the client's IP from the request
# Used as the key for rate limiting — 30 requests/minute per IP
limiter = Limiter(key_func=get_remote_address)


# ── App startup and shutdown ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup and once on shutdown.

    @asynccontextmanager splits the function at the yield:
      Everything before yield → runs on startup
      Everything after yield  → runs on shutdown

    This is FastAPI's modern replacement for @app.on_event("startup")
    """
    # Startup
    _init_api_db()
    print(f"[Ghost] Online — {config.SERVER_HOST}:{config.SERVER_PORT}")
    print(f"[Ghost] Memory: {get_memory().count()} stored interactions")

    # Store the event loop on ws_manager so paho's thread can
    # schedule async broadcasts via asyncio.run_coroutine_threadsafe()
    ws_manager.loop = asyncio.get_running_loop()

    # Start health poller as a background task
    # asyncio.create_task() launches it concurrently — it doesn't block startup
    # The poller runs alongside the server for its entire lifetime
    poller_task = asyncio.create_task(start_poller(ws_manager))

    # Start MQTT subscriber as a background task
    # Listens to ghost/alerts and forwards to WebSocket
    subscriber_task = asyncio.create_task(start_subscriber(ws_manager))

    # Start morning briefing scheduler as a background task
    # Checks every 60 seconds if it's time to send the briefing
    briefing_task = asyncio.create_task(start_briefing(ws_manager))


    yield  # Server runs here — between startup and shutdown

    # Shutdown — cancel both background tasks cleanly
    poller_task.cancel()
    subscriber_task.cancel()
    briefing_task.cancel()
    print("[Ghost] Shutting down.")


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ghost AI Orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)

# Attach rate limiter to app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Serve static files (app.js) at /static
app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")

# Jinja2 templates for dashboard HTML
templates = Jinja2Templates(directory="dashboard/templates")

# Shared WebSocket manager — one instance for the whole app
ws_manager = WebSocketManager()

# Anthropic client — one instance for the whole app
claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ── API usage tracking ────────────────────────────────────────────────────────
def _init_api_db():
    """
    Create the API usage SQLite database if it doesn't exist.

    SQLite is a file-based database — no server needed.
    The database file lives at data/api_usage.db
    We use it to track token usage and costs.
    """
    with sqlite3.connect(config.API_USAGE_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT    NOT NULL,
                device_id     TEXT,
                model         TEXT,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                cached_tokens INTEGER DEFAULT 0,
                action        TEXT
            )
        """)


def _log_usage(
    device_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    action: str = "",
):
    """
    Log one API call's token usage to SQLite.

    Called after every Claude API call.
    Dashboard reads this table to show cost tracking.
    """
    with sqlite3.connect(config.API_USAGE_DB) as conn:
        conn.execute(
            """INSERT INTO api_usage
               (ts, device_id, model, input_tokens, output_tokens, cached_tokens, action)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now(timezone.utc).isoformat(),
                device_id, model,
                input_tokens, output_tokens, cached_tokens,
                action,
            ),
        )


# ── Claude intent parsing ─────────────────────────────────────────────────────
def _call_claude(
    user_input: str,
    device_id: str,
    memory_context: str = "",
    model: str = config.MODEL_SMART,
    history: list = None,
) -> dict:
    """
    Send a command to Claude and get back a structured JSON action.

    Flow:
      1. Build messages list with memory context injected
      2. Call Claude API with cached system prompt
      3. Log token usage
      4. Parse JSON response
      5. Return action dict

    Args:
        user_input:     the command from the user
        device_id:      which device sent it (for usage logging)
        memory_context: relevant past interactions from ChromaDB

    Returns:
        dict with keys: action, params, response
    """
    messages = build_messages(user_input, memory_context, history)
    system   = get_system_prompt_with_cache()

    api_response = claude.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=messages,
    )

    # Log token usage — track costs over time
    usage = api_response.usage
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    _log_usage(
        device_id=device_id,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=cached,
        action="intent_parse",
    )

    # Extract the text response from Claude
    raw = api_response.content[0].text.strip()

    # Parse JSON — Claude should always return valid JSON
    # but sometimes wraps it in markdown code blocks
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Strip markdown code fences if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Last resort — return as unknown action
            return {
                "action": "unknown",
                "params": {"raw": raw},
                "response": raw
            }


# ── Agent dispatcher ──────────────────────────────────────────────────────────
async def _dispatch(action: dict, device_id: str) -> str:
    """
    Route a parsed action to the correct agent.

    Reads action["action"] and calls the appropriate agent.
    Returns a string response to send back to the user.

    As we build more agents in future weekends, we add
    more elif branches here — nothing else changes.

    Args:
        action:    dict with action, params, response from Claude
        device_id: which device sent the command

    Returns:
        string to send back to the user
    """
    action_name = action.get("action", "unknown")
    params      = action.get("params", {})
    ghost_reply = action.get("response", "")

    # ── search_file ───────────────────────────────────────────────────────────
    if action_name == "search_file":
        query     = params.get("query", "")
        devices   = params.get("devices", ["mac", "linux"])
        path_hint = params.get("path_hint", None)

        try:
            results      = search_files(query, devices, path_hint)
            file_summary = format_results(results)
            return f"{ghost_reply}\n\n{file_summary}"
        except ValueError as e:
            return f"Couldn't search: {e}"
        except Exception as e:
            return f"File search error: {e}"

    # ── device_status ─────────────────────────────────────────────────────────
    elif action_name == "device_status":
        return ghost_reply or "Device monitoring coming in Weekend 7."

    # ── unknown ───────────────────────────────────────────────────────────────
    elif action_name == "unknown":
        # Haiku couldn't map this to an action — it's conversational
        # Call Sonnet for a proper thoughtful response
        smart_action = _call_claude(
            user_input=params.get("raw", ghost_reply),
            device_id=device_id,
            model=config.MODEL_SMART,
        )
        return smart_action.get("response", ghost_reply) or "I didn't catch that. Could you rephrase?"
    
    # ── read_email ────────────────────────────────────────────────────────────
    elif action_name == "read_email":
        count = params.get("count", 10)
        try:
            emails = read_emails(count)
            return format_emails_for_display(emails)
        except Exception as e:
            return f"Couldn't read emails: {e}"

    # ── send_email ────────────────────────────────────────────────────────────
    elif action_name == "send_email":
        to      = params.get("to", "")
        subject = params.get("subject", "")
        body    = params.get("body", "")
        attachment = params.get("attachment_path", None)

        if not to:
            return "I need an email address to send to."
        try:
            result = send_email(to, subject, body, attachment)
            return f"{ghost_reply}\n\nEmail sent to {result['to']} — subject: {result['subject']}"
        except ValueError as e:
            return f"Attachment blocked for security: {e}"
        except Exception as e:
            return f"Couldn't send email: {e}"

    # ── calendar_read ──────────────────────────────────────────────────────────
    elif action_name == "calendar_read":
        days = params.get("days", 7)
        try:
            events = get_events(days)
            return f"{ghost_reply}\n\n{events}"
        except Exception as e:
            return f"Couldn't fetch calendar: {e}"

    # ── calendar_add ─────────────────────────────────────────────────
    elif action_name == "calendar_add":
        title       = params.get("title", "")
        start       = params.get("start", "")
        end         = params.get("end", "")
        description = params.get("description", None)

        if not title or not start or not end:
            return "I need a title, start time and end time to create an event."
        try:
            result = create_event(title, start, end, description)
            return f"{ghost_reply}\n\n{result}"
        except Exception as e:
            return f"Couldn't create event: {e}"
        
    # ── web_search ────────────────────────────────────────────────────────────
    elif action_name == "web_search":
        query = params.get("query", "")
        try:
            return search_and_summarise(query, claude, config.MODEL_FAST)
        except Exception as e:
            return f"Search failed: {e}"
        
    # ── mac_command ───────────────────────────────────────────────────────────
    elif action_name == "mac_command":
        subaction  = params.get("subaction", "")
        filename   = params.get("filename", "")
        directory  = params.get("directory", "")
        app_name   = params.get("app_name", "")
        url        = params.get("url", "")

        if subaction == "find_file":
            return find_file(filename, directory)
        elif subaction == "open_app":
            return open_app(app_name)
        elif subaction == "open_url":
            return open_url(url)
        elif subaction == "list_files":
            return list_files(directory)
        elif subaction == "get_running_apps":
            return get_running_apps()
        elif subaction == "get_stats":
            return get_mac_stats()
        else:
            return "I didn't understand what you want to do on the Mac."

    # ── mac_stats ─────────────────────────────────────────────────────────────
    elif action_name == "mac_stats":
        try:
            return get_mac_stats()
        except Exception as e:
            return f"Couldn't get Mac stats: {e}"

    # ── not yet implemented ───────────────────────────────────────────────────
    else:
        return (
            f"{ghost_reply}\n\n"
            f"['{action_name}' agent not yet implemented — "
            f"check the build schedule.]"
        )


# ── Request/Response models ───────────────────────────────────────────────────
class CommandRequest(BaseModel):
    """
    Pydantic model for POST /command request body.

    Pydantic automatically:
      - Validates that command is present and is a string
      - Returns a 422 error if validation fails
      - Converts the JSON body to a Python object
    """
    command: str
    context: Optional[str] = None  # optional extra context from client
    dry_run: bool = False  # if true, execute actions with side effects
    history: Optional[list] = None  # optional conversation history for this command


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """
    Liveness check — no auth required.

    Used by:
      - You, to verify the server is running
      - The dashboard, to show server status
      - Future: monitoring tools, uptime checkers

    Returns basic server info and memory count.
    """
    return {
        "status":       "online",
        "ghost":        "ready",
        "memory_count": get_memory().count(),
        "ts":           datetime.now(timezone.utc).isoformat(),
    }


@app.post("/command")
@limiter.limit(config.RATE_LIMIT)
async def command(
    request: Request,
    body: CommandRequest,
    device_id: str = Depends(verify_token),
):
    """
    Main command endpoint.

    Requires: Authorization: Bearer <device_token> header

    Flow:
      1. verify_token runs first (via Depends) — 401 if invalid
      2. Rate limiter checks — 429 if over limit
      3. Memory recalled from ChromaDB
      4. Claude called for intent parsing
      5. Action dispatched to agent
      6. Interaction stored in memory
      7. Response returned

    Args:
        request:   FastAPI Request object (needed by rate limiter)
        body:      validated CommandRequest with command string
        device_id: injected by verify_token dependency
    """
    memory = get_memory()

    # Step 1 — recall relevant past interactions
    memory_context = memory.recall(body.command)

    # Step 2 — ask Claude what to do
    action = _call_claude(body.command, device_id, memory_context, history=body.history)

    if body.dry_run:
        return {
            "action":   action.get("action"),
            "params":   action.get("params", {}),
            "response": action.get("response", ""),
            "device":   device_id,
        }

    # Step 3 — dispatch to agent
    response_text = await _dispatch(action, device_id)

    # Step 4 — store this interaction
    memory.store(
        user_input=body.command,
        ghost_response=response_text,
        device_id=device_id,
        action=action.get("action"),
    )

    return {
        "response": response_text,
        "action":   action.get("action"),
        "device":   device_id,
    }


@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    """
    WebSocket endpoint for persistent connections.

    Protocol:
      1. Client connects to /ws/{device_id}
      2. Client sends: {"token": "<jwt>"}
      3. Server replies: {"status": "authenticated", "device": "samsung"}
      4. Client sends: {"command": "find my resume"}
      5. Server replies: {"response": "...", "action": "...", "done": true}
      6. Repeat steps 4-5 indefinitely

    Connection stays open until device disconnects.
    """
    await ws_manager.connect(websocket, device_id)
    authenticated = False
    memory = get_memory()

    try:
        while True:
            # Wait for next message from device
            data = await websocket.receive_json()

            # ── Auth handshake ────────────────────────────────────────────────
            if not authenticated:
                token = data.get("token", "")
                try:
                    from auth import decode_token
                    payload       = decode_token(token)
                    authenticated = True
                    actual_device = payload.get("sub", device_id)
                    await websocket.send_json({
                        "status": "authenticated",
                        "device": actual_device,
                    })
                except Exception:
                    await websocket.send_json({"error": "Invalid token"})
                    await websocket.close()
                    return
                continue

            # ── Process command ───────────────────────────────────────────────
            command_text = data.get("command", "").strip()
            if not command_text:
                continue

            # Same flow as HTTP /command
            memory_context = memory.recall(command_text)
            action         = _call_claude(command_text, device_id, memory_context)
            response_text  = await _dispatch(action, device_id)

            memory.store(
                user_input=command_text,
                ghost_response=response_text,
                device_id=device_id,
                action=action.get("action"),
            )

            await websocket.send_json({
                "response": response_text,
                "action":   action.get("action"),
                "done":     True,
            })

    except WebSocketDisconnect:
        ws_manager.disconnect(device_id)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Serve the live dashboard.

    No JWT auth — Tailscale provides network-level security.
    Only devices on your Tailscale network can reach this.
    """
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )
