"""
scripts/publisher.py — Manual MQTT alert publisher for testing.

Run this directly to publish a test alert to the broker
and verify the subscriber receives it.

Usage:
    python scripts/publisher.py
"""

import json
import sys
import os

# Add project root to path so we can import config and publisher
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transport.mqtt_publisher import publish_alert


def main():
    # Build a fake alert dict — same structure as Alert.to_dict()
    test_alert = {
        "type":      "alert",
        "level":     "warning",
        "device_id": "thinkpad",
        "metric":    "disk",
        "value":     87.5,
        "threshold": 85.0,
        "message":   "thinkpad disk at 87.5%. Getting full — consider cleaning up.",
        "ts":        "2026-04-26T00:00:00+00:00",
    }

    print("Publishing test alert to ghost/alerts...")
    publish_alert(test_alert)
    print("Done. Check your subscriber terminal for the message.")


if __name__ == "__main__":
    main()
