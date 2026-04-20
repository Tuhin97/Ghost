"""
transport/websocket_manager.py — Manage WebSocket connections.

Tracks all active connections across devices so Ghost can:
  1. Send messages to a specific device
  2. Broadcast alerts to all connected devices simultaneously

This is what enables proactive alerts — Ghost can push messages
to your phone without you asking anything first.
"""

from fastapi import WebSocket


class WebSocketManager:
    """
    Manages all active WebSocket connections.

    Stores connections in a dictionary:
      device_id → WebSocket object

    Example:
      {
        "samsung":  <WebSocket>,
        "macbook":  <WebSocket>,
        "thinkpad": <WebSocket>,
      }
    """

    def __init__(self):
        # Dictionary mapping device_id to its WebSocket connection
        # Starts empty — connections are added as devices connect
        self._connections: dict = {}

    async def connect(self, websocket: WebSocket, device_id: str):
        """
        Accept a new WebSocket connection and register it.

        Called when a device connects to /ws/{device_id}.
        Must call websocket.accept() before any communication.

        Args:
            websocket:  the WebSocket object from FastAPI
            device_id:  which device is connecting ("samsung" etc)
        """
        # Accept the connection — without this the handshake fails
        # and the connection is immediately closed
        await websocket.accept()

        # Store it — overwrite if device reconnects
        self._connections[device_id] = websocket

        print(f"[Ghost] {device_id} connected via WebSocket")

    def disconnect(self, device_id: str):
        """
        Remove a device's connection when it disconnects.

        Called when a WebSocket connection closes — either the
        device disconnected intentionally or the connection dropped.

        Args:
            device_id: which device disconnected
        """
        self._connections.pop(device_id, None)
        print(f"[Ghost] {device_id} disconnected")

    async def send(self, device_id: str, message: dict):
        """
        Send a message to one specific device.

        Used when a response is only relevant to one device —
        e.g. the result of a command that device sent.

        Args:
            device_id: which device to send to
            message:   dictionary that gets sent as JSON

        Example:
            await ws_manager.send("samsung", {
                "response": "Found 2 files on your Mac",
                "action": "search_file"
            })
        """
        websocket = self._connections.get(device_id)

        if websocket:
            try:
                await websocket.send_json(message)
            except Exception:
                # Connection dropped unexpectedly — clean it up
                self.disconnect(device_id)

    async def broadcast(self, message: dict):
        """
        Send a message to ALL connected devices simultaneously.

        Used for system-wide alerts that every device should see —
        e.g. disk warning, device offline, CI failure.

        Args:
            message: dictionary that gets sent as JSON to everyone

        Example:
            await ws_manager.broadcast({
                "type": "alert",
                "level": "warning",
                "message": "Mac disk at 87%"
            })
        """
        # Track failed connections to clean up after the loop
        # We can't disconnect during iteration — that would modify
        # the dictionary while we're looping through it
        disconnected = []

        for device_id, websocket in self._connections.items():
            try:
                await websocket.send_json(message)
            except Exception:
                # Mark for removal — don't remove yet
                disconnected.append(device_id)

        # Now safe to remove failed connections
        for device_id in disconnected:
            self.disconnect(device_id)

    @property
    def connected_devices(self) -> list:
        """
        Return list of currently connected device IDs.

        @property means you access it like an attribute, not a method:
            ws_manager.connected_devices    ← no brackets
        not:
            ws_manager.connected_devices()  ← don't do this

        Used by the dashboard to show which devices are online.
        """
        return list(self._connections.keys())

    @property
    def connection_count(self) -> int:
        """Return how many devices are currently connected."""
        return len(self._connections)
