"""
transport/mqtt_publisher.py — MQTT message publisher.

Publishes alerts and responses to the Mosquitto broker.
Other devices subscribed to these topics receive them
even if they weren't connected when the message was sent.

Topics:
  ghost/alerts    — system health alerts
  ghost/responses — Ghost replies to commands
"""

import json
import logging

import paho.mqtt.client as mqtt

import config

logger = logging.getLogger(__name__)

# Topic constants — defined once, used everywhere
# Avoids typos like "ghost/allerts"
TOPIC_ALERTS    = "ghost/alerts"
TOPIC_RESPONSES = "ghost/responses"


def _get_client() -> mqtt.Client:
    """
    Create and connect an MQTT client.

    Called fresh each time we publish — keeps it simple.
    For high-frequency publishing you'd keep a persistent
    connection, but alerts are infrequent so this is fine.

    Returns:
        connected MQTT client
    """
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    # Only connect with credentials if configured
    if config.MQTT_USER and config.MQTT_PASSWORD:
        client.username_pw_set(config.MQTT_USER, config.MQTT_PASSWORD)

    client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    return client


def publish_alert(alert_dict: dict) -> None:
    """
    Publish a system alert to ghost/alerts topic.

    Args:
        alert_dict: Alert.to_dict() output from rules.py
    """
    try:
        client = _get_client()
        payload = json.dumps(alert_dict)
        client.publish(TOPIC_ALERTS, payload, qos=1)
        client.disconnect()
        logger.info("Published alert: %s", alert_dict.get("message"))
    except Exception as e:
        logger.error("Failed to publish alert: %s", e)


def publish_response(device_id: str, response: str) -> None:
    """
    Publish a Ghost response to ghost/responses topic.

    Args:
        device_id: which device this response is for
        response:  Ghost's reply text
    """
    try:
        client = _get_client()
        payload = json.dumps({
            "device_id": device_id,
            "response":  response,
        })
        client.publish(TOPIC_RESPONSES, payload, qos=1)
        client.disconnect()
        logger.info("Published response for %s", device_id)
    except Exception as e:
        logger.error("Failed to publish response: %s", e)
