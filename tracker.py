#!/usr/bin/env python3
"""
git-jira-tracker — CLI principal
Uso: python tracker.py <comando> [opciones]
"""

import argparse
import sys
import os

# Load .env before any other import that needs env vars
from config_loader import load_dotenv, load_config, extract_jira_key
load_dotenv()

from pathlib import Path
from typing import Optional

import time_tracker as tt
import jira_client as jira
import gitlab_client as gl
import mr_template as tpl
import stack_manager as sm
import stale_checker as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(seconds: int) -> str:
    if seconds <= 0:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    return " ".join(parts) if parts else "< 1m"


def _current_branch() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "branch", "--show-current"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Hook handlers (called by git hooks, must never raise)
# ---------------------------------------------------------------------------

def _hook_checkout(prev_branch: str, new_branch: str) -> None:
    """Called by post-checkout hook."""
    cfg = load_config()
    min_secs = int(cfg.get("min_track_minutes", 2)) * 60
    statuses = cfg.get("jira_statuses", {})

    # Stop tracking previous branch
    if prev_branch:
        session = tt.stop_tracking(min_seconds=min_secs)
        if session:
            jira_key = session["jira_key"]
            ok = jira.add_worklog(
                jira_key,
                session["seconds"],
                session["started"],
                f"[git-jira-tracker] Branch: {prev_branch}",
            )
            status_label = "✓" if ok else "⏳ (guardado en pending)"
            print(f"[tracker] {jira_key}: {_fmt(session['seconds'])} imputados {status_label}")

    # Start tracking new branch
    new_key = extract_jira_key(new_branch)
    if new_key:
        tt.start_tracking(new_branch, new_key)
        print(f"[tracker] Iniciando tracking para {new_key} en '{new_branch}'")

        # Transition Jira status → En progreso
        status_in_progress = statuses.get("in_progress", "")
        if status_in_progress:
            jira.transition_issue(new_key, status_in_progress)


def _hook_commit() -> None:
    """Called by post-commit hook."""
    partial = tt.save_partial()
    if partial:
        key = partial["jira_key"]
        secs = partial["partial_seconds"]
        print(f"[tracker] Tiempo parcial guardado: {key} → {_fmt(secs)}")


# ---------------------------------------------------------------------------
# Command: mr
# ---------------------------------------------------------------------------

def cmd_mr(args) -> None:
    cfg = load_config()
    branch = _current_branch()
    if not branch:
        print("Error: no se pudo determinar la rama actual.")
        sys.exit(1)

    jira_key = extract_jira_key(branch)
    if not jira_key:
        print(f"La rama '{branch}' no tiene número de tarea Jira.")
        sys.exit(1)

    # If --ready, just mark existing MR as ready
    if args.ready:
        existing = gl.get_mr_for_branch(branch)
        if not existing:
            print(f"No se encontró MR abierta para la rama '{branch}'.")
            sys.exit(1)
        updated = gl.mark_ready(existing["iid"])
        if updated:
            print(f"✓ MR marcada como lista para review: {updated['web_url']}")
            # Transition Jira → En revisión
            statuses = cfg.get("jira_statuses", {})
            in_review = statuses.get("in_review", "")
            if in_review:
                jira.transition_issue(jira_key, in_review)
        else:
            print("Error al actualizar la MR.")
        return

    # Get Jira issue info
    print(f"Obteniendo tarea {jira_key} de Jira...")
    issue = jira.get_issue(jira_key)
    if not issue:
        print(f"No se pudo obtener la tarea {jira_key} de Jira.")
        sys.exit(1)

    # Get total tracked time for this branch
    total_secs = tt.get_branch_total_seconds(jira_key)

    # Check if stacked branch (has parent MR)
    parent_mr = sm.get_parent_mr(branch)
    parent_mr_url = parent_mr["mr_url"] if parent_mr else None
    parent_mr_title = None
    if parent_mr and parent_mr.get("mr_iid"):
        parent_data = gl.get_mr(parent_mr["mr_iid"])
        if parent_data:
            parent_mr_title = parent_data.get("title")

    # Determine target branch
    parent_branch = sm.get_parent_branch(branch)
    target_branch = parent_branch if parent_branch else cfg.get("default_target_branch", "develop")

    # Generate title and description
    title = tpl.generate_title(jira_key, issue["summary"], draft=True)
    description = tpl.generate_description(
        jira_summary=issue["summary"],
        jira_description=issue["description"],
        jira_url=issue["url"],
        time_spent_seconds=total_secs,
        parent_mr_url=parent_mr_url,
        parent_mr_title=parent_mr_title,
    )

    print(f"Creando MR en GitLab...")
    print(f"  Rama origen:  {branch}")
    print(f"  Rama destino: {target_branch}")
    print(f"  Título:       {title}")

    mr = gl.create_mr(
        source_branch=branch,
        target_branch=target_branch,
        title=title,
        description=description,
        draft=True,
    )

    if not mr:
        print("Error al crear la MR en GitLab.")
        sys.exit(1)

    print(f"\n✓ MR creada: {mr['web_url']}")

    # Register in stack
    sm.register_branch(branch, parent_branch, mr_iid=mr["iid"], mr_url=mr["web_url"])

    # Transition Jira status
    statuses = cfg.get("jira_statuses", {})
    in_review_draft = statuses.get("in_review_draft", "")
    in_progress = statuses.get("in_progress", "")
    if in_review_draft:
        jira.transition_issue(jira_key, in_review_draft)
    elif in_progress:
        # Keep in progress (draft MR)
        pass


# ---------------------------------------------------------------------------
# Command: stack
# ---------------------------------------------------------------------------

def cmd_stack(args) -> None:
    cfg = load_config()

    if args.list:
        sm.show_stack_tree(gitlab_client=gl)
        return

    if args.update:
        merged = args.update
        target = cfg.get("default_target_branch", "develop")
        print(f"Actualizando MRs hijas de '{merged}' → target '{target}'...")
        sm.update_stacked_mrs(merged, target, gitlab_client=gl)
        return

    # Create new stacked branch
    if not args.branch_name:
        print("Uso: python tracker.py stack <nombre-nueva-rama>")
        print("Ejemplo: python tracker.py stack feature/QMS-124-permisos")
        sys.exit(1)

    new_branch = args.branch_name
    ok = sm.create_stacked_branch(new_branch)
    if not ok:
        sys.exit(1)

    # Auto-create MR if --mr flag
    if args.mr:
        # Parse synthetic args for cmd_mr
        class FakeArgs:
            ready = False
        print(f"\nCreando MR para la rama encadenada...")
        cmd_mr(FakeArgs())


# ---------------------------------------------------------------------------
# Command: stale
# ---------------------------------------------------------------------------

def cmd_stale(args) -> None:
    cfg = load_config()
    stale_hours = float(cfg.get("stale_hours", 48))

    print(f"Buscando MRs sin actividad hace más de {int(stale_hours)}h...")
    stale_mrs = sc.check_stale_mrs(stale_hours)
    report = sc.format_stale_report(stale_mrs, stale_hours)
    print(report)

    if args.notify:
        sc.notify_stale(stale_mrs, stale_hours)


# ---------------------------------------------------------------------------
# Command: status
# ---------------------------------------------------------------------------

def cmd_status(_args) -> None:
    current = tt.get_current_state()
    today = tt.get_today_summary()

    print("\n── Tiempo acumulado hoy ──────────────────────")
    if not today:
        print("  Sin actividad registrada hoy.")
    else:
        for key, secs in sorted(today.items(), key=lambda x: -x[1]):
            print(f"  {key:20s}  {_fmt(secs)}")

    print("\n── Sesión activa ─────────────────────────────")
    if current:
        print(f"  Tarea:  {current['jira_key']}")
        print(f"  Rama:   {current['branch']}")
        print(f"  Tiempo: {_fmt(current['elapsed_seconds'])}")
    else:
        print("  No hay tracking activo.")
    print()


# ---------------------------------------------------------------------------
# Command: log
# ---------------------------------------------------------------------------

def cmd_log(_args) -> None:
    weekly = tt.get_weekly_summary()

    print("\n── Resumen semana actual ─────────────────────")
    if not weekly:
        print("  Sin actividad registrada esta semana.")
    else:
        total = sum(weekly.values())
        for key, secs in sorted(weekly.items(), key=lambda x: -x[1]):
            print(f"  {key:20s}  {_fmt(secs)}")
        print(f"\n  {'TOTAL':20s}  {_fmt(total)}")
    print()


# ---------------------------------------------------------------------------
# Command: pending
# ---------------------------------------------------------------------------

def cmd_pending(args) -> None:
    pending = jira.get_pending()

    if not pending:
        print("No hay tiempos pendientes de imputar.")
        return

    print(f"\n── Tiempos pendientes ({len(pending)}) ──────────────────")
    for p in pending:
        print(f"  {p.get('task','?'):15s}  {_fmt(p.get('seconds',0))}  (guardado: {p.get('saved_at','?')[:19]})")

    if args.retry:
        print("\nReintetando imputación...")
        ok, remaining = jira.retry_pending()
        print(f"✓ Imputados: {ok}   Pendientes: {remaining}")
    else:
        print("\nUsa 'python tracker.py pending --retry' para intentar imputarlos.")


# ---------------------------------------------------------------------------
# Command: sync
# ---------------------------------------------------------------------------

def cmd_sync(_args) -> None:
    print("Sincronizando tiempos pendientes con Jira...")
    ok, remaining = jira.retry_pending()
    print(f"✓ Imputados: {ok}   Siguen pendientes: {remaining}")


# ---------------------------------------------------------------------------
# Command: mrs
# ---------------------------------------------------------------------------

def cmd_mrs(_args) -> None:
    from datetime import datetime, timezone

    print("Obteniendo MRs abiertas...")
    mrs = gl.list_open_mrs()

    if not mrs:
        print("No hay MRs abiertas.")
        return

    print(f"\n── MRs abiertas ({len(mrs)}) ──────────────────────────")
    now = datetime.now(timezone.utc)

    for mr in mrs:
        iid = mr.get("iid")
        title = mr.get("title", "")[:60]
        state = mr.get("state", "")
        draft = mr.get("draft", False)
        web_url = mr.get("web_url", "")
        reviewer = gl.get_mr_reviewer(mr) or "⚠️  Sin asignar"

        last_dt = gl.get_mr_last_activity(iid)
        if last_dt:
            delta = now - last_dt
            hours = delta.total_seconds() / 3600
            if hours < 1:
                inactivity = f"{int(hours*60)}m"
            elif hours < 24:
                inactivity = f"{int(hours)}h"
            else:
                inactivity = f"{int(hours/24)}d"
        else:
            inactivity = "?"

        draft_tag = " [DRAFT]" if draft else ""
        print(f"\n  !{iid}{draft_tag} {title}")
        print(f"    Estado:     {state}")
        print(f"    Reviewer:   {reviewer}")
        print(f"    Inactividad:{inactivity}")
        print(f"    URL:        {web_url}")
    print()


# ---------------------------------------------------------------------------
# Internal hook commands
# ---------------------------------------------------------------------------

def cmd_hook_checkout(args) -> None:
    try:
        _hook_checkout(args.prev_branch, args.new_branch)
    except Exception as exc:
        # Hooks must NEVER fail
        pass


def cmd_hook_commit(_args) -> None:
    try:
        _hook_commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracker.py",
        description="git-jira-tracker — Automatización Git + GitLab + Jira",
    )
    sub = parser.add_subparsers(dest="command")

    # mr
    p_mr = sub.add_parser("mr", help="Crear MR en GitLab para la rama actual")
    p_mr.add_argument("--ready", action="store_true",
                      help="Quitar draft y marcar como lista para review")

    # stack
    p_stack = sub.add_parser("stack", help="Gestión de ramas encadenadas")
    p_stack.add_argument("branch_name", nargs="?", help="Nombre de la nueva rama apilada")
    p_stack.add_argument("--list", action="store_true", help="Mostrar árbol de ramas")
    p_stack.add_argument("--update", metavar="MERGED_BRANCH",
                         help="Actualizar hijas tras el merge de MERGED_BRANCH")
    p_stack.add_argument("--mr", action="store_true",
                         help="Crear MR automáticamente tras crear la rama")

    # stale
    p_stale = sub.add_parser("stale", help="Detectar MRs obsoletas")
    p_stale.add_argument("--notify", action="store_true",
                         help="Enviar notificación por el canal configurado")

    # status
    sub.add_parser("status", help="Tiempo acumulado hoy y sesión activa")

    # log
    sub.add_parser("log", help="Resumen de horas de la semana actual")

    # pending
    p_pending = sub.add_parser("pending", help="Tiempos pendientes de imputar")
    p_pending.add_argument("--retry", action="store_true", help="Reintentar imputación")

    # sync
    sub.add_parser("sync", help="Forzar sincronización de tiempos pendientes")

    # mrs
    sub.add_parser("mrs", help="Listar todas las MRs abiertas del proyecto")

    # Internal hook commands (called from git hooks)
    p_co = sub.add_parser("_hook_checkout", help=argparse.SUPPRESS)
    p_co.add_argument("prev_branch")
    p_co.add_argument("new_branch")

    sub.add_parser("_hook_commit", help=argparse.SUPPRESS)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "mr": cmd_mr,
        "stack": cmd_stack,
        "stale": cmd_stale,
        "status": cmd_status,
        "log": cmd_log,
        "pending": cmd_pending,
        "sync": cmd_sync,
        "mrs": cmd_mrs,
        "_hook_checkout": cmd_hook_checkout,
        "_hook_commit": cmd_hook_commit,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
