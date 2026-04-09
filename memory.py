"""
memory.py — ChromaDB vector memory with Fernet encryption.

Every interaction is stored with timestamp + device_id.
Before each response, top-N relevant memories are retrieved
and injected into the prompt so Ghost has context from past sessions.

Encryption: all stored content is encrypted with Fernet before
writing to ChromaDB. The key lives in .env — never on disk
next to the data.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import chromadb
from cryptography.fernet import Fernet

import config


class GhostMemory:
    """
    Manages Ghost's persistent memory using ChromaDB + Fernet encryption.

    ChromaDB stores vectors (embeddings) for semantic search.
    The actual content is encrypted before storage.
    """

    def __init__(self):
        # Create the Fernet cipher using the key from .env
        self._fernet = Fernet(config.MEMORY_ENCRYPTION_KEY.encode())

        # Connect to ChromaDB — PersistentClient means data survives restarts
        # It stores everything in the data/ghost_memory folder
        self._client = chromadb.PersistentClient(
            path=str(config.MEMORY_DIR)
        )

        # Get or create the collection — like a table in a normal database
        # This is where all interactions are stored
        self._collection = self._client.get_or_create_collection(
            name="ghost_interactions",
            metadata={"description": "Ghost AI interaction history"}
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _encrypt(self, text: str) -> str:
        """
        Encrypt a string using Fernet.
        Returns encrypted bytes converted to a string for storage.
        """
        return self._fernet.encrypt(text.encode()).decode()

    def _decrypt(self, token: str) -> str:
        """
        Decrypt a Fernet-encrypted string.
        Returns the original plain text.
        """
        return self._fernet.decrypt(token.encode()).decode()

    def _make_id(self) -> str:
        """
        Generate a unique ID for each memory entry.
        Uses timestamp down to microseconds — effectively unique.
        Format: 20240115_090000_123456
        """
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    # ── Public API ────────────────────────────────────────────────────────────

    def store(
        self,
        user_input: str,
        ghost_response: str,
        device_id: str,
        action: Optional[str] = None,
    ) -> None:
        """
        Store an interaction in memory.

        Two things are saved:
          1. The raw user input as the document — ChromaDB embeds this
             for semantic search. This is what gets searched later.
          2. The full interaction (input + response) encrypted as metadata.
             This is what gets decrypted and shown when a memory is recalled.

        Args:
            user_input:     what the user said
            ghost_response: what Ghost replied
            device_id:      which device sent the command
            action:         which agent handled it (e.g. "search_file")
        """
        now = datetime.now(timezone.utc)

        # Build the full interaction as a JSON string
        full_content = json.dumps({
            "user":      user_input,
            "ghost":     ghost_response,
            "device":    device_id,
            "action":    action,
            "timestamp": now.isoformat(),
        })

        # Store in ChromaDB
        # documents= is what ChromaDB embeds for semantic search
        # metadatas= is extra data stored alongside the embedding
        self._collection.add(
            ids=[self._make_id()],
            documents=[user_input],
            metadatas=[{
                "encrypted": self._encrypt(full_content),
                "device_id": device_id,
                "action":    action or "unknown",
                "timestamp": now.isoformat(),
            }]
        )

    def recall(self, query: str, n: int = None) -> str:
        """
        Find the top-N most relevant past interactions for a query.
        Returns a formatted string ready to inject into the prompt.

        ChromaDB converts the query to an embedding and finds the
        stored interactions with the most similar embeddings.

        Args:
            query: the current user command to find relevant memories for
            n:     how many memories to return (defaults to config value)

        Returns:
            formatted string of past interactions, or "" if none exist
        """
        n = n or config.MEMORY_RECALL_COUNT

        # Don't search if nothing is stored yet
        count = self._collection.count()
        if count == 0:
            return ""

        # Can't return more than what's stored
        results = self._collection.query(
            query_texts=[query],
            n_results=min(n, count),
            include=["metadatas"]
        )

        memories = []
        for metadata in results["metadatas"][0]:
            try:
                # Decrypt the full interaction
                data = json.loads(self._decrypt(metadata["encrypted"]))

                # Format timestamp — just date and time, no milliseconds
                ts = data.get("timestamp", "")[:16].replace("T", " ")

                memories.append(
                    f"[{ts}] You asked: {data['user']}\n"
                    f"Ghost replied: {data['ghost']}"
                )
            except Exception:
                # Skip corrupted or unreadable entries silently
                continue

        return "\n\n".join(memories)

    def count(self) -> int:
        """Return total number of stored memories."""
        return self._collection.count()


# ── Module-level singleton ────────────────────────────────────────────────────

# One shared instance for the whole application
# Lazy loaded — only created when first needed
_memory: Optional[GhostMemory] = None


def get_memory() -> GhostMemory:
    """
    Return the shared GhostMemory instance.
    Creates it on first call, reuses it on every call after.
    This pattern is called a singleton.
    """
    global _memory
    if _memory is None:
        _memory = GhostMemory()
    return _memory
