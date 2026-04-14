"""
agents/file_agent.py — SSH file search on Mac and Linux.

Security:
  - Input sanitised with regex whitelist before any shell use
  - All shell arguments quoted with shlex.quote()
  - Search paths restricted to SAFE_SEARCH_PATHS whitelist
  - Never searches / or paths outside the whitelist
  - Uses dedicated ghost_key SSH key, not personal key
"""

import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

import paramiko

import config


@dataclass
class FileResult:
    """
    Represents a single file found during search.

    @dataclass automatically generates:
      - __init__() so you can do FileResult(device="mac", path="/Users/...")
      - __repr__() so printing looks clean
      - __eq__() so you can compare two FileResults
    """
    device: str    # "mac" or "linux"
    path: str      # full path to the file

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON responses."""
        return {"device": self.device, "path": self.path}


def _sanitise_query(query: str) -> str:
    """
    Strip everything except safe characters from the search query.

    Allowed: letters, numbers, spaces, dots, hyphens, underscores
    Removed: semicolons, pipes, backticks, quotes, $, !, etc.

    This prevents shell injection — a query like
    "resume; rm -rf ~/" becomes just "resume rm -rf " which
    is harmless when passed to find.

    Args:
        query: raw user input

    Returns:
        sanitised string safe to use in shell commands
    """
    return re.sub(r'[^a-zA-Z0-9\s.\-_]', '', query).strip()


def _build_find_command(query: str, paths: list) -> str:
    """
    Build a safe find command to search for files.

    Uses shlex.quote() on every argument so nothing can be
    interpreted as a shell command.

    Example output:
      find '/Users/tuhin/Documents' '/Users/tuhin/Downloads'
           -maxdepth 8 -iname '*resume*' -type f 2>/dev/null | head -20

    Args:
        query: sanitised search term
        paths: list of absolute paths to search in

    Returns:
        complete shell command as a string
    """
    # Quote the query — wraps it in single quotes
    # *resume* becomes '*resume*' — shell can't inject through it
    safe_query = shlex.quote(f"*{query}*")

    # Quote every path individually
    safe_paths = " ".join(shlex.quote(p) for p in paths)

    return (
        f"find {safe_paths} "
        f"-maxdepth 8 "           # never recurse deeper than 8 levels
        f"-iname {safe_query} "   # -i means case insensitive
        f"-type f "               # only files, not folders
        f"2>/dev/null "           # suppress permission errors
        f"| head -20"             # max 20 results
    )


def _ssh_find(host: str, user: str, command: str) -> list:
    """
    Connect to a remote machine via SSH and run a command.

    Uses the dedicated ghost_key — not your personal SSH key.
    Connection is always closed in the finally block even if
    something goes wrong.

    Args:
        host:    IP or hostname of the remote machine
        user:    SSH username
        command: the command to run remotely

    Returns:
        list of output lines from the command
    """
    client = paramiko.SSHClient()

    # RejectPolicy means — never auto-accept unknown host keys
    # This prevents man-in-the-middle attacks
    # The host must already be in known_hosts
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    # Expand ~ to actual home directory path
    key_path = config.SSH_KEY_PATH.replace("~", f"/home/{config.LINUX_USER}")

    try:
        client.connect(
            hostname=host,
            username=user,
            key_filename=key_path,
            timeout=10,          # give up connecting after 10 seconds
            look_for_keys=False, # only use ghost_key, not any other key
            allow_agent=False,   # don't use SSH agent — explicit key only
        )

        # Run the command on the remote machine
        # timeout=15 means give up if command takes longer than 15 seconds
        _, stdout, _ = client.exec_command(command, timeout=15)

        # Read all output, decode bytes to string, split into lines
        lines = stdout.read().decode(errors="replace").strip().splitlines()

        # Return only non-empty lines
        return [line for line in lines if line.strip()]

    finally:
        # Always close the connection
        # finally runs even if an exception was raised above
        client.close()


def search_files(
    query: str,
    devices: Optional[list] = None,
    path_hint: Optional[str] = None,
) -> list:
    """
    Search for files matching query on specified devices.

    This is the main public function — orchestrator calls this.

    Args:
        query:      filename or keyword to search for
        devices:    list of "mac" and/or "linux". Defaults to both
        path_hint:  optional folder name to narrow search
                    e.g. "Desktop", "Downloads"
                    if None, searches all SAFE_SEARCH_PATHS

    Returns:
        list of FileResult objects

    Raises:
        ValueError: if query is empty after sanitisation
    """
    if devices is None:
        devices = ["mac", "linux"]

    # Step 1 — sanitise the query
    clean_query = _sanitise_query(query)
    if not clean_query:
        raise ValueError("Search query is empty after sanitisation.")

    # Step 2 — build search paths
    # If path_hint given, filter to only matching paths
    # e.g. path_hint="Desktop" → only search ~/Desktop
    if path_hint:
        safe_hint = _sanitise_query(path_hint)
        search_paths = [
            p for p in config.SAFE_SEARCH_PATHS
            if safe_hint.lower() in p.lower()
        ]
        # Fall back to all paths if hint matched nothing
        if not search_paths:
            search_paths = config.SAFE_SEARCH_PATHS
    else:
        search_paths = config.SAFE_SEARCH_PATHS

    results = []

    # Step 3 — search Mac via SSH
    if "mac" in devices and config.MAC_HOST and config.MAC_USER:
        mac_paths = [
            p.replace("~", f"/Users/{config.MAC_USER}")
            for p in search_paths
        ]
        cmd = _build_find_command(clean_query, mac_paths)
        try:
            paths = _ssh_find(config.MAC_HOST, config.MAC_USER, cmd)
            results.extend(
                FileResult(device="mac", path=p) for p in paths
            )
        except Exception as e:
            results.append(
                FileResult(device="mac", path=f"[ERROR: {e}]")
            )

    # Step 4 — search Linux locally since im using Linux to host the service.
    if "linux" in devices:
        linux_paths = [
            p.replace("~", f"/home/{config.LINUX_USER}")
            for p in search_paths
        ]
        cmd = _build_find_command(clean_query, linux_paths)
        try:
            out = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=15
            )
            for line in out.stdout.strip().splitlines():
                if line.strip():
                    results.append(
                        FileResult(device="linux", path=line.strip())
                    )
        except Exception as e:
            results.append(
                FileResult(device="linux", path=f"[ERROR: {e}]")
            )

    return results


def format_results(results: list) -> str:
    """
    Format search results into a human readable string
    for Ghost's response.

    Args:
        results: list of FileResult objects

    Returns:
        formatted string to include in Ghost's reply
    """
    if not results:
        return "No files found."

    lines = [f"  [{r.device}] {r.path}" for r in results]
    return f"Found {len(results)} file(s):\n" + "\n".join(lines)
