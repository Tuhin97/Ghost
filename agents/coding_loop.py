"""
agents/coding_loop.py — Planning and execution loop for the coding agent.

This is what makes Ghost's coding agent intelligent rather than just
a file writer. Before touching any code, Ghost:
  1. Explores the codebase to understand what exists
  2. Makes a step-by-step plan
  3. Executes each step one at a time
  4. Verifies the result
  5. Commits working changes

This is called an agentic loop — Claude acts, observes, decides next action.
"""

import json
import logging
from pathlib import Path

import config
from agents.coding_agent import (
    analyse_codebase,
    create_repo,
    delete_file,
    git_commit,
    git_push,
    git_status,
    list_files,
    read_file,
    run_command,
    write_file,
)

logger = logging.getLogger(__name__)

# Maximum steps in a single coding session — prevents infinite loops
MAX_STEPS = 20


def _plan(task: str, codebase_summary: str, claude_client, model: str) -> list[dict]:
    """
    Ask Claude to make a step-by-step plan for a coding task.

    Args:
        task:             what the user wants to build or fix
        codebase_summary: output from analyse_codebase()
        claude_client:    Anthropic client
        model:            which Claude model to use

    Returns:
        list of step dicts — each has 'action' and 'description'
    """
    prompt = (
        f"You are a coding agent. Your task is:\n\n{task}\n\n"
        f"Current codebase:\n{codebase_summary}\n\n"
        f"Make a step-by-step plan to complete this task.\n"
        f"Each step must be one of these actions:\n"
        f"  - write_file: create or modify a file\n"
        f"  - run_command: run a shell command\n"
        f"  - read_file: read a file to understand it\n"
        f"  - create_repo: create a new project (already handles git init — do NOT add a separate git init step)\n"
        f"  - git_commit: commit changes\n"
        f"  - git_push: push to remote\n\n"
        f"IMPORTANT RULES:\n"
        f"  - If building a NEW project, ALWAYS start with create_repo to create a dedicated subdirectory\n"
        f"  - NEVER write files directly into ~/Projects — always create a subdirectory first\n"
        f"  - write_file paths must be relative to the project directory, not absolute\n"
        f"  - Always end with git_commit\n\n"
        f"  - For Python projects, use /home/tuhin/Ghost/venv/bin/pip for installing packages and /home/tuhin/Ghost/venv/bin/python3 to run scripts\n"
        f"Respond with a JSON array only. Example:\n"
        f'[{{"action": "create_repo", "description": "Create new project at ~/Projects/flask-app"}},\n'
        f' {{"action": "write_file", "description": "Create app.py with Flask boilerplate"}},\n'
        f' {{"action": "run_command", "description": "pip install flask"}},\n'
        f' {{"action": "git_commit", "description": "commit initial Flask app"}}]\n\n'
        f"Maximum {MAX_STEPS} steps. Be specific. No explanation outside the JSON."
    )

    response = claude_client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code blocks if Claude wrapped it
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Could not parse plan: %s", raw)
        return [{"action": "unknown", "description": raw}]


def _execute_step(step: dict, task: str, repo_path: str, claude_client, model: str) -> str:
    """
    Execute a single step from the plan.

    Claude decides exactly what to do for each step — what file to write,
    what command to run etc. This function calls the right coding_agent
    function based on the action type.

    Args:
        step:          step dict with 'action' and 'description'
        task:          original task for context
        repo_path:     working directory
        claude_client: Anthropic client
        model:         which Claude model to use

    Returns:
        result string from the action
    """
    action      = step.get("action", "")
    description = step.get("description", "")

    if action == "write_file":
        # Ask Claude what to write
        prompt = (
            f"Task: {task}\n"
            f"Step: {description}\n"
            f"Repo: {repo_path}\n\n"
            f"Respond with JSON only:\n"
            f'{{"path": "relative/path/to/file.py", "content": "full file content here"}}'
        )
        response = claude_client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            data    = json.loads(raw)
            path    = str(Path(repo_path) / data["path"])
            content = data["content"]
            return write_file(path, content)
        except Exception as e:
            return f"write_file step failed: {e}\nRaw: {raw[:200]}"

    elif action == "run_command":
        # Ask Claude what command to run
        prompt = (
            f"Task: {task}\n"
            f"Step: {description}\n"
            f"Respond with JSON only:\n"
            f'{{"command": "the shell command to run"}}'
        )
        response = claude_client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            data = json.loads(raw)
            return run_command(data["command"], cwd=repo_path)
        except Exception as e:
            return f"run_command step failed: {e}"

    elif action == "read_file":
        prompt = (
            f"Step: {description}\n"
            f"Repo: {repo_path}\n"
            f"Respond with JSON only:\n"
            f'{{"path": "relative/path/to/file"}}'
        )
        response = claude_client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            data = json.loads(raw)
            path = str(Path(repo_path) / data["path"])
            return read_file(path)
        except Exception as e:
            return f"read_file step failed: {e}"

    elif action == "create_repo":
        prompt = (
            f"Step: {description}\n"
            f"Respond with JSON only:\n"
            f'{{"path": "~/Projects/project-name", "description": "what this project does"}}'
        )
        response = claude_client.messages.create(
            model=model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            data = json.loads(raw)
            return create_repo(data["path"], data.get("description", ""))
        except Exception as e:
            return f"create_repo step failed: {e}"

    elif action == "git_commit":
        return git_commit(repo_path, description)

    elif action == "git_push":
        return git_push(repo_path)

    else:
        return f"Unknown action: {action}"


async def run_coding_task(
    task: str,
    repo_path: str,
    claude_client,
    model: str = None,
) -> str:
    """
    Run a complete coding task — plan, execute, verify, commit.

    This is the main entry point called by the orchestrator.

    Args:
        task:          what to build or fix in plain English
        repo_path:     path to work in — existing repo or new project
        claude_client: Anthropic client
        model:         Claude model to use — defaults to MODEL_BEST

    Returns:
        summary of what was done
    """
    if model is None:
        model = config.MODEL_BEST  # coding needs the best model

    repo_path = str(Path(repo_path).expanduser())
    results   = []

    # Step 1 — Explore
    logger.info("Coding task: %s in %s", task, repo_path)
    path_obj = Path(repo_path)

    if path_obj.exists():
        codebase_summary = analyse_codebase(repo_path)
    else:
        codebase_summary = "New project — directory does not exist yet."

    # Step 2 — Plan
    logger.info("Planning...")
    plan = _plan(task, codebase_summary, claude_client, model)
    results.append(f"Plan ({len(plan)} steps):")
    for i, step in enumerate(plan, 1):
        results.append(f"  {i}. [{step['action']}] {step['description']}")

    # Step 3 — Execute
    logger.info("Executing %d steps...", len(plan))
    for i, step in enumerate(plan, 1):
        logger.info("Step %d/%d: %s", i, len(plan), step.get("description", ""))
        result = _execute_step(step, task, repo_path, claude_client, model)
        results.append(f"\nStep {i} [{step['action']}]: {step['description']}")
        results.append(f"Result: {result[:500]}")

        # If create_repo succeeded, update repo_path to the new project directory
        if step.get("action") == "create_repo" and "Created new project at" in result:
            # Extract the path from the result message
            new_path = result.split("Created new project at ")[1].split(" with")[0].strip()
            repo_path = str(Path(new_path).expanduser())
            logger.info("Updated repo_path to: %s", repo_path)

    # Step 4 — Status
    status = git_status(repo_path)
    results.append(f"\nGit status:\n{status}")

    return "\n".join(results)