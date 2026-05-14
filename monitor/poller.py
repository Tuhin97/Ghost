"""
monitor/poller.py — Async health poller.

Runs every 30 seconds in the background.
Collects real metrics from each device and fires alerts
through the WebSocket manager if thresholds are crossed.

Flow:
  1. poll_thinkpad()  — reads psutil locally (no SSH needed)
  2. poll_mac()       — SSHs in and runs psutil remotely
  3. check_all()      — runs rules engine on the numbers
  4. broadcast()      — sends any alerts to all connected devices
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import paramiko
import psutil

import config
from monitor.rules import check_all, check_device_offline

logger = logging.getLogger(__name__)


# Tracks when we last got a successful response from each device.
# Key: device_id string  Value: datetime (UTC)
# Used to detect offline devices.
_last_seen: dict[str, datetime] = {}


def _update_last_seen(device_id: str) -> None:
    """
    Record that we just heard from a device.

    Args:
        device_id: which device just responded
    """
    _last_seen[device_id] = datetime.now(timezone.utc)


def _get_local_metrics() -> dict:
    """
    Read CPU, RAM, and disk metrics from the local machine.

    psutil is a cross-platform library that talks directly
    to the OS kernel — no shell commands needed.

    Returns:
        dict with keys: cpu_pct, ram_pct, disk_pct
    """
    return {
        # interval=1 means measure CPU over 1 second window
        # without interval it returns 0.0 (meaningless snapshot)
        "cpu_pct":  psutil.cpu_percent(interval=1),

        # virtual_memory() returns named tuple with .percent
        "ram_pct":  psutil.virtual_memory().percent,

        # disk_usage("/") checks the root filesystem
        # .percent is already a float like 73.4
        "disk_pct": psutil.disk_usage("/").percent,
    }


def _get_remote_metrics(host: str, user: str) -> Optional[dict]:
    """
    SSH into a remote machine and collect its metrics via psutil.

    We run a one-liner Python command on the remote machine.
    The remote machine must have psutil installed.

    Args:
        host: IP or hostname of remote machine
        user: SSH username

    Returns:
        dict with cpu_pct, ram_pct, disk_pct — or None if SSH fails
    """
    # This is the command we'll run on the remote machine
    # json.dumps makes the output easy to parse back here
    remote_cmd = (
        "/opt/anaconda3/bin/python3 -c \""
        "import psutil, json; "
        "import platform; "
        "print(json.dumps({"
        "'cpu_pct': psutil.cpu_percent(interval=1), "
        "'ram_pct': psutil.virtual_memory().percent, "
        "'disk_pct': psutil.disk_usage('/').percent, "
        "'os': platform.platform()"
        "}))\""
    )

    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    key_path = config.SSH_KEY_PATH.replace("~", f"/home/{config.LINUX_USER}")

    try:
        client.connect(
            hostname=host,
            username=user,
            key_filename=key_path,
            timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )

        _, stdout, _ = client.exec_command(remote_cmd, timeout=20)
        output = stdout.read().decode(errors="replace").strip()

        # Parse the JSON the remote python3 printed
        return json.loads(output)

    except Exception as e:
        logger.warning("Could not reach %s@%s: %s", user, host, e)
        return None

    finally:
        client.close()


async def _poll_once(ws_manager) -> None:
    """
    Run one full poll cycle across all devices.

    Collects metrics, runs rules, broadcasts alerts.
    Called every 30 seconds by the poll loop.

    Args:
        ws_manager: the WebSocketManager instance from orchestrator
                    needed to broadcast alerts to connected clients
    """
    all_alerts = []

    # --- ThinkPad (local) ---
    # asyncio.to_thread() runs blocking code in a thread pool
    # psutil.cpu_percent(interval=1) blocks for 1 second
    # We can't block the async event loop — to_thread() prevents that
    metrics = await asyncio.to_thread(_get_local_metrics)
    _update_last_seen("thinkpad")

    alerts = check_all(
        device_id="thinkpad",
        disk_pct=metrics["disk_pct"],
        cpu_pct=metrics["cpu_pct"],
        ram_pct=metrics["ram_pct"],
    )
    all_alerts.extend(alerts)

    # --- MacBook (remote via SSH) ---
    if config.MAC_HOST and config.MAC_USER:
        mac_metrics = await asyncio.to_thread(
            _get_remote_metrics, config.MAC_HOST, config.MAC_USER
        )

        if mac_metrics is not None:
            _update_last_seen("macbook")
            alerts = check_all(
                device_id="macbook",
                disk_pct=mac_metrics["disk_pct"],
                cpu_pct=mac_metrics["cpu_pct"],
                ram_pct=mac_metrics["ram_pct"],
            )
            all_alerts.extend(alerts)
        else:
            # SSH failed — check if it has been offline too long
            if "macbook" in _last_seen:
                offline_alert = check_device_offline("macbook", _last_seen["macbook"])
                if offline_alert:
                    all_alerts.append(offline_alert)

    # --- Broadcast all alerts ---
    for alert in all_alerts:
        logger.info("ALERT: %s", alert.message)
        await ws_manager.broadcast(json.dumps(alert.to_dict()))


async def start_poller(ws_manager) -> None:
    """
    Infinite loop that polls all devices every 30 seconds.

    Started once at server startup via FastAPI lifespan.
    Runs for the lifetime of the server.

    Args:
        ws_manager: WebSocketManager — passed in from orchestrator
    """
    logger.info("Health poller started. Polling every %ss.", config.POLL_INTERVAL_SECONDS)

    while True:
        try:
            await _poll_once(ws_manager)
        except Exception as e:
            # Never let a crash kill the poller
            # Log it and keep going
            logger.error("Poller cycle failed: %s", e)

        # Wait before next cycle
        # asyncio.sleep() yields control back to the event loop
        # so other requests can be handled while we wait
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
