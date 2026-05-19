"""
Core time tracking logic for git-jira-tracker.
Manages state in ~/.jira-tracker/state.json
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TRACKER_DIR = Path.home() / ".jira-tracker"
STATE_FILE = TRACKER_DIR / "state.json"
TIME_LOG_FILE = TRACKER_DIR / "time_log.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _load_time_log() -> list:
    if TIME_LOG_FILE.exists():
        try:
            return json.loads(TIME_LOG_FILE.read_text())
        except Exception:
            pass
    return []


def _append_time_log(entry: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    log = _load_time_log()
    entry["logged_at"] = _now_iso()
    log.append(entry)
    if len(log) > 50_000:
        log = log[-50_000:]
    TIME_LOG_FILE.write_text(json.dumps(log, indent=2))


def _repo_path() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_tracking(branch: str, jira_key: str, repo: Optional[str] = None) -> None:
    """Start time tracking for a branch/task."""
    repo = repo or _repo_path()
    state = {
        "branch": branch,
        "jira_key": jira_key,
        "start_time": _now_iso(),
        "start_ts": _now_ts(),
        "repo": repo,
        "partial_seconds": 0,
    }
    _save_state(state)


def stop_tracking(min_seconds: int = 120) -> Optional[dict]:
    """
    Stop current tracking session.
    Returns dict with {jira_key, seconds, started, branch} or None.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None

    start_ts = state.get("start_ts", 0)
    partial = state.get("partial_seconds", 0)
    elapsed = int(_now_ts() - start_ts) + partial

    jira_key = state["jira_key"]
    branch = state.get("branch", "")
    started = state.get("start_time", _now_iso())
    repo = state.get("repo", "")

    # Clear state
    _save_state({})

    if elapsed < min_seconds:
        _append_time_log({
            "type": "ignored",
            "reason": "below_minimum",
            "jira_key": jira_key,
            "branch": branch,
            "seconds": elapsed,
            "min_seconds": min_seconds,
        })
        return None

    _append_time_log({
        "type": "session_end",
        "jira_key": jira_key,
        "branch": branch,
        "seconds": elapsed,
        "started": started,
        "repo": repo,
    })

    return {
        "jira_key": jira_key,
        "seconds": elapsed,
        "started": started,
        "branch": branch,
        "repo": repo,
    }


def save_partial() -> Optional[dict]:
    """
    Save partial time (called on commit) so time is not lost.
    Updates accumulated partial_seconds and resets start_ts.
    Returns current accumulated info or None.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None

    start_ts = state.get("start_ts", 0)
    partial = state.get("partial_seconds", 0)
    elapsed = int(_now_ts() - start_ts)

    new_partial = partial + elapsed
    state["partial_seconds"] = new_partial
    state["start_ts"] = _now_ts()
    _save_state(state)

    _append_time_log({
        "type": "partial_save",
        "jira_key": state["jira_key"],
        "branch": state.get("branch", ""),
        "partial_seconds": new_partial,
    })

    return {
        "jira_key": state["jira_key"],
        "partial_seconds": new_partial,
        "branch": state.get("branch", ""),
    }


def get_current_state() -> Optional[dict]:
    """Return current tracking state with elapsed seconds."""
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None

    start_ts = state.get("start_ts", 0)
    partial = state.get("partial_seconds", 0)
    elapsed = int(_now_ts() - start_ts) + partial

    return {
        "jira_key": state["jira_key"],
        "branch": state.get("branch", ""),
        "elapsed_seconds": elapsed,
        "start_time": state.get("start_time", ""),
        "repo": state.get("repo", ""),
    }


def get_today_summary() -> dict[str, int]:
    """
    Return dict of {jira_key: total_seconds} for today.
    """
    from datetime import date
    today = date.today().isoformat()
    log = _load_time_log()
    summary: dict[str, int] = {}

    for entry in log:
        if entry.get("type") not in ("session_end",):
            continue
        logged_at = entry.get("logged_at", "")
        if not logged_at.startswith(today):
            continue
        key = entry.get("jira_key", "")
        if key:
            summary[key] = summary.get(key, 0) + entry.get("seconds", 0)

    # Add current session if active
    current = get_current_state()
    if current:
        key = current["jira_key"]
        summary[key] = summary.get(key, 0) + current["elapsed_seconds"]

    return summary


def get_weekly_summary() -> dict[str, int]:
    """
    Return dict of {jira_key: total_seconds} for the current week (Mon–Sun).
    """
    from datetime import date, timedelta
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_start_iso = week_start.isoformat()

    log = _load_time_log()
    summary: dict[str, int] = {}

    for entry in log:
        if entry.get("type") not in ("session_end",):
            continue
        logged_at = entry.get("logged_at", "")
        if logged_at[:10] < week_start_iso:
            continue
        key = entry.get("jira_key", "")
        if key:
            summary[key] = summary.get(key, 0) + entry.get("seconds", 0)

    # Add current session
    current = get_current_state()
    if current:
        key = current["jira_key"]
        summary[key] = summary.get(key, 0) + current["elapsed_seconds"]

    return summary


def get_branch_total_seconds(jira_key: str) -> int:
    """Return total seconds tracked for a given Jira key across all sessions."""
    log = _load_time_log()
    total = 0
    for entry in log:
        if entry.get("type") == "session_end" and entry.get("jira_key") == jira_key:
            total += entry.get("seconds", 0)
    current = get_current_state()
    if current and current["jira_key"] == jira_key:
        total += current["elapsed_seconds"]
    return total
