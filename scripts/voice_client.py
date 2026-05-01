"""
scripts/voice_client.py — Local voice client for Ghost.

Push to talk voice interface:
  1. Press Enter to start speaking
  2. Speak your command
  3. Press Enter to stop
  4. Ghost processes and speaks back

Run:
    venv/bin/python3 scripts/voice_client.py
"""

import os
import re
import sys

import requests
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice.stt import push_to_talk
from voice.tts import speak
from agents.email_agent import read_emails, format_emails_for_voice

load_dotenv()

GHOST_URL = "http://localhost:8000/command"
TOKEN     = os.getenv("DEVICE_TOKEN_THINKPAD")

# Stores last N conversation turns so Ghost understands follow-on commands
# Each entry is a tuple of (user_command, ghost_response)
conversation_history = []
MAX_HISTORY_TURNS = 4

def _normalize_voice_text(text: str) -> str:
    """
    Fix common voice transcription artifacts before sending to Ghost.

    faster-whisper transcribes spoken email addresses like:
      "shadowkid020 at gmail dot com"
    but Ghost needs:
      "shadowkid020@gmail.com"
    """
    text = re.sub(r'\s+at\s+', '@', text)
    text = re.sub(r'\s+dot\s+', '.', text)
    return text


def send_to_ghost(text: str, dry_run: bool = False) -> dict:
    """Send transcribed text to Ghost. Returns full response dict."""

    # Build history in Claude's alternating message format
    # Each past turn becomes a user message followed by an assistant message
    history_messages = []
    for user_cmd, ghost_resp in conversation_history[-MAX_HISTORY_TURNS:]:
        history_messages.append({"role": "user",      "content": user_cmd})
        history_messages.append({"role": "assistant", "content": ghost_resp})

    text = _normalize_voice_text(text)
    response = requests.post(
        GHOST_URL,
        json={"command": text, "dry_run": dry_run, "history": history_messages or None},
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=60,
    )
    return response.json()


def main():
    print("[Ghost Voice Client] Starting...")
    print("[Ghost Voice Client] Press Ctrl+C to exit\n")

    speak("Ghost voice client ready.")

    while True:
        try:
            text = push_to_talk()
            if not text:
                continue

            print(f"Sending to Ghost: {text}")

            # Dry run first — parse intent without executing
            data   = send_to_ghost(text, dry_run=True)
            action = data.get("action", "")

            # ── Send email — confirm before sending ───────────────────────────
            if action == "send_email":
                params  = data.get("params", {})
                to      = params.get("to", "")
                subject = params.get("subject", "no subject")

                while True:
                    speak(
                        f"I'll send an email to {to}, subject: {subject}. "
                        f"Say yes to confirm, no to cancel, or correct to change the address."
                    )

                    confirmation = push_to_talk()
                    if not confirmation:
                        continue

                    conf = confirmation.lower()

                    if any(w in conf for w in ["yes", "yeah", "send", "confirm", "go ahead", "do it"]):
                        # Execute — send the original command for real this time
                        result = send_to_ghost(text, dry_run=False)
                        reply  = result.get("response", "Email sent.")
                        print(f"Ghost says: {reply}\n")
                        conversation_history.append((text, reply))
                        speak(reply)
                        break

                    elif any(w in conf for w in ["no", "cancel", "stop", "abort"]):
                        speak("Email cancelled.")
                        break

                    elif any(w in conf for w in ["correct", "change", "wrong", "fix"]):
                        speak("What's the correct email address? Type it and press Enter, or just press Enter to speak it.")
                        typed = input("Type address (or Enter to speak): ").strip()
                        if typed:
                            to = typed
                        else:
                            spoken = push_to_talk()
                            if spoken:
                                to = _normalize_voice_text(spoken).strip()

                        # Rebuild the command with the corrected address so Ghost
                        # re-parses it correctly when dry_run=False
                        text = f"send email to {to} subject {subject}"
                        speak(f"Got it. Using {to}.")
                        # Loop back to confirmation

                    else:
                        speak("Say yes to send, no to cancel, or correct to change the address.")

            # ── Read email — voice format ─────────────────────────────────────
            elif action == "read_email":
                try:
                    emails = read_emails(10)
                    reply  = format_emails_for_voice(emails)
                except Exception as e:
                    reply = f"Couldn't read your emails. {e}"
                print(f"Ghost says: {reply}\n")
                conversation_history.append((text, reply))
                speak(reply)

            # ── Everything else — execute normally ────────────────────────────
            else:
                data  = send_to_ghost(text, dry_run=False)
                reply = data.get("response", "")
                print(f"Ghost says: {reply}\n")
                conversation_history.append((text, reply))
                speak(reply)

        except KeyboardInterrupt:
            print("\n[Ghost] Voice client stopped.")
            break
        except Exception as e:
            print(f"[Ghost] Error: {e}")
            continue


if __name__ == "__main__":
    main()
