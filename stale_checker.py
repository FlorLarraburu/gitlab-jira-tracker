"""
Stale MR detector for git-jira-tracker.
Lists MRs with no activity for more than stale_hours (default: 48).
Supports Slack webhook and email notifications.
"""

import re
from datetime import datetime, timezone
from typing import Optional

import gitlab_client as gl
from config_loader import load_config


def _hours_ago(dt: Optional[datetime]) -> Optional[float]:
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    delta = now - dt
    return delta.total_seconds() / 3600


def _human_delta(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} minutos"
    if hours < 24:
        return f"{int(hours)} horas"
    days = hours / 24
    return f"{int(days)} días"


def _extract_jira_key(title: str, pattern: str) -> Optional[str]:
    """Try to extract Jira issue key from MR title."""
    # Pattern like [QMS-123]
    m = re.search(r'\[([A-Z]+-\d+)\]', title)
    if m:
        return m.group(1)
    return None


def check_stale_mrs(stale_hours: Optional[float] = None) -> list[dict]:
    """
    Return list of stale MR dicts with enriched info:
    {mr_iid, title, jira_key, web_url, opened_hours, last_activity_hours,
     last_activity_dt, reviewer, has_reviewer}
    """
    cfg = load_config()
    if stale_hours is None:
        stale_hours = float(cfg.get("stale_hours", 48))

    branch_pattern = cfg.get("branch_pattern", r"^(feature|fix|chore)/([A-Z]+-[0-9]+)-.*$")

    mrs = gl.list_open_mrs()
    stale = []

    for mr in mrs:
        iid = mr.get("iid")
        title = mr.get("title", "")
        web_url = mr.get("web_url", "")
        created_at_str = mr.get("created_at", "")
        reviewer = gl.get_mr_reviewer(mr)

        # Compute opened time
        opened_hours = None
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                opened_hours = _hours_ago(created_dt)
            except Exception:
                pass

        # Get last activity
        last_activity_dt = gl.get_mr_last_activity(iid)
        last_activity_hours = _hours_ago(last_activity_dt)

        # Use last_activity_hours if available, otherwise opened_hours
        inactivity = last_activity_hours if last_activity_hours is not None else opened_hours

        if inactivity is None or inactivity < stale_hours:
            continue

        jira_key = _extract_jira_key(title, branch_pattern)

        stale.append({
            "iid": iid,
            "title": title,
            "jira_key": jira_key,
            "web_url": web_url,
            "opened_hours": opened_hours,
            "last_activity_hours": last_activity_hours,
            "last_activity_dt": last_activity_dt,
            "reviewer": reviewer,
            "has_reviewer": reviewer is not None,
        })

    return stale


def format_stale_report(stale_mrs: list[dict], stale_hours: float) -> str:
    """Format the stale MR list as a readable string."""
    if not stale_mrs:
        return f"✓ No hay MRs sin actividad hace más de {int(stale_hours)}h."

    lines = [f"MRs sin actividad hace más de {int(stale_hours)}h:",
             "─" * 45]

    for mr in stale_mrs:
        title = mr["title"]
        jira_key = mr.get("jira_key", "")
        prefix = f"[{jira_key}] " if jira_key else ""

        opened = mr.get("opened_hours")
        opened_str = _human_delta(opened) if opened else "?"

        last = mr.get("last_activity_hours")
        last_dt = mr.get("last_activity_dt")
        if last is not None:
            last_str = f"hace {_human_delta(last)}"
            if last_dt:
                # Try to get who made the last activity (we don't have that info here)
                last_str += f" ({last_dt.strftime('%Y-%m-%d %H:%M')} UTC)"
        else:
            last_str = "desconocido"

        reviewer = mr.get("reviewer")
        reviewer_str = reviewer if reviewer else "Sin asignar ⚠️"

        lines.append(f"\n{prefix}{title}")
        lines.append(f"  Abierta hace:   {opened_str}")
        lines.append(f"  Último evento:  {last_str}")
        lines.append(f"  Reviewer:       {reviewer_str}")
        lines.append(f"  URL:            {mr['web_url']}")

    return "\n".join(lines)


def notify_stale(stale_mrs: list[dict], stale_hours: float) -> bool:
    """
    Send notification about stale MRs via the configured channel.
    Returns True if notification was sent.
    """
    cfg = load_config()
    notify_channel = cfg.get("notify_channel", "")

    if not notify_channel:
        print("[stale] notify_channel not configured in config.json")
        return False

    report = format_stale_report(stale_mrs, stale_hours)

    # Slack webhook: starts with https://hooks.slack.com
    if notify_channel.startswith("https://"):
        ok = gl.send_slack_notification(notify_channel, report)
        if ok:
            print("[stale] Slack notification sent.")
        else:
            print("[stale] Slack notification failed.")
        return ok

    # Email: "smtp:host:port:user:password:to"
    if notify_channel.startswith("smtp:"):
        parts = notify_channel.split(":", 5)
        if len(parts) == 6:
            _, host, port, user, password, to = parts
            smtp_cfg = {"host": host, "port": port, "user": user,
                        "password": password, "to": to}
            ok = gl.send_email_notification(smtp_cfg, "MRs obsoletas — git-jira-tracker", report)
            if ok:
                print("[stale] Email notification sent.")
            else:
                print("[stale] Email notification failed.")
            return ok

    print(f"[stale] Unknown notify_channel format: {notify_channel}")
    return False
