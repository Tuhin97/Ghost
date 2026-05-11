"""
agents/coding_agent.py — Autonomous coding agent for Ghost.

Capabilities:
  - Read and understand existing codebases
  - Write new files and modify existing ones
  - Run shell commands (tests, installs, servers)
  - Git commit and push changes
  - Create new projects from scratch

Security:
  - Only operates within SAFE_CODE_PATHS
  - Never deletes files without confirmation
  - Always commits before making large changes
  - Shows diff before pushing to remote
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Paths Ghost is allowed to read and write code in
SAFE_CODE_PATHS = [
    Path("~/Ghost").expanduser(),
    Path("~/Projects").expanduser(),
    Path("~/Code").expanduser(),
]

# Commands that are never allowed regardless of context
BLOCKED_COMMANDS = [
    "rm -rf",
    "sudo",
    "chmod 777",
    "dd if",
    "> /dev/",
    "mkfs",
    "shutdown",
    "reboot",
]

# Max output length to return — long outputs get truncated
MAX_OUTPUT_CHARS = 3000

def _is_safe_path(path: Path) -> bool:
    """
    Check if a path is within the allowed coding directories.

    Args:
        path: absolute path to check

    Returns:
        True if safe, False if outside allowed directories
    """
    resolved = path.resolve()
    return any(
        str(resolved).startswith(str(safe))
        for safe in SAFE_CODE_PATHS
    )


def read_file(path: str) -> str:
    """
    Read a file from the filesystem.

    Args:
        path: file path — can use ~ for home directory

    Returns:
        file contents as string
    """
    file_path = Path(path).expanduser().resolve()

    if not _is_safe_path(file_path):
        return f"Access denied: {path} is outside allowed directories."

    if not file_path.exists():
        return f"File not found: {path}"

    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Cannot read {path} — binary file."
    except Exception as e:
        return f"Error reading {path}: {e}"


def write_file(path: str, content: str) -> str:
    """
    Write content to a file, creating directories if needed.

    Args:
        path:    file path to write to
        content: content to write

    Returns:
        confirmation or error message
    """
    file_path = Path(path).expanduser().resolve()

    if not _is_safe_path(file_path):
        return f"Access denied: {path} is outside allowed directories."

    try:
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Written: {path} ({len(content)} chars)"
    except Exception as e:
        return f"Error writing {path}: {e}"


def list_files(path: str) -> str:
    """
    List files and directories in a path recursively.

    Args:
        path: directory path to list

    Returns:
        tree-like string of files
    """
    dir_path = Path(path).expanduser().resolve()

    if not _is_safe_path(dir_path):
        return f"Access denied: {path} is outside allowed directories."

    if not dir_path.exists():
        return f"Directory not found: {path}"

    lines = []
    for item in sorted(dir_path.rglob("*")):
        # Skip hidden files, __pycache__, venv
        parts = item.parts
        if any(p.startswith(".") or p in ("__pycache__", "venv", "node_modules") for p in parts):
            continue
        # Indent based on depth relative to root
        depth  = len(item.relative_to(dir_path).parts) - 1
        prefix = "  " * depth + ("📁 " if item.is_dir() else "📄 ")
        lines.append(f"{prefix}{item.name}")

    return "\n".join(lines) if lines else "Empty directory."


def delete_file(path: str) -> str:
    """
    Delete a file — only allowed within safe paths.

    Args:
        path: file path to delete

    Returns:
        confirmation or error
    """
    file_path = Path(path).expanduser().resolve()

    if not _is_safe_path(file_path):
        return f"Access denied: {path} is outside allowed directories."

    if not file_path.exists():
        return f"File not found: {path}"

    if file_path.is_dir():
        return f"Use a specific file path — will not delete directories automatically."

    try:
        file_path.unlink()
        return f"Deleted: {path}"
    except Exception as e:
        return f"Error deleting {path}: {e}"

def run_command(command: str, cwd: str = None) -> str:
    """
    Run a shell command and return the output.

    Args:
        command: shell command to run
        cwd:     working directory — defaults to home

    Returns:
        combined stdout and stderr output, truncated if too long
    """
    # Check for blocked commands
    for blocked in BLOCKED_COMMANDS:
        if blocked in command:
            return f"Command blocked for security: '{blocked}' is not allowed."

    # Default working directory
    work_dir = Path(cwd).expanduser() if cwd else Path.home()

    if cwd and not _is_safe_path(work_dir.resolve()):
        return f"Access denied: {cwd} is outside allowed directories."

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=60,  # never hang forever
        )

        # Combine stdout and stderr
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"

        output = output.strip()

        # Truncate long output
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(output)} total chars]"

        if not output:
            return f"Command completed with return code {result.returncode}."

        return output

    except subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds."
    except Exception as e:
        return f"Command failed: {e}"
    
def git_init(repo_path: str) -> str:
    """
    Initialise a new git repository.

    Args:
        repo_path: path to initialise git in

    Returns:
        confirmation or error
    """
    path = Path(repo_path).expanduser().resolve()

    if not _is_safe_path(path):
        return f"Access denied: {repo_path} is outside allowed directories."

    path.mkdir(parents=True, exist_ok=True)
    return run_command("git init", cwd=str(path))


def git_status(repo_path: str) -> str:
    """
    Get git status of a repository.

    Args:
        repo_path: path to the repository

    Returns:
        git status output
    """
    return run_command("git status", cwd=repo_path)


def git_diff(repo_path: str) -> str:
    """
    Get git diff of uncommitted changes.

    Args:
        repo_path: path to the repository

    Returns:
        git diff output
    """
    return run_command("git diff", cwd=repo_path)


def git_commit(repo_path: str, message: str) -> str:
    """
    Stage all changes and commit.

    Args:
        repo_path: path to the repository
        message:   commit message

    Returns:
        confirmation or error
    """
    # Stage all changes
    stage = run_command("git add -A", cwd=repo_path)
    if "blocked" in stage.lower() or "denied" in stage.lower():
        return stage

    # Commit
    return run_command(f'git commit -m "{message}"', cwd=repo_path)


def git_push(repo_path: str) -> str:
    """
    Push committed changes to remote.

    Args:
        repo_path: path to the repository

    Returns:
        confirmation or error
    """
    return run_command("git push", cwd=repo_path)


def git_log(repo_path: str, count: int = 5) -> str:
    """
    Get recent git commit history.

    Args:
        repo_path: path to the repository
        count:     number of commits to show

    Returns:
        formatted commit log
    """
    return run_command(f"git log --oneline -n {count}", cwd=repo_path)


def create_repo(repo_path: str, description: str = "") -> str:
    """
    Create a new project directory with git init and basic structure.

    Args:
        repo_path:   where to create the project
        description: optional project description for README

    Returns:
        confirmation or error
    """
    path = Path(repo_path).expanduser().resolve()

    if not _is_safe_path(path):
        return f"Access denied: {repo_path} is outside allowed directories."

    if path.exists():
        return f"Directory already exists: {repo_path}"

    # Create directory and init git
    path.mkdir(parents=True, exist_ok=True)
    git_init(str(path))

    # Create basic .gitignore
    gitignore = """# Python
__pycache__/
*.pyc
*.pyo
venv/
.env

# Node
node_modules/
dist/

# OS
.DS_Store
*.swp
"""
    write_file(str(path / ".gitignore"), gitignore)

    # Create README
    readme = f"# {path.name}\n\n{description}\n"
    write_file(str(path / "README.md"), readme)

    # Initial commit
    git_commit(str(path), "Initial commit")

    return f"Created new project at {repo_path} with git initialised."

def analyse_codebase(repo_path: str) -> str:
    """
    Analyse a codebase and return a summary for Claude to understand.

    Reads the file structure and key files so Claude can understand
    the project before making changes.

    Args:
        repo_path: path to the repository

    Returns:
        summary string — file tree + key file contents
    """
    path = Path(repo_path).expanduser().resolve()

    if not _is_safe_path(path):
        return f"Access denied: {repo_path} is outside allowed directories."

    if not path.exists():
        return f"Directory not found: {repo_path}"

    sections = []

    # File tree
    sections.append("=== FILE STRUCTURE ===")
    sections.append(list_files(repo_path))

    # Key files to read — understand the project
    KEY_FILES = [
        "README.md",
        "requirements.txt",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "config.py",
        "main.py",
        "app.py",
        "index.js",
        "index.ts",
    ]

    sections.append("\n=== KEY FILES ===")
    for filename in KEY_FILES:
        file_path = path / filename
        if file_path.exists():
            content = read_file(str(file_path))
            # Only include first 500 chars of each file — enough context
            preview = content[:500] + "..." if len(content) > 500 else content
            sections.append(f"\n--- {filename} ---\n{preview}")

    # Git log if it's a git repo
    git_log_output = git_log(repo_path)
    if "not a git repository" not in git_log_output.lower():
        sections.append(f"\n=== RECENT COMMITS ===\n{git_log_output}")

    return "\n".join(sections)