"""
Loads .env and config.json for git-jira-tracker.
Searches for .env in: current directory, repo root, script directory, HOME.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG = {
    "default_target_branch": "develop",
    "min_track_minutes": 2,
    "idle_threshold_minutes": 60,
    "stale_hours": 48,
    "notify_channel": "",
    "jira_statuses": {
        "in_progress": "En progreso",
        "in_review": "En revisión",
        "in_review_draft": "",
        "done": "Hecho",
    },
    "branch_pattern": r"^(feature|fix|chore)/([A-Z]+-[0-9]+)-.*$",
}


def _script_dir() -> Path:
    return Path(__file__).parent.resolve()


def _repo_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return Path.cwd()


def load_dotenv() -> None:
    """Load .env file into os.environ. Search in multiple locations."""
    search_paths = [
        Path.cwd() / ".env",
        _repo_root() / ".env",
        _script_dir() / ".env",
        Path.home() / ".jira-tracker" / ".env",
    ]
    for path in search_paths:
        if path.exists():
            _parse_env_file(path)
            return


def _parse_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def load_config() -> dict[str, Any]:
    """Load config.json, merging with defaults. Returns config dict."""
    search_paths = [
        Path.cwd() / "config.json",
        _repo_root() / "config.json",
        _script_dir() / "config.json",
        Path.home() / ".jira-tracker" / "config.json",
    ]
    cfg = dict(_DEFAULT_CONFIG)
    cfg["jira_statuses"] = dict(_DEFAULT_CONFIG["jira_statuses"])

    for path in search_paths:
        if path.exists():
            try:
                user_cfg = json.loads(path.read_text(encoding="utf-8"))
                # Merge top-level keys
                for k, v in user_cfg.items():
                    if k == "jira_statuses" and isinstance(v, dict):
                        cfg["jira_statuses"].update(v)
                    else:
                        cfg[k] = v
                break
            except Exception as exc:
                print(f"[config] Warning: could not parse {path}: {exc}")

    return cfg


def extract_jira_key(branch_name: str, pattern: Optional[str] = None) -> Optional[str]:
    """Extract Jira issue key from a branch name using the configured pattern."""
    from typing import Optional as Opt
    cfg = load_config()
    pat = pattern or cfg.get("branch_pattern", r"^(feature|fix|chore)/([A-Z]+-[0-9]+)-.*$")
    m = re.match(pat, branch_name)
    if m:
        # Return the group that looks like PROJ-123
        for g in m.groups():
            if g and re.match(r"^[A-Z]+-\d+$", g):
                return g
    # Fallback: search anywhere in branch name
    m2 = re.search(r"([A-Z]+-\d+)", branch_name)
    if m2:
        return m2.group(1)
    return None


# Make Optional importable from this module for convenience
from typing import Optional
