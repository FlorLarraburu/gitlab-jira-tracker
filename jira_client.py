"""
Jira API v3 REST client for git-jira-tracker.
All calls have explicit timeouts and error handling.
Failures are saved to ~/.jira-tracker/pending.json for retry.
"""

import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

TRACKER_DIR = Path.home() / ".jira-tracker"
PENDING_FILE = TRACKER_DIR / "pending.json"
LOG_FILE = TRACKER_DIR / "log.json"
TIMEOUT = 10  # seconds

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _env(key: str, required: bool = True) -> str:
    val = os.environ.get(key, "")
    if required and not val:
        raise EnvironmentError(f"Missing environment variable: {key}")
    return val


def _auth() -> tuple[str, str]:
    return (_env("JIRA_USER"), _env("JIRA_TOKEN"))


def _base() -> str:
    return _env("JIRA_URL").rstrip("/")


def _headers() -> dict:
    return {"Accept": "application/json", "Content-Type": "application/json"}


def _log_event(event: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    events: list = []
    if LOG_FILE.exists():
        try:
            events = json.loads(LOG_FILE.read_text())
        except Exception:
            events = []
    event["ts"] = datetime.now(timezone.utc).isoformat()
    events.append(event)
    # Keep last 10 000 events
    if len(events) > 10_000:
        events = events[-10_000:]
    LOG_FILE.write_text(json.dumps(events, indent=2))


def _save_pending(entry: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    pending: list = []
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text())
        except Exception:
            pending = []
    entry["saved_at"] = datetime.now(timezone.utc).isoformat()
    pending.append(entry)
    PENDING_FILE.write_text(json.dumps(pending, indent=2))


def _load_pending() -> list:
    if not PENDING_FILE.exists():
        return []
    try:
        return json.loads(PENDING_FILE.read_text())
    except Exception:
        return []


def _write_pending(entries: list) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(entries, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_issue(issue_key: str) -> Optional[dict]:
    """Return issue fields dict or None on failure."""
    try:
        url = f"{_base()}/rest/api/3/issue/{issue_key}"
        resp = requests.get(url, auth=_auth(), headers=_headers(), timeout=TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            fields = data.get("fields", {})
            return {
                "key": issue_key,
                "summary": fields.get("summary", ""),
                "description": _extract_description(fields.get("description")),
                "status": fields.get("status", {}).get("name", ""),
                "url": f"{_base()}/browse/{issue_key}",
            }
        _log_event({"type": "jira_error", "action": "get_issue", "key": issue_key,
                    "status": resp.status_code, "body": resp.text[:500]})
    except Exception as exc:
        _log_event({"type": "jira_exception", "action": "get_issue", "key": issue_key,
                    "error": str(exc)})
    return None


def _extract_description(desc_field) -> str:
    """Convert Atlassian Document Format to plain text."""
    if not desc_field:
        return ""
    if isinstance(desc_field, str):
        return desc_field
    # ADF format
    lines: list[str] = []
    for block in desc_field.get("content", []):
        block_type = block.get("type", "")
        if block_type == "paragraph":
            text = ""
            for inline in block.get("content", []):
                if inline.get("type") == "text":
                    text += inline.get("text", "")
            if text:
                lines.append(text)
        elif block_type == "bulletList":
            for item in block.get("content", []):
                for para in item.get("content", []):
                    for inline in para.get("content", []):
                        if inline.get("type") == "text":
                            lines.append(f"- {inline.get('text', '')}")
        elif block_type == "heading":
            text = ""
            for inline in block.get("content", []):
                if inline.get("type") == "text":
                    text += inline.get("text", "")
            if text:
                lines.append(f"\n### {text}")
    return "\n".join(lines).strip()


def add_worklog(issue_key: str, seconds: int, started: str, comment: str = "") -> bool:
    """
    Impute worklog to Jira.
    If API fails, saves to pending.json and returns False.
    Returns True on success.
    """
    if seconds < 60:
        return True  # Nothing to log

    entry = {
        "type": "worklog",
        "task": issue_key,
        "seconds": seconds,
        "started": started,
        "comment": comment,
    }

    try:
        url = f"{_base()}/rest/api/3/issue/{issue_key}/worklog"
        # Jira expects timeSpentSeconds and started in specific format
        started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        started_jira = started_dt.strftime("%Y-%m-%dT%H:%M:%S.000+0000")

        payload = {
            "timeSpentSeconds": seconds,
            "started": started_jira,
        }
        if comment:
            payload["comment"] = {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}],
            }

        resp = requests.post(url, auth=_auth(), headers=_headers(),
                             json=payload, timeout=TIMEOUT)
        if resp.status_code in (200, 201):
            _log_event({"type": "worklog_ok", "task": issue_key,
                        "seconds": seconds, "started": started})
            return True

        _log_event({"type": "worklog_fail", "task": issue_key,
                    "status": resp.status_code, "body": resp.text[:500]})
        _save_pending(entry)
        return False

    except Exception as exc:
        _log_event({"type": "worklog_exception", "task": issue_key, "error": str(exc)})
        _save_pending(entry)
        return False


def transition_issue(issue_key: str, target_status_name: str) -> bool:
    """
    Transition issue to the given status name.
    Looks up available transitions and picks a fuzzy match.
    Returns True on success, False on failure (non-blocking).
    """
    try:
        url = f"{_base()}/rest/api/3/issue/{issue_key}/transitions"
        resp = requests.get(url, auth=_auth(), headers=_headers(), timeout=TIMEOUT)
        if resp.status_code != 200:
            return False

        transitions = resp.json().get("transitions", [])
        target_lower = target_status_name.lower()
        matched = None
        for t in transitions:
            name = t.get("to", {}).get("name", "").lower()
            if name == target_lower:
                matched = t
                break
        if not matched:
            # Partial match fallback
            for t in transitions:
                name = t.get("to", {}).get("name", "").lower()
                if target_lower in name or name in target_lower:
                    matched = t
                    break

        if not matched:
            _log_event({"type": "transition_no_match", "task": issue_key,
                        "target": target_status_name,
                        "available": [t.get("to", {}).get("name") for t in transitions]})
            return False

        payload = {"transition": {"id": matched["id"]}}
        resp2 = requests.post(url, auth=_auth(), headers=_headers(),
                              json=payload, timeout=TIMEOUT)
        if resp2.status_code in (200, 204):
            _log_event({"type": "transition_ok", "task": issue_key,
                        "status": matched["to"]["name"]})
            return True

        _log_event({"type": "transition_fail", "task": issue_key,
                    "status": resp2.status_code})
        return False

    except Exception as exc:
        _log_event({"type": "transition_exception", "task": issue_key, "error": str(exc)})
        return False


def retry_pending() -> tuple[int, int]:
    """
    Retry all pending entries.
    Returns (success_count, remaining_count).
    """
    pending = _load_pending()
    if not pending:
        return 0, 0

    remaining = []
    success = 0
    for entry in pending:
        entry_type = entry.get("type")
        if entry_type == "worklog":
            ok = add_worklog(
                entry["task"],
                entry["seconds"],
                entry["started"],
                entry.get("comment", ""),
            )
            if ok:
                success += 1
            else:
                remaining.append(entry)
        else:
            remaining.append(entry)

    _write_pending(remaining)
    return success, len(remaining)


def get_pending() -> list:
    return _load_pending()


def get_log(days: int = 7) -> list:
    """Return log entries from the last N days."""
    if not LOG_FILE.exists():
        return []
    try:
        events = json.loads(LOG_FILE.read_text())
        cutoff = time.time() - days * 86400
        result = []
        for e in events:
            try:
                ts = datetime.fromisoformat(e.get("ts", "")).timestamp()
                if ts >= cutoff:
                    result.append(e)
            except Exception:
                pass
        return result
    except Exception:
        return []
