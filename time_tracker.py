"""
Core time tracking logic for git-jira-tracker.

Single global session: only one task tracked at a time across all repos.
Switching branches in ANY repo stops the current timer and starts a new one.

State in ~/.jira-tracker/state.json:
{
  "branch": "feature/QMS-123-...",
  "jira_key": "QMS-123",
  "repo": "/path/to/repo",
  "start_time": "<ISO>",
  "start_ts": 1234567890.0,
  "partial_seconds": 0
}
"""

import json
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

TRACKER_DIR = Path.home() / ".jira-tracker"
STATE_FILE   = TRACKER_DIR / "state.json"
TIME_LOG_FILE = TRACKER_DIR / "time_log.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text())
        # Reject old multi-repo format (keyed by path)
        if isinstance(data, dict) and data and all(str(k).startswith("/") or "\\" in str(k) for k in data):
            return {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _save_state(state: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def _load_time_log() -> list:
    if not TIME_LOG_FILE.exists():
        return []
    try:
        return json.loads(TIME_LOG_FILE.read_text())
    except Exception:
        return []

def _append_time_log(entry: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    log = _load_time_log()
    entry["logged_at"] = _now_iso()
    log.append(entry)
    if len(log) > 50_000:
        log = log[-50_000:]
    TIME_LOG_FILE.write_text(json.dumps(log, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_tracking(branch: str, jira_key: str, repo: str) -> None:
    """Start the single global tracking session."""
    _save_state({
        "branch": branch,
        "jira_key": jira_key,
        "repo": repo,
        "start_time": _now_iso(),
        "start_ts": _now_ts(),
        "partial_seconds": 0,
    })


def stop_tracking(min_seconds: int = 120) -> Optional[dict]:
    """
    Stop the current session regardless of which repo it belongs to.
    Returns {jira_key, seconds, started, branch, repo} or None.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None

    start_ts = state.get("start_ts", 0.0)
    partial  = state.get("partial_seconds", 0)
    elapsed  = int(_now_ts() - start_ts) + partial

    jira_key = state["jira_key"]
    branch   = state.get("branch", "")
    started  = state.get("start_time", _now_iso())
    repo     = state.get("repo", "")

    _save_state({})

    if elapsed < min_seconds:
        _append_time_log({
            "type": "ignored", "reason": "below_minimum",
            "jira_key": jira_key, "branch": branch,
            "seconds": elapsed, "min_seconds": min_seconds, "repo": repo,
        })
        return None

    _append_time_log({
        "type": "session_end",
        "jira_key": jira_key, "branch": branch,
        "seconds": elapsed, "started": started, "repo": repo,
    })
    return {"jira_key": jira_key, "seconds": elapsed,
            "started": started, "branch": branch, "repo": repo}


def save_partial(repo: str) -> Optional[dict]:
    """
    Checkpoint on commit: accumulate elapsed seconds without ending the session.
    Only checkpoints if the commit is in the currently tracked repo.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None
    if state.get("repo") != repo:
        return None  # commit in a different repo — don't touch the active session

    start_ts = state.get("start_ts", 0.0)
    partial  = state.get("partial_seconds", 0)
    elapsed  = int(_now_ts() - start_ts)

    new_partial = partial + elapsed
    state["partial_seconds"] = new_partial
    state["start_ts"] = _now_ts()
    _save_state(state)

    _append_time_log({
        "type": "partial_save",
        "jira_key": state["jira_key"],
        "branch": state.get("branch", ""),
        "partial_seconds": new_partial,
        "repo": repo,
    })
    return {
        "jira_key": state["jira_key"],
        "partial_seconds": new_partial,
        "branch": state.get("branch", ""),
        "repo": repo,
    }


def get_current_state() -> Optional[dict]:
    """Return active session with elapsed_seconds, or None."""
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None
    start_ts = state.get("start_ts", 0.0)
    partial  = state.get("partial_seconds", 0)
    return {
        "jira_key":       state["jira_key"],
        "branch":         state.get("branch", ""),
        "repo":           state.get("repo", ""),
        "elapsed_seconds": int(_now_ts() - start_ts) + partial,
        "start_time":     state.get("start_time", ""),
    }


def get_today_summary() -> dict[str, int]:
    """Returns {jira_key: total_seconds} for today."""
    today = date.today().isoformat()
    log = _load_time_log()
    summary: dict[str, int] = {}
    for entry in log:
        if entry.get("type") != "session_end":
            continue
        if not entry.get("logged_at", "").startswith(today):
            continue
        key = entry.get("jira_key", "")
        if key:
            summary[key] = summary.get(key, 0) + entry.get("seconds", 0)
    current = get_current_state()
    if current:
        key = current["jira_key"]
        summary[key] = summary.get(key, 0) + current["elapsed_seconds"]
    return summary


def get_weekly_summary() -> dict[str, int]:
    """Returns {jira_key: total_seconds} for the current week (Mon–Sun)."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    log = _load_time_log()
    summary: dict[str, int] = {}
    for entry in log:
        if entry.get("type") != "session_end":
            continue
        if entry.get("logged_at", "")[:10] < week_start:
            continue
        key = entry.get("jira_key", "")
        if key:
            summary[key] = summary.get(key, 0) + entry.get("seconds", 0)
    current = get_current_state()
    if current:
        key = current["jira_key"]
        summary[key] = summary.get(key, 0) + current["elapsed_seconds"]
    return summary


def get_branch_total_seconds(jira_key: str) -> int:
    """Total seconds ever tracked for a given Jira key."""
    log = _load_time_log()
    total = sum(
        e.get("seconds", 0) for e in log
        if e.get("type") == "session_end" and e.get("jira_key") == jira_key
    )
    current = get_current_state()
    if current and current["jira_key"] == jira_key:
        total += current["elapsed_seconds"]
    return total
