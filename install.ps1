# git-jira-tracker — Instalador para Windows (PowerShell)
# Uso: powershell -ExecutionPolicy Bypass -File install.ps1 [-Repo "C:\ruta\repo"]
param(
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"
$TrackerHome = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Info    { Write-Host "[info]  $args" -ForegroundColor Cyan }
function Write-Ok      { Write-Host "[ok]    $args" -ForegroundColor Green }
function Write-Warn    { Write-Host "[warn]  $args" -ForegroundColor Yellow }
function Write-Err     { Write-Host "[error] $args" -ForegroundColor Red }
function Write-Header  { Write-Host "`n$args" -ForegroundColor White }

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor White
Write-Host "║       git-jira-tracker  installer        ║" -ForegroundColor White
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor White

# ─────────────────────────────────────────────────────────────────────────────
Write-Header "1. Verificando Python 3..."
# ─────────────────────────────────────────────────────────────────────────────
$PythonCmd = $null
foreach ($cmd in @("py", "python", "python3")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]
            if ($major -ge 3 -and $minor -ge 8) {
                $PythonCmd = $cmd
                Write-Ok "Python $major.$minor encontrado: $(Get-Command $cmd -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)"
                break
            }
        }
    } catch { }
}

if (-not $PythonCmd) {
    Write-Err "Python 3.8+ no encontrado."
    Write-Host "  Descárgalo desde: https://www.python.org/downloads/"
    Write-Host "  O con winget:  winget install Python.Python.3"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Header "2. Instalando dependencias Python..."
# ─────────────────────────────────────────────────────────────────────────────
try {
    & $PythonCmd -m pip install --quiet --upgrade requests
    Write-Ok "Dependencias instaladas: requests"
} catch {
    try {
        & $PythonCmd -m pip install --user --quiet --upgrade requests
        Write-Ok "Dependencias instaladas (--user): requests"
    } catch {
        Write-Err "No se pudieron instalar dependencias. Ejecuta manualmente: pip install requests"
        exit 1
    }
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Header "3. Configuración de credenciales (.env)..."
# ─────────────────────────────────────────────────────────────────────────────
$EnvFile = Join-Path $TrackerHome ".env"
$SkipEnv = $false

if (Test-Path $EnvFile) {
    $resp = Read-Host "  .env ya existe. ¿Reconfigurarlo? (s/N)"
    if ($resp -notmatch "^[sS]$") {
        Write-Info "Saltando configuración de .env"
        $SkipEnv = $true
    }
}

if (-not $SkipEnv) {
    Write-Host ""
    Write-Host "  Introduce tus credenciales (Enter para dejar vacío y editar después):"
    Write-Host ""

    $JiraUrl     = Read-Host "  JIRA_URL (ej: https://miempresa.atlassian.net)"
    $JiraUser    = Read-Host "  JIRA_USER (tu email de Jira)"
    $JiraToken   = Read-Host "  JIRA_TOKEN (token de API de Jira)"
    $GitlabUrl   = Read-Host "  GITLAB_URL (ej: https://gitlab.miempresa.com)"
    $GitlabToken = Read-Host "  GITLAB_TOKEN (token personal de GitLab)"
    $GitlabPid   = Read-Host "  GITLAB_PROJECT_ID (ID numérico del proyecto)"

    @"
JIRA_URL=$JiraUrl
JIRA_USER=$JiraUser
JIRA_TOKEN=$JiraToken
GITLAB_URL=$GitlabUrl
GITLAB_TOKEN=$GitlabToken
GITLAB_PROJECT_ID=$GitlabPid
"@ | Set-Content -Path $EnvFile -Encoding UTF8
    Write-Ok ".env creado en $EnvFile"
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Header "4. Verificando conexión con Jira y GitLab..."
# ─────────────────────────────────────────────────────────────────────────────
Push-Location $TrackerHome
try {
    & $PythonCmd -c @"
import sys, os
sys.path.insert(0, r'$TrackerHome')
from config_loader import load_dotenv
load_dotenv()
import requests, os as _os

jira_url   = _os.environ.get('JIRA_URL','').rstrip('/')
jira_user  = _os.environ.get('JIRA_USER','')
jira_token = _os.environ.get('JIRA_TOKEN','')
if jira_url and jira_user and jira_token:
    try:
        r = requests.get(f'{jira_url}/rest/api/3/myself',
                         auth=(jira_user, jira_token),
                         headers={'Accept':'application/json'}, timeout=10)
        if r.status_code == 200:
            print('  OK Jira conectado como:', r.json().get('displayName','?'))
        else:
            print(f'  ERR Jira: {r.status_code}')
    except Exception as e:
        print(f'  ERR Jira: {e}')
else:
    print('  WARN Credenciales Jira no configuradas')

gl_url   = _os.environ.get('GITLAB_URL','').rstrip('/')
gl_token = _os.environ.get('GITLAB_TOKEN','')
gl_pid   = _os.environ.get('GITLAB_PROJECT_ID','')
if gl_url and gl_token and gl_pid:
    try:
        r = requests.get(f'{gl_url}/api/v4/projects/{gl_pid}',
                         headers={'PRIVATE-TOKEN': gl_token}, timeout=10)
        if r.status_code == 200:
            print('  OK GitLab conectado, proyecto:', r.json().get('name_with_namespace','?'))
        else:
            print(f'  ERR GitLab: {r.status_code}')
    except Exception as e:
        print(f'  ERR GitLab: {e}')
else:
    print('  WARN Credenciales GitLab no configuradas')
"@
} catch {
    Write-Warn "Verificación de conectividad falló (puedes continuar y editar .env)"
}
Pop-Location

# ─────────────────────────────────────────────────────────────────────────────
Write-Header "5. Instalando hooks en el repositorio Git..."
# ─────────────────────────────────────────────────────────────────────────────

# Detect repo
if (-not $Repo) {
    try {
        $gitRoot = & git rev-parse --show-toplevel 2>$null
        if ($gitRoot) {
            $Repo = $gitRoot.Trim()
            Write-Info "Repositorio detectado: $Repo"
        }
    } catch { }
    if (-not $Repo) {
        $Repo = Read-Host "  Ruta al repositorio Git donde instalar los hooks"
    }
}

$GitDir = $null
try {
    $GitDir = (& git -C $Repo rev-parse --git-dir 2>$null).Trim()
    if ($GitDir -and -not [System.IO.Path]::IsPathRooted($GitDir)) {
        $GitDir = Join-Path $Repo $GitDir
    }
} catch { }

if (-not $GitDir -or -not (Test-Path $GitDir)) {
    Write-Warn "No se encontró repositorio Git en '$Repo'. Hooks NO instalados."
    Write-Warn "Instálalos más tarde: powershell -File install.ps1 -Repo C:\ruta\repo"
} else {
    $HooksDir = Join-Path $GitDir "hooks"
    if (-not (Test-Path $HooksDir)) { New-Item -ItemType Directory -Path $HooksDir | Out-Null }

    function Install-Hook {
        param([string]$HookName)
        $SrcHook = Join-Path $TrackerHome "hooks\$HookName"
        $Dest    = Join-Path $HooksDir $HookName

        # Windows hook wrapper — calls the sh hook via Git's bundled sh.exe
        $GitExe  = (Get-Command git).Source
        $GitSh   = Join-Path (Split-Path (Split-Path $GitExe)) "bin\sh.exe"
        if (-not (Test-Path $GitSh)) {
            $GitSh = "sh"
        }

        # Line that invokes the real hook
        $CallLine = "TRACKER_HOME=`"$TrackerHome`" `"$GitSh`" `"$SrcHook`" `"`$@`""

        if (Test-Path $Dest) {
            $existing = Get-Content $Dest -Raw
            if ($existing -match "git-jira-tracker") {
                Write-Info "$HookName ya instalado (sin cambios)"
                return
            }
            Write-Info "$HookName ya existe, concatenando..."
            Add-Content -Path $Dest -Value "`n# ── git-jira-tracker ──"
            Add-Content -Path $Dest -Value $CallLine
        } else {
            @"
#!/bin/sh
# git-jira-tracker — $HookName
$CallLine
"@ | Set-Content -Path $Dest -Encoding UTF8
        }
        Write-Ok "$HookName instalado en $Dest"
    }

    Install-Hook "post-checkout"
    Install-Hook "post-commit"
}

# ─────────────────────────────────────────────────────────────────────────────
Write-Header "6. Creando directorio de datos..."
# ─────────────────────────────────────────────────────────────────────────────
$DataDir = Join-Path $env:USERPROFILE ".jira-tracker"
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
Write-Ok "Directorio de datos: $DataDir"

# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║  Instalación completada.                             ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "Comandos disponibles (desde el directorio del tracker):" -ForegroundColor White
Write-Host "  py tracker.py status          — Tiempo de hoy"
Write-Host "  py tracker.py log             — Resumen de la semana"
Write-Host "  py tracker.py mr              — Crear MR en GitLab (draft)"
Write-Host "  py tracker.py mr --ready      — Marcar MR como lista para review"
Write-Host "  py tracker.py stack <rama>    — Crear rama encadenada"
Write-Host "  py tracker.py stack --list    — Ver árbol de ramas"
Write-Host "  py tracker.py stale           — Ver MRs obsoletas"
Write-Host "  py tracker.py stale --notify  — Notificar MRs obsoletas"
Write-Host "  py tracker.py mrs             — Listar MRs abiertas"
Write-Host "  py tracker.py pending --retry — Reintentar imputación pendiente"
Write-Host "  py tracker.py sync            — Sincronizar tiempos con Jira"
Write-Host ""
Write-Host "Archivos de datos en: $DataDir" -ForegroundColor Cyan
Write-Host ""
