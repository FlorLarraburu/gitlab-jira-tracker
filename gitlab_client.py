"""
GitLab API v4 REST client for git-jira-tracker.
All calls have explicit timeouts and error handling.
"""

import os
from datetime import datetime, timezone
from typing import Optional

import requests

TIMEOUT = 10


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise EnvironmentError(f"Missing environment variable: {key}")
    return val


def _base() -> str:
    return _env("GITLAB_URL").rstrip("/")


def _project_id() -> str:
    return _env("GITLAB_PROJECT_ID")


def _headers() -> dict:
    return {
        "PRIVATE-TOKEN": _env("GITLAB_TOKEN"),
        "Content-Type": "application/json",
    }


def _api(path: str) -> str:
    return f"{_base()}/api/v4{path}"


# ---------------------------------------------------------------------------
# MR operations
# ---------------------------------------------------------------------------

def create_mr(
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    draft: bool = True,
) -> Optional[dict]:
    """
    Create a Merge Request. Returns dict with 'iid', 'web_url', 'id' or None.
    """
    try:
        final_title = f"Draft: {title}" if draft else title

        payload: dict = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": final_title,
            "description": description,
            "remove_source_branch": True,
        }

        url = _api(f"/projects/{_project_id()}/merge_requests")
        resp = requests.post(url, headers=_headers(), json=payload, timeout=TIMEOUT)
        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "iid": data["iid"],
                "id": data["id"],
                "web_url": data["web_url"],
                "title": data["title"],
                "state": data["state"],
                "draft": data.get("draft", False),
            }
        print(f"[gitlab] create_mr failed: {resp.status_code} {resp.text[:300]}")
        return None
    except Exception as exc:
        print(f"[gitlab] create_mr exception: {exc}")
        return None


def mark_ready(mr_iid: int) -> Optional[dict]:
    """Remove draft status from MR and return updated MR dict."""
    try:
        url = _api(f"/projects/{_project_id()}/merge_requests/{mr_iid}")
        # Remove "Draft: " prefix from title first
        mr = get_mr(mr_iid)
        if not mr:
            return None

        title = mr.get("title", "")
        for prefix in ("Draft: ", "WIP: ", "[WIP] ", "draft: "):
            if title.startswith(prefix):
                title = title[len(prefix):]
                break

        payload = {"title": title, "draft": False}
        resp = requests.put(url, headers=_headers(), json=payload, timeout=TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "iid": data["iid"],
                "web_url": data["web_url"],
                "title": data["title"],
                "draft": data.get("draft", False),
            }
        print(f"[gitlab] mark_ready failed: {resp.status_code} {resp.text[:300]}")
        return None
    except Exception as exc:
        print(f"[gitlab] mark_ready exception: {exc}")
        return None


def get_mr(mr_iid: int) -> Optional[dict]:
    """Get MR details by IID."""
    try:
        url = _api(f"/projects/{_project_id()}/merge_requests/{mr_iid}")
        resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def get_mr_for_branch(source_branch: str) -> Optional[dict]:
    """Find open MR for a given source branch."""
    try:
        url = _api(f"/projects/{_project_id()}/merge_requests")
        params = {
            "source_branch": source_branch,
            "state": "opened",
            "per_page": 5,
        }
        resp = requests.get(url, headers=_headers(), params=params, timeout=TIMEOUT)
        if resp.status_code == 200:
            mrs = resp.json()
            if mrs:
                m = mrs[0]
                return {
                    "iid": m["iid"],
                    "id": m["id"],
                    "web_url": m["web_url"],
                    "title": m["title"],
                    "state": m["state"],
                    "draft": m.get("draft", False),
                    "target_branch": m["target_branch"],
                    "source_branch": m["source_branch"],
                }
        return None
    except Exception:
        return None


def list_open_mrs(page: int = 1, per_page: int = 100) -> list:
    """List all open MRs for the project."""
    try:
        url = _api(f"/projects/{_project_id()}/merge_requests")
        params = {"state": "opened", "per_page": per_page, "page": page}
        resp = requests.get(url, headers=_headers(), params=params, timeout=TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        print(f"[gitlab] list_open_mrs failed: {resp.status_code}")
        return []
    except Exception as exc:
        print(f"[gitlab] list_open_mrs exception: {exc}")
        return []


def update_mr_target(mr_iid: int, new_target_branch: str) -> bool:
    """Update the target branch of an MR."""
    try:
        url = _api(f"/projects/{_project_id()}/merge_requests/{mr_iid}")
        payload = {"target_branch": new_target_branch}
        resp = requests.put(url, headers=_headers(), json=payload, timeout=TIMEOUT)
        return resp.status_code == 200
    except Exception:
        return False


def get_mr_last_activity(mr_iid: int) -> Optional[datetime]:
    """
    Return datetime of last activity on the MR (notes + commits).
    Returns None on failure.
    """
    try:
        # Check notes (comments)
        notes_url = _api(f"/projects/{_project_id()}/merge_requests/{mr_iid}/notes")
        params = {"sort": "desc", "order_by": "created_at", "per_page": 1}
        resp = requests.get(notes_url, headers=_headers(), params=params, timeout=TIMEOUT)
        last_note_dt = None
        if resp.status_code == 200:
            notes = resp.json()
            if notes:
                ts_str = notes[0].get("created_at", "")
                if ts_str:
                    last_note_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

        # Check MR itself for updated_at
        mr = get_mr(mr_iid)
        last_update_dt = None
        if mr:
            ts_str = mr.get("updated_at", "")
            if ts_str:
                last_update_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

        candidates = [dt for dt in [last_note_dt, last_update_dt] if dt]
        if candidates:
            return max(candidates)
        return None
    except Exception:
        return None


def get_mr_reviewer(mr: dict) -> Optional[str]:
    """Extract first reviewer username from MR dict."""
    reviewers = mr.get("reviewers", [])
    if reviewers:
        return reviewers[0].get("username")
    assignee = mr.get("assignee")
    if assignee:
        return assignee.get("username")
    return None


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

def get_user_id(username: str) -> Optional[int]:
    """Resolve GitLab username to user ID."""
    try:
        url = _api("/users")
        params = {"username": username}
        resp = requests.get(url, headers=_headers(), params=params, timeout=TIMEOUT)
        if resp.status_code == 200:
            users = resp.json()
            if users:
                return users[0]["id"]
        return None
    except Exception:
        return None


def verify_connection() -> bool:
    """Quick connectivity check to GitLab."""
    try:
        url = _api(f"/projects/{_project_id()}")
        resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_slack_notification(webhook_url: str, message: str) -> bool:
    """Send message to Slack via incoming webhook."""
    try:
        payload = {"text": message}
        resp = requests.post(webhook_url, json=payload, timeout=TIMEOUT)
        return resp.status_code == 200
    except Exception:
        return False


def send_email_notification(smtp_config: dict, subject: str, body: str) -> bool:
    """Send email using smtplib. smtp_config keys: host, port, user, password, to."""
    import smtplib
    from email.mime.text import MIMEText
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_config["user"]
        msg["To"] = smtp_config["to"]
        with smtplib.SMTP(smtp_config["host"], int(smtp_config.get("port", 587))) as s:
            s.starttls()
            s.login(smtp_config["user"], smtp_config["password"])
            s.send_message(msg)
        return True
    except Exception as exc:
        print(f"[email] send failed: {exc}")
        return False
