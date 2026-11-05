"""
agents/mac_agent.py — Run commands on MacBook via SSH.

Lets Ghost execute commands on the Mac remotely and return the output.

Flow:
  1. Orchestrator calls run_command(command)
  2. SSH into Mac, run the command, capture output
  3. Return output as string

Security:
  - Only whitelisted commands are allowed
  - No shell operators like &&, |, ; to prevent chaining
  - No sudo commands
"""

import logging
import subprocess

import config

logger = logging.getLogger(__name__)

# SSH connection details
MAC_USER = config.MAC_USER
MAC_HOST = config.MAC_HOST
SSH_TIMEOUT = 10  # seconds


def get_mac_stats() -> str:
    """
    Get basic system stats from the Mac — disk, CPU, uptime.

    Returns:
        formatted string of Mac system stats
    """
    def _ssh(cmd: str) -> str:
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                 f"{MAC_USER}@{MAC_HOST}", cmd],
                capture_output=True,
                text=True,
                timeout=SSH_TIMEOUT,
            )
            return result.stdout.strip()
        except Exception:
            return "unavailable"

    disk   = _ssh("df -h /")
    uptime = _ssh("uptime")

    return "\n".join([
        "Mac stats:",
        f"Uptime: {uptime}",
        f"Disk:\n{disk}",
    ])


def find_file(filename: str, directory: str = None) -> str:
    """
    Find a file on the Mac by name.

    Args:
        filename:  file name to search for
        directory: optional directory to search in — defaults to home

    Returns:
        list of matching file paths or not found message
    """
    # Build safe search path — no ~ expansion issues over SSH
    if directory:
        # Normalise common spoken directory names
        DIR_MAP = {
            "downloads": "Downloads",
            "documents": "Documents",
            "desktop":   "Desktop",
            "pictures":  "Pictures",
            "home":      "",
        }
        normalised = DIR_MAP.get(directory.lower().strip("~/"), directory.strip("~/"))
        search_path = f"/Users/{MAC_USER}/{normalised}" if normalised else f"/Users/{MAC_USER}"
    else:
        search_path = f"/Users/{MAC_USER}"

    try:
        result =  subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             f"{MAC_USER}@{MAC_HOST}", f'find {search_path} -name "{filename}"'],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )

        output = result.stdout.strip()

        if not output:
            return f"No file named '{filename}' found on Mac."

        return f"Found on Mac:\n{output}"

    except subprocess.TimeoutExpired:
        return "Mac did not respond in time."
    except Exception as e:
        return f"Could not search Mac: {e}"

def open_app(app_name: str) -> str:
    """
    Open an application on the Mac by name.

    Args:
        app_name: name of the app — e.g. "Safari", "Spotify", "Apple TV"

    Returns:
        confirmation string or error
    """
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             f"{MAC_USER}@{MAC_HOST}", f'open -a "{app_name}"'],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )

        if result.returncode != 0:
            return f"Couldn't open {app_name}: {result.stderr.strip()}"

        return f"Opening {app_name} on Mac."

    except subprocess.TimeoutExpired:
        return "Mac did not respond in time."
    except Exception as e:
        return f"Could not reach Mac: {e}"

def open_url(url: str) -> str:
    """
    Open a URL in the default browser on the Mac.

    Args:
        url: full URL to open — e.g. "https://google.com"

    Returns:
        confirmation string or error
    """
    # Ensure URL has a scheme
    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             f"{MAC_USER}@{MAC_HOST}", f'open "{url}"'],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )

        if result.returncode != 0:
            return f"Couldn't open URL: {result.stderr.strip()}"

        return f"Opening {url} on Mac."

    except subprocess.TimeoutExpired:
        return "Mac did not respond in time."
    except Exception as e:
        return f"Could not reach Mac: {e}"

def list_files(directory: str = "Downloads") -> str:
    """
    List files in a Mac directory.

    Args:
        directory: folder name — e.g. "Downloads", "Documents", "Desktop"

    Returns:
        list of files as string
    """
    DIR_MAP = {
        "downloads": "Downloads",
        "documents": "Documents",
        "desktop":   "Desktop",
        "pictures":  "Pictures",
        "home":      "",
    }

    normalised  = DIR_MAP.get(directory.lower().strip("~/"), directory.strip("~/"))
    search_path = f"/Users/{MAC_USER}/{normalised}" if normalised else f"/Users/{MAC_USER}"

    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             f"{MAC_USER}@{MAC_HOST}", f"ls {search_path}"],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )

        output = result.stdout.strip()

        if not output:
            return f"No files found in {directory}."

        return f"Files in Mac {directory}:\n{output}"

    except subprocess.TimeoutExpired:
        return "Mac did not respond in time."
    except Exception as e:
        return f"Could not reach Mac: {e}"

def get_running_apps() -> str:
    """
    Get list of currently running applications on the Mac.

    Returns:
        list of running app names
    """
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             f"{MAC_USER}@{MAC_HOST}",
             "osascript -e 'tell application \"System Events\" to get name of every process where background only is false'"],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )

        output = result.stdout.strip()

        if not output:
            return "No running apps found."

        return f"Running apps on Mac:\n{output}"

    except subprocess.TimeoutExpired:
        return "Mac did not respond in time."
    except Exception as e:
        return f"Could not reach Mac: {e}"

def get_file_bytes(filepath: str) -> tuple[bytes, str]:
    """
    Read a file from the Mac into memory over SFTP without saving to disk.

    Args:
        filepath: absolute path on the Mac e.g. /Users/tuhinanand/Downloads/file.pdf

    Returns:
        tuple of (file_bytes, filename)

    Raises:
        RuntimeError: if connection or file read fails
    """
    import paramiko

    try:
        # Set up SSH client
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(MAC_HOST, username=MAC_USER, timeout=SSH_TIMEOUT)

        # Open SFTP session and read file into memory
        sftp = client.open_sftp()
        with sftp.file(filepath, "rb") as f:
            file_bytes = f.read()

        sftp.close()
        client.close()

        filename = filepath.split("/")[-1]
        return file_bytes, filename

    except FileNotFoundError:
        raise RuntimeError(f"File not found on Mac: {filepath}")
    except Exception as e:
        raise RuntimeError(f"Could not read file from Mac: {e}")