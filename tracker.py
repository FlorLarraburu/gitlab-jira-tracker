#!/usr/bin/env python3
"""
git-jira-tracker — CLI principal
Uso: tracker <comando> [opciones]
"""

import argparse
import subprocess
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
    try:
        r = subprocess.run(["git", "branch", "--show-current"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def _current_repo() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Hook handlers (called by git hooks — must never raise)
# ---------------------------------------------------------------------------

def _hook_checkout(prev_branch: str, new_branch: str, repo: str) -> None:
    """
    Called by post-checkout hook.
    Stops whatever is currently running (any repo) and starts the new branch.
    One timer at a time, always.
    """
    cfg = load_config()
    min_secs = int(cfg.get("min_track_minutes", 2)) * 60
    statuses = cfg.get("jira_statuses", {})

    # Stop whatever was running — even if it was in a different repo
    session = tt.stop_tracking(min_seconds=min_secs)
    if session:
        jira_key = session["jira_key"]
        ok = jira.add_worklog(
            jira_key,
            session["seconds"],
            session["started"],
            f"[git-jira-tracker] Branch: {session['branch']}",
        )
        status_label = "✓" if ok else "⏳ (guardado en pending)"
        print(f"[tracker] {jira_key}: {_fmt(session['seconds'])} imputados {status_label}")

    # Start tracking new branch if it has a Jira key
    new_key = extract_jira_key(new_branch)
    if new_key:
        tt.start_tracking(new_branch, new_key, repo)
        print(f"[tracker] Iniciando tracking → {new_key} en '{new_branch}'")
        status_in_progress = statuses.get("in_progress", "")
        if status_in_progress:
            jira.transition_issue(new_key, status_in_progress)


def _hook_commit(repo: str) -> None:
    """Called by post-commit hook."""
    if not repo:
        return
    partial = tt.save_partial(repo)
    if partial:
        key = partial["jira_key"]
        secs = partial["partial_seconds"]
        print(f"[tracker] Parcial guardado: {key} → {_fmt(secs)}")


# ---------------------------------------------------------------------------
# Command: mr
# ---------------------------------------------------------------------------

def cmd_mr(args) -> None:
    cfg = load_config()
    branch = _current_branch()
    repo = _current_repo()

    if not branch:
        print("Error: no se pudo determinar la rama actual.")
        sys.exit(1)

    jira_key = extract_jira_key(branch)
    if not jira_key:
        print(f"La rama '{branch}' no tiene número de tarea Jira.")
        sys.exit(1)

    # --ready: mark existing MR as ready for review
    if args.ready:
        existing = gl.get_mr_for_branch(branch)
        if not existing:
            print(f"No se encontró MR abierta para '{branch}'.")
            sys.exit(1)
        updated = gl.mark_ready(existing["iid"])
        if updated:
            print(f"✓ MR lista para review: {updated['web_url']}")
            statuses = cfg.get("jira_statuses", {})
            in_review = statuses.get("in_review", "")
            if in_review:
                jira.transition_issue(jira_key, in_review)
        else:
            print("Error al actualizar la MR.")
        return

    # Fetch Jira issue
    print(f"Obteniendo {jira_key} de Jira...")
    issue = jira.get_issue(jira_key)
    if not issue:
        print(f"No se pudo obtener {jira_key} de Jira.")
        sys.exit(1)

    total_secs = tt.get_branch_total_seconds(jira_key)

    # Stacked MR: check for parent
    parent_mr = sm.get_parent_mr(branch)
    parent_mr_url = parent_mr["mr_url"] if parent_mr else None
    parent_mr_title = None
    if parent_mr and parent_mr.get("mr_iid"):
        parent_data = gl.get_mr(parent_mr["mr_iid"])
        if parent_data:
            parent_mr_title = parent_data.get("title")

    parent_branch = sm.get_parent_branch(branch)
    target_branch = parent_branch if parent_branch else cfg.get("default_target_branch", "develop")

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
    sm.register_branch(branch, parent_branch, mr_iid=mr["iid"], mr_url=mr["web_url"])

    statuses = cfg.get("jira_statuses", {})
    in_review_draft = statuses.get("in_review_draft", "")
    if in_review_draft:
        jira.transition_issue(jira_key, in_review_draft)


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
        print(f"Actualizando MRs hijas de '{merged}' → '{target}'...")
        sm.update_stacked_mrs(merged, target, gitlab_client=gl)
        return

    if not args.branch_name:
        print("Uso: tracker stack <nombre-nueva-rama>")
        print("Ejemplo: tracker stack feature/QMS-124-permisos")
        sys.exit(1)

    ok = sm.create_stacked_branch(args.branch_name)
    if not ok:
        sys.exit(1)

    if args.mr:
        class FakeArgs:
            ready = False
        print("\nCreando MR para la rama encadenada...")
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
    today   = tt.get_today_summary()
    current = tt.get_current_state()

    print("\n── Tiempo acumulado hoy ──────────────────────")
    if not today:
        print("  Sin actividad registrada hoy.")
    else:
        for key, secs in sorted(today.items(), key=lambda x: -x[1]):
            print(f"  {key:20s}  {_fmt(secs)}")

    print("\n── Sesión activa ─────────────────────────────")
    if not current:
        print("  No hay tracking activo.")
    else:
        repo_name = Path(current["repo"]).name if current.get("repo") else "?"
        print(f"  Tarea:   {current['jira_key']}")
        print(f"  Rama:    {current['branch']}")
        print(f"  Repo:    {repo_name}")
        print(f"  Tiempo:  {_fmt(current['elapsed_seconds'])}")
    print()


# ---------------------------------------------------------------------------
# Command: log
# ---------------------------------------------------------------------------

def cmd_log(_args) -> None:
    weekly = tt.get_weekly_summary()
    print("\n── Resumen semana actual ─────────────────────")
    if not weekly:
        print("  Sin actividad esta semana.")
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
        print(f"  {p.get('task','?'):15s}  {_fmt(p.get('seconds',0))}"
              f"  (guardado: {p.get('saved_at','?')[:19]})")
    if args.retry:
        print("\nReintetando imputación...")
        ok, remaining = jira.retry_pending()
        print(f"✓ Imputados: {ok}   Pendientes: {remaining}")
    else:
        print("\nUsa 'tracker pending --retry' para intentar imputarlos.")


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
        reviewer = gl.get_mr_reviewer(mr) or "⚠  Sin asignar"
        last_dt = gl.get_mr_last_activity(iid)
        if last_dt:
            hours = (now - last_dt).total_seconds() / 3600
            inactivity = (f"{int(hours*60)}m" if hours < 1
                         else f"{int(hours)}h" if hours < 24
                         else f"{int(hours/24)}d")
        else:
            inactivity = "?"
        draft_tag = " [DRAFT]" if draft else ""
        print(f"\n  !{iid}{draft_tag} {title}")
        print(f"    Estado:      {state}")
        print(f"    Reviewer:    {reviewer}")
        print(f"    Inactividad: {inactivity}")
        print(f"    URL:         {web_url}")
    print()


# ---------------------------------------------------------------------------
# Command: doctor
# ---------------------------------------------------------------------------

def cmd_doctor(_args) -> None:
    import sys as _sys
    import importlib

    OK   = "✓"
    WARN = "⚠ "
    FAIL = "✗"

    issues = []

    def _line(symbol, label, value="", note=""):
        label_col = f"{label:<22}"
        note_part = f"  {note}" if note else ""
        print(f"  {symbol}  {label_col} {value}{note_part}")

    def _ok(label, value="", note=""):
        _line(OK, label, value, note)

    def _warn(label, value="", note=""):
        issues.append(("warn", label))
        _line(WARN, label, value, note)

    def _fail(label, value="", note=""):
        issues.append(("fail", label))
        _line(FAIL, label, value, note)

    print("\n── git-jira-tracker doctor ───────────────────────────\n")

    # ── Python version ────────────────────────────────────────────────────────
    v = _sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v.major >= 3 and v.minor >= 8:
        _ok("Python", ver_str)
    else:
        _fail("Python", ver_str, "requiere 3.8+")

    # ── requests ─────────────────────────────────────────────────────────────
    try:
        import requests as _req
        _ok("requests", getattr(_req, "__version__", "instalado"))
    except ImportError:
        _fail("requests", "no instalado", "pip install requests")

    # ── .env ─────────────────────────────────────────────────────────────────
    from config_loader import _script_dir
    env_candidates = [
        Path.cwd() / ".env",
        _script_dir() / ".env",
        Path.home() / ".jira-tracker" / ".env",
    ]
    env_found = next((p for p in env_candidates if p.exists()), None)
    if env_found:
        _ok(".env", str(env_found))
    else:
        _warn(".env", "no encontrado", "copia .env.example a .env y rellénalo")

    print()

    # ── Environment variables ────────────────────────────────────────────────
    required_vars = {
        "JIRA_URL":          "URL base de Jira",
        "JIRA_USER":         "Email de Jira",
        "JIRA_TOKEN":        "Token API de Jira",
        "GITLAB_URL":        "URL base de GitLab",
        "GITLAB_TOKEN":      "Token personal de GitLab",
        "GITLAB_PROJECT_ID": "ID numérico del proyecto",
    }
    for var, desc in required_vars.items():
        val = os.environ.get(var, "")
        if val:
            # Mask tokens
            display = val if "URL" in var or var == "JIRA_USER" or var == "GITLAB_PROJECT_ID" \
                else f"{val[:4]}{'*' * (len(val) - 4)}"
            _ok(var, display)
        else:
            _fail(var, "no configurado", desc)

    print()

    # ── Jira connectivity ─────────────────────────────────────────────────────
    jira_url  = os.environ.get("JIRA_URL", "").rstrip("/")
    jira_user = os.environ.get("JIRA_USER", "")
    jira_tok  = os.environ.get("JIRA_TOKEN", "")
    if jira_url and jira_user and jira_tok:
        try:
            import requests as _req
            r = _req.get(f"{jira_url}/rest/api/3/myself",
                         auth=(jira_user, jira_tok),
                         headers={"Accept": "application/json"},
                         timeout=10)
            if r.status_code == 200:
                name = r.json().get("displayName", "?")
                _ok("Jira API", f"conectado como {name}")
            else:
                _fail("Jira API", f"HTTP {r.status_code}",
                      "verifica JIRA_URL / JIRA_USER / JIRA_TOKEN")
        except Exception as e:
            _fail("Jira API", "sin conexión", str(e)[:60])
    else:
        _warn("Jira API", "no comprobado", "faltan credenciales")

    # ── GitLab connectivity ───────────────────────────────────────────────────
    gl_url = os.environ.get("GITLAB_URL", "").rstrip("/")
    gl_tok = os.environ.get("GITLAB_TOKEN", "")
    gl_pid = os.environ.get("GITLAB_PROJECT_ID", "")
    if gl_url and gl_tok and gl_pid:
        try:
            import requests as _req
            r = _req.get(f"{gl_url}/api/v4/projects/{gl_pid}",
                         headers={"PRIVATE-TOKEN": gl_tok},
                         timeout=10)
            if r.status_code == 200:
                pname = r.json().get("name_with_namespace", "?")
                _ok("GitLab API", f"proyecto: {pname}")
            else:
                _fail("GitLab API", f"HTTP {r.status_code}",
                      "verifica GITLAB_URL / GITLAB_TOKEN / GITLAB_PROJECT_ID")
        except Exception as e:
            _fail("GitLab API", "sin conexión", str(e)[:60])
    else:
        _warn("GitLab API", "no comprobado", "faltan credenciales")

    print()

    # ── Git hooks ─────────────────────────────────────────────────────────────
    repo = _current_repo()
    if repo:
        _ok("Repo actual", repo)
        git_dir_r = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, cwd=repo
        )
        git_dir = (git_dir_r.stdout.strip() if git_dir_r.returncode == 0 else ".git")
        if not Path(git_dir).is_absolute():
            git_dir = str(Path(repo) / git_dir)

        for hook in ("post-checkout", "post-commit"):
            hook_path = Path(git_dir) / "hooks" / hook
            if hook_path.exists():
                content = hook_path.read_text()
                if "git-jira-tracker" in content:
                    _ok(f"hook/{hook}", "instalado")
                else:
                    _warn(f"hook/{hook}", "existe pero sin tracker",
                          "reinstala con install.sh")
            else:
                _warn(f"hook/{hook}", "no instalado",
                      "ejecuta install.sh --repo <ruta>")
    else:
        _warn("Repo actual", "no detectado",
              "ejecuta desde dentro de un repo git")

    print()

    # ── Data directory ────────────────────────────────────────────────────────
    data_dir = Path.home() / ".jira-tracker"
    if data_dir.exists():
        _ok("Directorio datos", str(data_dir))
    else:
        _warn("Directorio datos", str(data_dir), "se creará en el primer uso")

    # ── Pending worklogs ──────────────────────────────────────────────────────
    pending = jira.get_pending()
    if not pending:
        _ok("Worklogs pendientes", "ninguno")
    else:
        _warn("Worklogs pendientes", str(len(pending)),
              "ejecuta: tracker pending --retry")

    # ── Active session ────────────────────────────────────────────────────────
    current = tt.get_current_state()
    if current:
        _ok("Sesión activa",
            f"{current['jira_key']} — {_fmt(current['elapsed_seconds'])}"
            f"  ({Path(current['repo']).name if current.get('repo') else '?'})")
    else:
        _ok("Sesión activa", "ninguna")

    # ── config.json ───────────────────────────────────────────────────────────
    cfg = load_config()
    _ok("config.json", f"min_track={cfg.get('min_track_minutes')}m  "
        f"stale={cfg.get('stale_hours')}h  "
        f"target={cfg.get('default_target_branch')}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 53)
    fails  = sum(1 for kind, _ in issues if kind == "fail")
    warns  = sum(1 for kind, _ in issues if kind == "warn")
    if not issues:
        print(f"  {OK}  Todo OK. El tracker está listo.\n")
    else:
        if fails:
            print(f"  {FAIL}  {fails} error(es) — el tracker puede no funcionar.")
        if warns:
            print(f"  {WARN}  {warns} advertencia(s).")
        print()


# ---------------------------------------------------------------------------
# Internal hook commands (called from git hooks)
# ---------------------------------------------------------------------------

def cmd_hook_checkout(args) -> None:
    try:
        _hook_checkout(args.prev_branch, args.new_branch, args.repo_root)
    except Exception:
        pass


def cmd_hook_commit(args) -> None:
    try:
        _hook_commit(args.repo_root)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracker",
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
                         help="Crear MR tras crear la rama")

    # stale
    p_stale = sub.add_parser("stale", help="Detectar MRs obsoletas")
    p_stale.add_argument("--notify", action="store_true",
                         help="Enviar notificación por el canal configurado")

    # status
    sub.add_parser("status", help="Tiempo de hoy y sesiones activas (todos los repos)")

    # log
    sub.add_parser("log", help="Resumen de horas de la semana actual")

    # pending
    p_pending = sub.add_parser("pending", help="Tiempos pendientes de imputar")
    p_pending.add_argument("--retry", action="store_true",
                           help="Reintentar imputación ahora")

    # sync
    sub.add_parser("sync", help="Forzar sincronización de tiempos pendientes")

    # mrs
    sub.add_parser("mrs", help="Listar todas las MRs abiertas del proyecto")

    # doctor
    sub.add_parser("doctor", help="Comprobar configuración y conectividad")

    # Internal hook commands
    p_co = sub.add_parser("_hook_checkout", help=argparse.SUPPRESS)
    p_co.add_argument("prev_branch")
    p_co.add_argument("new_branch")
    p_co.add_argument("repo_root")

    p_cm = sub.add_parser("_hook_commit", help=argparse.SUPPRESS)
    p_cm.add_argument("repo_root")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "mr":             cmd_mr,
        "stack":          cmd_stack,
        "stale":          cmd_stale,
        "status":         cmd_status,
        "log":            cmd_log,
        "pending":        cmd_pending,
        "sync":           cmd_sync,
        "mrs":            cmd_mrs,
        "doctor":         cmd_doctor,
        "_hook_checkout": cmd_hook_checkout,
        "_hook_commit":   cmd_hook_commit,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
