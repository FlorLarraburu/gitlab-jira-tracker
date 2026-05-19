"""
Stacked MR manager for git-jira-tracker.
Tracks parent→child branch relationships and keeps MR targets in sync.
State stored in ~/.jira-tracker/stack.json
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

TRACKER_DIR = Path.home() / ".jira-tracker"
STACK_FILE = TRACKER_DIR / "stack.json"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_stack() -> dict:
    """
    Returns dict:
    {
      "repo_path": {
        "branch_name": {
          "parent": "parent_branch_or_null",
          "mr_iid": int_or_null,
          "mr_url": str_or_null
        },
        ...
      }
    }
    """
    if STACK_FILE.exists():
        try:
            return json.loads(STACK_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_stack(data: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    STACK_FILE.write_text(json.dumps(data, indent=2))


def _repo_path() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _git_run(*args) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, timeout=30
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_branch(branch: str, parent: Optional[str], mr_iid: Optional[int] = None,
                    mr_url: Optional[str] = None) -> None:
    """Register a branch in the stack with its parent."""
    repo = _repo_path()
    if not repo:
        return
    data = _load_stack()
    if repo not in data:
        data[repo] = {}
    data[repo][branch] = {
        "parent": parent,
        "mr_iid": mr_iid,
        "mr_url": mr_url,
    }
    _save_stack(data)


def update_mr_info(branch: str, mr_iid: int, mr_url: str) -> None:
    """Update MR info for an existing stack entry."""
    repo = _repo_path()
    if not repo:
        return
    data = _load_stack()
    if repo in data and branch in data[repo]:
        data[repo][branch]["mr_iid"] = mr_iid
        data[repo][branch]["mr_url"] = mr_url
        _save_stack(data)


def get_parent_branch(branch: Optional[str] = None) -> Optional[str]:
    """Return the parent branch name for the given (or current) branch."""
    repo = _repo_path()
    if not repo:
        return None
    branch = branch or _current_branch()
    data = _load_stack()
    return data.get(repo, {}).get(branch, {}).get("parent")


def get_parent_mr(branch: Optional[str] = None) -> Optional[dict]:
    """Return parent MR dict {mr_iid, mr_url} or None."""
    parent = get_parent_branch(branch)
    if not parent:
        return None
    repo = _repo_path()
    data = _load_stack()
    entry = data.get(repo, {}).get(parent, {})
    if entry.get("mr_iid"):
        return {"mr_iid": entry["mr_iid"], "mr_url": entry.get("mr_url")}
    return None


def get_children(branch: str) -> list[str]:
    """Return list of branches that have branch as parent."""
    repo = _repo_path()
    data = _load_stack()
    children = []
    for b, info in data.get(repo, {}).items():
        if info.get("parent") == branch:
            children.append(b)
    return children


def create_stacked_branch(new_branch: str) -> bool:
    """
    Create new_branch from the current branch (not develop).
    Register parent relationship.
    Returns True on success.
    """
    parent = _current_branch()
    if not parent:
        print("[stack] Could not determine current branch.")
        return False

    code, _, err = _git_run("checkout", "-b", new_branch)
    if code != 0:
        print(f"[stack] git checkout -b {new_branch} failed: {err}")
        return False

    register_branch(new_branch, parent)
    print(f"[stack] Created branch '{new_branch}' stacked on '{parent}'")
    return True


def show_stack_tree(gitlab_client=None) -> None:
    """Print the stack tree for the current repo."""
    repo = _repo_path()
    data = _load_stack()
    branches = data.get(repo, {})

    if not branches:
        print("No stacked branches registered for this repo.")
        return

    # Build tree structure
    roots: list[str] = []
    children_map: dict[str, list[str]] = {}
    for branch, info in branches.items():
        parent = info.get("parent")
        if not parent or parent not in branches:
            roots.append(branch)
        else:
            children_map.setdefault(parent, []).append(branch)

    def print_tree(branch: str, prefix: str = "", is_last: bool = True) -> None:
        connector = "└── " if is_last else "├── "
        entry = branches.get(branch, {})
        mr_iid = entry.get("mr_iid")
        mr_url = entry.get("mr_url", "")

        # Fetch live MR state if gitlab_client provided
        state_tag = ""
        if gitlab_client and mr_iid:
            try:
                mr = gitlab_client.get_mr(mr_iid)
                if mr:
                    state = mr.get("state", "")
                    draft = mr.get("draft", False)
                    label = "draft" if draft else state
                    state_tag = f" [{label}]"
            except Exception:
                pass

        mr_info = f" → !{mr_iid}{state_tag}" if mr_iid else " (sin MR)"
        print(f"{prefix}{connector}{branch}{mr_info}")
        children = children_map.get(branch, [])
        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(children):
            print_tree(child, new_prefix, i == len(children) - 1)

    print(f"\nStack tree for: {repo}")
    print("─" * 50)
    for i, root in enumerate(roots):
        print_tree(root, "", i == len(roots) - 1)
    print()


def update_stacked_mrs(merged_branch: str, new_target: str, gitlab_client=None) -> None:
    """
    When merged_branch gets merged, update all direct children MRs to point
    to new_target and optionally rebase.
    """
    children = get_children(merged_branch)
    if not children:
        print(f"[stack] No children branches found for '{merged_branch}'.")
        return

    for child in children:
        print(f"\n[stack] Updating child branch: {child}")

        repo = _repo_path()
        data = _load_stack()
        entry = data.get(repo, {}).get(child, {})
        mr_iid = entry.get("mr_iid")

        # Update target in GitLab
        if gitlab_client and mr_iid:
            ok = gitlab_client.update_mr_target(mr_iid, new_target)
            if ok:
                print(f"  ✓ MR !{mr_iid} target updated to '{new_target}'")
            else:
                print(f"  ✗ Failed to update MR !{mr_iid} target")

        # Update parent in local stack
        data[repo][child]["parent"] = new_target
        _save_stack(data)

        # Attempt rebase
        current = _current_branch()
        code, _, err = _git_run("rebase", new_target, child)
        if code == 0:
            print(f"  ✓ Rebased '{child}' onto '{new_target}'")
        else:
            print(f"  ✗ Rebase of '{child}' onto '{new_target}' failed (manual resolution needed): {err}")
