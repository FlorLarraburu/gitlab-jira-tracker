"""
Core time tracking logic for git-jira-tracker.

Single global session — one task tracked at a time across all repos.

State in ~/.jira-tracker/state.json:
{
  "branch":           "feature/QMS-123-...",
  "jira_key":         "QMS-123",
  "repo":             "/path/to/repo",
  "start_time":       "<ISO>",     — wall-clock start of the session
  "start_ts":         <float>,     — epoch when current uncommitted chunk started
  "partial_seconds":  <int>,       — time confirmed active via commits
  "last_active_ts":   <float>,     — last proof of activity: commit OR status check
  "paused":           <bool>,      — true when manually paused with `tracker stop`
}

Idle gap detection
──────────────────
last_active_ts is the heartbeat. It is updated by:
  • git commit          → save_partial()
  • tracker status/log  → ping_active()

When stop_tracking() is called, if now - last_active_ts > idle_threshold_minutes,
the dead time beyond the threshold is discarded.

Pause / resume
──────────────
  tracker stop    → pause_tracking(): freezes the timer, accumulated time preserved
  tracker restart → resume_tracking(): timer resumes from where it stopped
  git checkout    → if paused, bills the paused time as-is and starts new session
"""

import json
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

TRACKER_DIR   = Path.home() / ".jira-tracker"
STATE_FILE    = TRACKER_DIR / "state.json"
TIME_LOG_FILE = TRACKER_DIR / "time_log.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()

def _fmt_secs(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    parts = []
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    return " ".join(parts) if parts else "< 1m"

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text())
        # Discard old multi-repo format (dict keyed by paths)
        if data and all(str(k).startswith("/") or "\\" in str(k) for k in data):
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

def _idle_threshold() -> int:
    """Returns idle threshold in seconds from config."""
    from config_loader import load_config
    return int(load_config().get("idle_threshold_minutes", 120)) * 60


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_tracking(branch: str, jira_key: str, repo: str) -> None:
    """Start the single global tracking session."""
    now = _now_ts()
    _save_state({
        "branch":          branch,
        "jira_key":        jira_key,
        "repo":            repo,
        "start_time":      _now_iso(),
        "start_ts":        now,
        "partial_seconds": 0,
        "last_active_ts":  now,
    })


def ping_active() -> None:
    """
    Update last_active_ts to now — proof the user is at the computer.
    Called by any read command: status, log, doctor…
    No-op if no session is active or session is paused.
    """
    state = _load_state()
    if not state or not state.get("jira_key") or state.get("paused"):
        return
    state["last_active_ts"] = _now_ts()
    _save_state(state)


def pause_tracking() -> Optional[dict]:
    """
    Pause the current session: freeze the timer without billing to Jira.
    Accumulated time is preserved in partial_seconds.
    Returns session info or None if nothing is active.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None
    if state.get("paused"):
        return None  # already paused

    now      = _now_ts()
    start_ts = state.get("start_ts", now)
    partial  = state.get("partial_seconds", 0)
    elapsed  = int(now - start_ts)
    accumulated = partial + elapsed

    state["partial_seconds"] = accumulated
    state["start_ts"]        = now    # will be reset on resume anyway
    state["last_active_ts"]  = now
    state["paused"]          = True
    _save_state(state)

    _append_time_log({
        "type":    "paused",
        "jira_key": state["jira_key"],
        "branch":  state.get("branch", ""),
        "partial_seconds": accumulated,
    })
    return {
        "jira_key":          state["jira_key"],
        "branch":            state.get("branch", ""),
        "accumulated_seconds": accumulated,
    }


def resume_tracking() -> Optional[dict]:
    """
    Resume a paused session: restart the timer from the current moment.
    Returns session info or None if nothing is paused.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None
    if not state.get("paused"):
        return None  # not paused

    now = _now_ts()
    state["start_ts"]       = now
    state["last_active_ts"] = now
    state["paused"]         = False
    _save_state(state)

    _append_time_log({
        "type":    "resumed",
        "jira_key": state["jira_key"],
        "branch":  state.get("branch", ""),
    })
    return {
        "jira_key": state["jira_key"],
        "branch":   state.get("branch", ""),
        "partial_seconds": state.get("partial_seconds", 0),
    }


def save_partial(repo: str) -> Optional[dict]:
    """
    Checkpoint on git commit: updates last_active_ts and accumulates elapsed
    time into partial_seconds, resetting the uncommitted counter.
    Only acts if the commit is in the currently tracked repo and not paused.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None
    if state.get("repo") != repo:
        return None
    if state.get("paused"):
        return None  # timer is paused — commit doesn't resume or alter it

    now      = _now_ts()
    start_ts = state.get("start_ts", now)
    partial  = state.get("partial_seconds", 0)
    elapsed  = int(now - start_ts)

    new_partial = partial + elapsed
    state["partial_seconds"]  = new_partial
    state["start_ts"]         = now     # reset uncommitted counter
    state["last_active_ts"]   = now     # proof of activity
    _save_state(state)

    _append_time_log({
        "type":            "partial_save",
        "jira_key":        state["jira_key"],
        "branch":          state.get("branch", ""),
        "partial_seconds": new_partial,
        "repo":            repo,
    })
    return {
        "jira_key":        state["jira_key"],
        "partial_seconds": new_partial,
        "branch":          state.get("branch", ""),
        "repo":            repo,
    }


def stop_tracking(min_seconds: int = 120) -> Optional[dict]:
    """
    Stop the current session and return the billable time.

    If paused: bills partial_seconds directly — no idle detection needed,
    the user explicitly stopped the timer.

    If active: applies idle gap detection using last_active_ts as heartbeat.
    """
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None

    jira_key = state["jira_key"]
    branch   = state.get("branch", "")
    started  = state.get("start_time", _now_iso())
    repo     = state.get("repo", "")
    partial  = state.get("partial_seconds", 0)
    discarded = 0

    if state.get("paused"):
        # Timer was explicitly paused — bill only what was accumulated
        elapsed = partial
    else:
        start_ts       = state.get("start_ts", 0.0)
        last_active_ts = state.get("last_active_ts", start_ts)
        now            = _now_ts()
        uncommitted    = now - start_ts
        since_active   = now - last_active_ts
        idle_threshold = _idle_threshold()

        if since_active > idle_threshold:
            billable_uncommitted = max(0, uncommitted - (since_active - idle_threshold))
            discarded   = int(uncommitted - billable_uncommitted)
            uncommitted = billable_uncommitted

        elapsed = partial + int(uncommitted)

    _save_state({})

    if discarded:
        _append_time_log({
            "type":             "idle_gap_discarded",
            "jira_key":         jira_key,
            "branch":           branch,
            "discarded_seconds": discarded,
            "repo":             repo,
        })
        print(f"[tracker] Inactividad detectada — {_fmt_secs(discarded)} descartados")

    if elapsed < min_seconds:
        _append_time_log({
            "type": "ignored", "reason": "below_minimum",
            "jira_key": jira_key, "branch": branch,
            "seconds": elapsed, "min_seconds": min_seconds, "repo": repo,
        })
        return None

    _append_time_log({
        "type":     "session_end",
        "jira_key": jira_key, "branch": branch,
        "seconds":  elapsed, "started": started, "repo": repo,
    })
    return {"jira_key": jira_key, "seconds": elapsed,
            "started": started, "branch": branch, "repo": repo}


def get_current_state() -> Optional[dict]:
    """
    Return active session info. Pings last_active_ts unless paused.
    Returns None if no session exists.
    """
    ping_active()
    state = _load_state()
    if not state or not state.get("jira_key"):
        return None

    paused  = state.get("paused", False)
    partial = state.get("partial_seconds", 0)

    if paused:
        elapsed = partial          # timer frozen at accumulated value
    else:
        start_ts = state.get("start_ts", 0.0)
        elapsed  = int(_now_ts() - start_ts) + partial

    return {
        "jira_key":        state["jira_key"],
        "branch":          state.get("branch", ""),
        "repo":            state.get("repo", ""),
        "elapsed_seconds": elapsed,
        "start_time":      state.get("start_time", ""),
        "paused":          paused,
    }


def get_today_summary() -> dict[str, int]:
    ping_active()
    today = date.today().isoformat()
    log   = _load_time_log()
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
    ping_active()
    today      = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    log        = _load_time_log()
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
    log   = _load_time_log()
    total = sum(
        e.get("seconds", 0) for e in log
        if e.get("type") == "session_end" and e.get("jira_key") == jira_key
    )
    current = get_current_state()
    if current and current["jira_key"] == jira_key:
        total += current["elapsed_seconds"]
    return total
