#!/bin/bash
# git-jira-tracker — Instalador para macOS / Linux
# Uso: bash install.sh [--repo /ruta/al/repo]
set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
RESET="\033[0m"

info()    { echo -e "${CYAN}[info]${RESET}  $*"; }
success() { echo -e "${GREEN}[ok]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET}  $*"; }
error()   { echo -e "${RED}[error]${RESET} $*"; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

TRACKER_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_REPO=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) TARGET_REPO="$2"; shift 2 ;;
        *)      shift ;;
    esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║       git-jira-tracker  installer        ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"

# ─────────────────────────────────────────────────────────────────────────────
header "1. Verificando Python 3..."
# ─────────────────────────────────────────────────────────────────────────────
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        VER=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 8 ]; then
            PYTHON_CMD="$cmd"
            success "Python $VER encontrado: $(which $cmd)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    error "Python 3.8+ no encontrado. Instálalo desde https://python.org o con Homebrew:"
    echo "  brew install python3"
    exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
header "2. Instalando dependencias Python..."
# ─────────────────────────────────────────────────────────────────────────────
REQS="requests"
VENV_DIR="$TRACKER_HOME/.venv"
INSTALL_OK=0

"$PYTHON_CMD" -m pip install --quiet --upgrade $REQS 2>/dev/null && INSTALL_OK=1

if [ "$INSTALL_OK" = "0" ]; then
    "$PYTHON_CMD" -m pip install --user --quiet --upgrade $REQS 2>/dev/null && INSTALL_OK=1
fi

# PEP 668 (Homebrew Python en macOS 14+) — usar venv
if [ "$INSTALL_OK" = "0" ]; then
    warn "pip install bloqueado (entorno gestionado). Creando venv en $VENV_DIR..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade $REQS && INSTALL_OK=1
    if [ "$INSTALL_OK" = "1" ]; then
        success "Entorno virtual creado: $VENV_DIR"
        # Patch PYTHON_CMD to use venv python for the rest of the installer
        PYTHON_CMD="$VENV_DIR/bin/python"
        warn "Los hooks usarán $PYTHON_CMD"
        warn "Si cambias de Python, reinstala: bash install.sh"
    fi
fi

if [ "$INSTALL_OK" = "0" ]; then
    error "No se pudieron instalar las dependencias."
    error "Instala manualmente: pip3 install requests"
    exit 1
fi
success "Dependencias instaladas: $REQS"

# ─────────────────────────────────────────────────────────────────────────────
header "3. Configuración de credenciales (.env)..."
# ─────────────────────────────────────────────────────────────────────────────
ENV_FILE="$TRACKER_HOME/.env"

if [ -f "$ENV_FILE" ]; then
    warn ".env ya existe. ¿Deseas reconfigurarlo? (s/N)"
    read -r RECONFIG
    [[ "$RECONFIG" =~ ^[sS]$ ]] || { info "Saltando configuración de .env"; SKIP_ENV=1; }
fi

if [ -z "$SKIP_ENV" ]; then
    echo ""
    echo "Introduce tus credenciales (pulsa Enter para dejar vacío y editar manualmente después):"
    echo ""

    read -rp "  JIRA_URL (ej: https://miempresa.atlassian.net): " JIRA_URL
    read -rp "  JIRA_USER (tu email de Jira): "                    JIRA_USER
    read -rp "  JIRA_TOKEN (token de API de Jira): "               JIRA_TOKEN
    read -rp "  GITLAB_URL (ej: https://gitlab.miempresa.com): "   GITLAB_URL
    read -rp "  GITLAB_TOKEN (token personal de GitLab): "         GITLAB_TOKEN
    read -rp "  GITLAB_PROJECT_ID (ID numérico del proyecto): "    GITLAB_PROJECT_ID

    cat > "$ENV_FILE" <<EOF
JIRA_URL=${JIRA_URL}
JIRA_USER=${JIRA_USER}
JIRA_TOKEN=${JIRA_TOKEN}
GITLAB_URL=${GITLAB_URL}
GITLAB_TOKEN=${GITLAB_TOKEN}
GITLAB_PROJECT_ID=${GITLAB_PROJECT_ID}
EOF
    chmod 600 "$ENV_FILE"
    success ".env creado en $ENV_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
header "4. Verificando conexión con Jira y GitLab..."
# ─────────────────────────────────────────────────────────────────────────────
(
    cd "$TRACKER_HOME"
    "$PYTHON_CMD" - <<'PYEOF'
import sys, os
sys.path.insert(0, os.getcwd())
from config_loader import load_dotenv
load_dotenv()

import requests, os as _os

errors = []

# Jira check
jira_url = _os.environ.get("JIRA_URL","").rstrip("/")
jira_user = _os.environ.get("JIRA_USER","")
jira_token = _os.environ.get("JIRA_TOKEN","")
if jira_url and jira_user and jira_token:
    try:
        r = requests.get(f"{jira_url}/rest/api/3/myself",
                         auth=(jira_user, jira_token),
                         headers={"Accept":"application/json"},
                         timeout=10)
        if r.status_code == 200:
            name = r.json().get("displayName","?")
            print(f"  ✓ Jira OK — conectado como: {name}")
        else:
            errors.append(f"Jira respondió {r.status_code}. Verifica URL/USER/TOKEN.")
    except Exception as e:
        errors.append(f"Jira: {e}")
else:
    print("  ⚠  Credenciales de Jira no configuradas (edita .env)")

# GitLab check
gl_url = _os.environ.get("GITLAB_URL","").rstrip("/")
gl_token = _os.environ.get("GITLAB_TOKEN","")
gl_project = _os.environ.get("GITLAB_PROJECT_ID","")
if gl_url and gl_token and gl_project:
    try:
        r = requests.get(f"{gl_url}/api/v4/projects/{gl_project}",
                         headers={"PRIVATE-TOKEN": gl_token},
                         timeout=10)
        if r.status_code == 200:
            pname = r.json().get("name_with_namespace","?")
            print(f"  ✓ GitLab OK — proyecto: {pname}")
        else:
            errors.append(f"GitLab respondió {r.status_code}. Verifica URL/TOKEN/PROJECT_ID.")
    except Exception as e:
        errors.append(f"GitLab: {e}")
else:
    print("  ⚠  Credenciales de GitLab no configuradas (edita .env)")

for e in errors:
    print(f"  ✗ {e}", file=sys.stderr)
PYEOF
) || warn "La verificación de conectividad tuvo errores (puedes continuar y editar .env)"

# ─────────────────────────────────────────────────────────────────────────────
header "5. Instalando hooks en el repositorio Git..."
# ─────────────────────────────────────────────────────────────────────────────

# Determine which repo to install into
if [ -z "$TARGET_REPO" ]; then
    # Try current directory first, then ask
    if git -C "$(pwd)" rev-parse --git-dir >/dev/null 2>&1; then
        TARGET_REPO="$(pwd)"
        info "Repositorio detectado: $TARGET_REPO"
    else
        echo ""
        read -rp "  Ruta al repositorio Git donde instalar los hooks: " TARGET_REPO
    fi
fi

GIT_DIR="$(git -C "$TARGET_REPO" rev-parse --git-dir 2>/dev/null || true)"
if [ -z "$GIT_DIR" ]; then
    warn "No se encontró repositorio Git en '$TARGET_REPO'. Los hooks NO se instalarán."
    warn "Puedes instalarlos manualmente más tarde: bash install.sh --repo /ruta/repo"
else
    HOOKS_DIR="$GIT_DIR/hooks"
    mkdir -p "$HOOKS_DIR"

    install_hook() {
        local HOOK_NAME="$1"
        local SRC="$TRACKER_HOME/hooks/$HOOK_NAME"
        local DEST="$HOOKS_DIR/$HOOK_NAME"

        # Build hook line that calls tracker
        HOOK_LINE="TRACKER_HOME=\"$TRACKER_HOME\" TRACKER_PYTHON=\"$PYTHON_CMD\" \"$TRACKER_HOME/hooks/$HOOK_NAME\" \"\$@\""

        if [ -f "$DEST" ]; then
            # Check if already installed
            if grep -q "git-jira-tracker" "$DEST" 2>/dev/null; then
                info "$HOOK_NAME ya instalado (sin cambios)"
                return
            fi
            # Concatenate — preserve existing hook
            info "$HOOK_NAME ya existe, concatenando..."
            echo "" >> "$DEST"
            echo "# ── git-jira-tracker ──────────────────────────" >> "$DEST"
            echo "$HOOK_LINE" >> "$DEST"
        else
            # Create new hook
            cat > "$DEST" <<EOF
#!/bin/sh
# git-jira-tracker — $HOOK_NAME
$HOOK_LINE
EOF
        fi
        chmod +x "$DEST"
        # Also make source hook executable
        chmod +x "$SRC"
        success "$HOOK_NAME instalado en $DEST"
    }

    install_hook "post-checkout"
    install_hook "post-commit"
fi

# ─────────────────────────────────────────────────────────────────────────────
header "6. Creando directorio de datos..."
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "$HOME/.jira-tracker"
success "Directorio de datos: ~/.jira-tracker/"

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║  Instalación completada.                             ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "${BOLD}Comandos disponibles:${RESET}"
echo "  python tracker.py status          — Tiempo de hoy y sesión activa"
echo "  python tracker.py log             — Resumen de horas de la semana"
echo "  python tracker.py mr              — Crear MR en GitLab (draft)"
echo "  python tracker.py mr --ready      — Marcar MR como lista para review"
echo "  python tracker.py stack <rama>    — Crear rama encadenada"
echo "  python tracker.py stack --list    — Ver árbol de ramas"
echo "  python tracker.py stack --update <rama-mergeada>"
echo "  python tracker.py stale           — Ver MRs obsoletas"
echo "  python tracker.py stale --notify  — Notificar MRs obsoletas"
echo "  python tracker.py mrs             — Listar todas las MRs abiertas"
echo "  python tracker.py pending         — Ver tiempos pendientes"
echo "  python tracker.py pending --retry — Reintentar imputación"
echo "  python tracker.py sync            — Sincronizar pendientes con Jira"
echo ""
echo -e "${CYAN}Archivos de datos en ~/.jira-tracker/${RESET}"
echo ""
