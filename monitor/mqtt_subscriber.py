"""
monitor/mqtt_subscriber.py — MQTT message subscriber.

Listens to the Mosquitto broker and forwards incoming
alerts to the WebSocket manager for real-time delivery
to connected devices.

Runs as a background asyncio task alongside the poller.

Topics subscribed:
  ghost/alerts — system health alerts from poller
"""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

import config
from transport.mqtt_publisher import TOPIC_ALERTS

logger = logging.getLogger(__name__)


def _on_connect(client, userdata, flags, reason_code, properties):
    """
    Called automatically by paho when connection to broker succeeds.

    We subscribe to topics here — not before — because if the
    connection drops and reconnects, paho calls on_connect again
    and re-subscribes automatically.

    Args:
        client:      the MQTT client instance
        userdata:    custom data we passed in (ws_manager)
        flags:       connection flags from broker
        reason_code: 0 means success
        properties:  MQTT v5 properties (unused here)
    """
    if reason_code == 0:
        logger.info("MQTT subscriber connected to broker.")
        # Subscribe to alerts topic
        # qos=1 — at least once delivery
        client.subscribe(TOPIC_ALERTS, qos=1)
    else:
        logger.error("MQTT subscriber failed to connect. Code: %s", reason_code)


def _on_message(client, userdata, message):
    """
    Called automatically by paho when a message arrives.

    userdata is the ws_manager — we passed it in when
    creating the client so we can broadcast from here.

    Args:
        client:   the MQTT client instance
        userdata: ws_manager passed in at client creation
        message:  the incoming MQTT message object
    """
    try:
        # Decode bytes to string
        payload = message.payload.decode("utf-8")
        logger.info("MQTT received on %s: %s", message.topic, payload)

        # Get the ws_manager we stored in userdata
        ws_manager = userdata

        # Schedule the async broadcast on the event loop
        # _on_message runs in paho's thread — not the asyncio thread
        # asyncio.run_coroutine_threadsafe() safely bridges the two
        loop = ws_manager.loop
        asyncio.run_coroutine_threadsafe(
            ws_manager.broadcast(payload),
            loop
        )

    except Exception as e:
        logger.error("Error handling MQTT message: %s", e)


async def start_subscriber(ws_manager) -> None:
    """
    Start the MQTT subscriber as a background task.

    Connects to the broker, registers callbacks, and runs
    the paho network loop in a thread so it doesn't block
    the asyncio event loop.

    Args:
        ws_manager: WebSocketManager — used to broadcast alerts
    """
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        userdata=ws_manager,   # passed to every callback as userdata
    )

    # Register callbacks
    client.on_connect = _on_connect
    client.on_message = _on_message

    if config.MQTT_USER and config.MQTT_PASSWORD:
        client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)

    try:
        client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    except Exception as e:
        logger.error("Could not connect MQTT subscriber to broker: %s", e)
        return

    # loop_start() runs paho's network loop in a background thread
    # This is non-blocking — asyncio event loop stays free
    client.loop_start()

    logger.info("MQTT subscriber started. Listening on %s", TOPIC_ALERTS)

    # Keep this coroutine alive for the lifetime of the server
    # asyncio.sleep(3600) = sleep 1 hour, then loop again forever
    while True:
        await asyncio.sleep(3600)
