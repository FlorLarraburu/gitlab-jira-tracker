# git-jira-tracker

Automatiza completamente el flujo de trabajo entre **Git**, **GitLab** y **Jira**.

- Trackea el tiempo por rama y lo imputa automáticamente como worklog en Jira.
- Crea Merge Requests desde terminal con descripción autogenerada.
- Gestiona ramas encadenadas (stacked MRs).
- Detecta y notifica MRs obsoletas.
- Cambia estados de tareas Jira según el ciclo de vida de la rama.

---

## Tabla de contenidos

1. [Qué hace y por qué](#qué-hace-y-por-qué)
2. [Requisitos previos](#requisitos-previos)
3. [Cómo obtener los tokens](#cómo-obtener-los-tokens)
4. [Instalación en macOS / Linux](#instalación-en-macos--linux)
5. [Instalación en Windows](#instalación-en-windows)
6. [Instalarlo en un repo nuevo](#instalarlo-en-un-repo-nuevo)
7. [Convención de nombrado de ramas](#convención-de-nombrado-de-ramas)
8. [Comandos disponibles](#comandos-disponibles)
9. [Cómo usar MRs encadenadas](#cómo-usar-mrs-encadenadas)
10. [Configuración de config.json](#configuración-de-configjson)
11. [Notificaciones de MRs obsoletas](#notificaciones-de-mrs-obsoletas)
12. [Cómo desactivarlo temporalmente](#cómo-desactivarlo-temporalmente)
13. [Troubleshooting](#troubleshooting)
14. [Cómo contribuir](#cómo-contribuir)

---

## Qué hace y por qué

Cuando cambias de rama, el tracker detecta automáticamente la tarea Jira asociada, registra el tiempo trabajado y lo imputa como worklog al salir de esa rama. Nunca más tendrás que acordarte de imputar horas a mano.

Desde terminal puedes crear MRs con un solo comando: el tracker consulta Jira, genera el título y la descripción estándar y crea la MR en GitLab en modo draft. Cuando está lista, `mr --ready` la marca para review y transiciona la tarea en Jira.

---

## Requisitos previos

- Python 3.8 o superior
- Git 2.x
- Acceso a Jira con permisos de escritura (worklogs y transiciones)
- Acceso a GitLab con permisos de creación de MRs en el proyecto
- `pip` disponible (`python3 -m pip` o `py -m pip` en Windows)

---

## Cómo obtener los tokens

### Token de Jira (Atlassian API Token)

1. Entra en [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Haz clic en **Create API token**
3. Ponle un nombre (ej: `git-jira-tracker`) y copia el token generado
4. Guárdalo como `JIRA_TOKEN` en el `.env`
5. `JIRA_USER` es tu dirección de email de Atlassian
6. `JIRA_URL` es la URL base de tu Jira, ej: `https://miempresa.atlassian.net`

### Token de GitLab (Personal Access Token)

1. En GitLab, ve a **User Settings → Access Tokens** (o `/profile/personal_access_tokens`)
2. Crea un token con los scopes: `api`, `read_user`
3. Guárdalo como `GITLAB_TOKEN` en el `.env`
4. `GITLAB_PROJECT_ID`: ve al proyecto en GitLab → **Settings → General**, verás el ID numérico al inicio de la página

---

## Instalación en macOS / Linux

```bash
# 1. Clona o descarga el proyecto
git clone <url-del-repo> ~/git-jira-tracker
cd ~/git-jira-tracker

# 2. Ejecuta el instalador
bash install.sh

# 3. El instalador te pedirá:
#    - Tus credenciales (genera el .env)
#    - La ruta al repo donde instalar los hooks
#    - Verifica la conexión con Jira y GitLab automáticamente

# 4. Si quieres instalar en un repo específico:
bash install.sh --repo /ruta/a/tu/repo
```

> El instalador nunca sobreescribe hooks existentes: los concatena.

---

## Instalación en Windows

Abre **PowerShell como administrador**:

```powershell
# Permitir ejecución de scripts (solo si no está ya habilitado)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Ir al directorio del tracker
cd C:\Users\TuUsuario\git-jira-tracker

# Ejecutar el instalador
powershell -ExecutionPolicy Bypass -File install.ps1

# Con repo específico:
powershell -ExecutionPolicy Bypass -File install.ps1 -Repo "C:\repos\mi-proyecto"
```

Los hooks en Windows usan el `sh.exe` incluido con Git para Windows, por lo que no necesitas instalar nada adicional.

---

## Instalarlo en un repo nuevo

Después de clonar un proyecto nuevo, instala los hooks apuntando a ese repo:

```bash
# macOS/Linux
bash ~/git-jira-tracker/install.sh --repo /ruta/al/nuevo-repo

# Windows
powershell -ExecutionPolicy Bypass -File C:\git-jira-tracker\install.ps1 -Repo "C:\repos\nuevo-repo"
```

El instalador detecta si los hooks ya existen y los concatena sin borrar lo anterior.

---

## Convención de nombrado de ramas

El tracker extrae el número de tarea del nombre de la rama. El patrón por defecto es:

```
tipo/PROYECTO-123-descripcion-corta
```

Ejemplos válidos:
```
feature/QMS-123-login-oauth
fix/QMS-456-null-pointer-en-checkout
chore/QMS-789-actualizar-dependencias
```

El patrón es configurable en `config.json` (`branch_pattern`). Si la rama no tiene número de tarea Jira, el tracker la ignora silenciosamente.

---

## Autocompletado

Tras la instalación, escribe `tracker ` y pulsa **Tab** para ver todos los comandos. Funciona con bash y zsh. El instalador lo configura automáticamente.

Si necesitas activarlo manualmente:
```bash
# Bash
echo "source ~/git-jira-tracker/completions/tracker.bash" >> ~/.bashrc

# Zsh
mkdir -p ~/.zsh/completions
ln -s ~/git-jira-tracker/completions/_tracker ~/.zsh/completions/_tracker
echo 'fpath=(~/.zsh/completions $fpath)' >> ~/.zshrc
echo 'autoload -Uz compinit && compinit' >> ~/.zshrc
```

---

## Comandos disponibles

Tras la instalación el comando es simplemente `tracker`. Si por algún motivo no está en el PATH, el fallback siempre es `python tracker.py <comando>`.

### `tracker status`
Muestra el tiempo acumulado hoy y la sesión activa en este momento.

```
── Tiempo acumulado hoy ──────────────────────
  QMS-123               2h 15m
  QMS-456               45m

── Sesión activa ─────────────────────────────
  Tarea:   QMS-123
  Rama:    feature/QMS-123-login
  Repo:    mi-proyecto
  Tiempo:  1h 30m
```

**Solo hay un timer activo a la vez.** Cuando haces `git checkout` en cualquier repo, el timer anterior se para (y el tiempo se imputa) y arranca el nuevo. Si tienes dos proyectos abiertos, el tracker no puede saber en cuál estás trabajando sin monitorizar el foco de ventanas a nivel de sistema — el `checkout` es la señal más fiable.

**Detección de inactividad (idle gap)**

El tracker mantiene un latido (`last_active_ts`) que se actualiza con cada commit y cada vez que ejecutas `tracker status` o `tracker log`. Si al hacer checkout el tiempo desde el último latido supera `idle_threshold_minutes` (default: 60 min), el tiempo muerto más allá del umbral se descarta:

```
Trabajas de 9:00 a 13:00, último status a las 12:55
Te vas a comer. Vuelves a las 14:30 y haces checkout.
  Tiempo desde último latido: 1h 35m > umbral 60min
  Descartado:  35 minutos
  Imputado:    4h 55m  (no las 5h 30m de reloj)
```

Para pausar el timer de forma explícita antes de una reunión o del almuerzo usa `tracker stop`. Para que el latido se renueve mientras trabajas sin hacer commits, basta con ejecutar `tracker status` de vez en cuando.

---

### `tracker doctor`
Diagnóstico completo del entorno. Útil para onboarding y troubleshooting.

```
── git-jira-tracker doctor ───────────────────────────

  ✓  Python                3.12.0
  ✓  requests              2.31.0
  ✓  .env                  /Users/user/git-jira-tracker/.env

  ✓  JIRA_URL              https://miempresa.atlassian.net
  ✓  JIRA_USER             user@email.com
  ✓  JIRA_TOKEN            abcd************
  ✓  GITLAB_URL            https://gitlab.miempresa.com
  ✓  GITLAB_TOKEN          glpat***********
  ✓  GITLAB_PROJECT_ID     123

  ✓  Jira API              conectado como: Nombre Apellido
  ✓  GitLab API            proyecto: miempresa/mi-repo

  ✓  Repo actual           /Users/user/mi-proyecto
  ✓  hook/post-checkout    instalado
  ✓  hook/post-commit      instalado

  ✓  Worklogs pendientes   ninguno
  ✓  Sesiones activas      1
  ✓  config.json           min_track=2m  stale=48h  target=develop

─────────────────────────────────────────────────────
  ✓  Todo OK. El tracker está listo.
```

---

### `tracker stop`
Pausa el timer en el momento actual. El tiempo acumulado se preserva pero **no se imputa** en Jira todavía. Usa esto cuando te vas a comer, a una reunión, o simplemente cuando sabes que vas a dejar el ordenador un rato.

```bash
tracker stop
# [tracker] Timer pausado — QMS-123 — 1h 45m acumulados.
#           Usa 'tracker restart' para reanudar.
```

`tracker status` muestra `⏸ PAUSADO` mientras el timer está detenido.

---

### `tracker restart`
Reanuda un timer pausado con `tracker stop`. El tiempo acumulado antes de la pausa se conserva y el contador continúa desde donde se dejó.

```bash
tracker restart
# [tracker] Timer reanudado — QMS-123 (1h 45m ya acumulados)
```

El timer también se reanuda automáticamente al hacer `git checkout` a la misma rama o al hacer `git commit`.

---

### `tracker help`
Muestra todos los comandos disponibles con una descripción breve.

---

### `tracker log`
Resumen de horas por tarea de la semana actual (lunes a domingo).

---

### `tracker mr`
Crea una MR en GitLab para la rama actual en modo **draft**:

1. Detecta la rama actual y extrae el número de tarea Jira
2. Consulta Jira para obtener título y descripción
3. Calcula el tiempo total imputado en esa rama
4. Genera el título: `Draft: [QMS-123] Título de la tarea`
5. Genera la descripción con la plantilla estándar
6. Crea la MR en GitLab apuntando a `develop` (o a la rama padre si es encadenada)

```bash
tracker mr
```

---

### `tracker mr --ready`
Quita el estado draft de la MR y la marca como lista para review.
Transiciona la tarea en Jira a "En revisión".

```bash
tracker mr --ready
```

---

### `python tracker.py pending`
Muestra los tiempos que no pudieron imputarse en Jira (fallos de API).

```bash
python tracker.py pending          # solo muestra
python tracker.py pending --retry  # muestra y reintenta
```

---

### `python tracker.py sync`
Fuerza el reintento de todos los tiempos pendientes.

---

### `tracker mrs`
Lista todas las MRs abiertas del proyecto con su estado, reviewer asignado y tiempo sin actividad.

---

### `tracker stale`
Lista las MRs sin actividad durante más de X horas (configurable en `config.json`).

```bash
tracker stale            # solo lista
tracker stale --notify   # lista y envía notificación
```

---

### `tracker stack <nueva-rama>`
Crea una nueva rama apilada sobre la actual (en lugar de sobre `develop`).

```bash
# Estando en feature/QMS-123-login
tracker stack feature/QMS-124-permisos

# Crea feature/QMS-124-permisos desde feature/QMS-123-login
# y registra la relación padre→hijo
```

### `tracker stack --list`
Muestra el árbol de ramas encadenadas con el estado de cada MR en GitLab.

```
Stack tree for: /home/user/mi-proyecto
──────────────────────────────────────────────────
└── feature/QMS-123-login → !42 [draft]
    └── feature/QMS-124-permisos → !45 [opened]
        └── feature/QMS-125-dashboard (sin MR)
```

### `tracker stack --update <rama-mergeada>`
Cuando se mergea una MR padre, actualiza el target de las MRs hijas a `develop` y hace rebase.

```bash
tracker stack --update feature/QMS-123-login
```

---

## Cómo usar MRs encadenadas

Las MRs encadenadas permiten trabajar en tareas dependientes sin esperar al merge de la anterior.

```
develop
  └── feature/QMS-123-login         ← en review
        └── feature/QMS-124-permisos  ← trabajando aquí
```

**Flujo paso a paso:**

```bash
# 1. Estás en develop, comienzas con QMS-123
git checkout -b feature/QMS-123-login

# Trabajas... haces commits... y creas la MR
tracker mr

# 2. Mientras QMS-123 está en review, comienzas QMS-124
#    Estando en feature/QMS-123-login:
tracker stack feature/QMS-124-permisos
# → Crea la rama desde QMS-123, no desde develop
# → Registra la relación padre→hijo

# 3. Trabajas en QMS-124, creas su MR
tracker mr
# → El target de esta MR será feature/QMS-123-login
# → La descripción incluye el link a la MR padre

# 4. Se mergea QMS-123. Actualiza QMS-124 automáticamente:
tracker stack --update feature/QMS-123-login
# → Cambia el target de !45 a develop
# → Hace rebase de feature/QMS-124-permisos sobre develop

# 5. Ver el árbol en cualquier momento
tracker stack --list
```

---

## Configuración de config.json

```json
{
  "default_target_branch": "develop",
  "min_track_minutes": 2,
  "stale_hours": 48,
  "notify_channel": "",
  "jira_statuses": {
    "in_progress": "En progreso",
    "in_review": "En revisión",
    "in_review_draft": "",
    "done": "Hecho"
  },
  "branch_pattern": "^(feature|fix|chore)/([A-Z]+-[0-9]+)-.*$"
}
```

| Campo | Descripción |
|---|---|
| `default_target_branch` | Rama destino por defecto para las MRs |
| `min_track_minutes` | Tiempo mínimo para imputar (sesiones más cortas se ignoran) |
| `idle_threshold_minutes` | Tiempo máximo sin actividad antes de descartar el tramo muerto (default: 60) |
| `stale_hours` | Horas de inactividad para considerar una MR obsoleta |
| `notify_channel` | Canal para notificaciones (ver sección siguiente) |
| `jira_statuses.in_progress` | Nombre exacto del estado "en progreso" en tu Jira |
| `jira_statuses.in_review` | Nombre del estado para MR lista (sin draft) |
| `jira_statuses.in_review_draft` | Nombre del estado para MR en draft (dejar vacío si no existe) |
| `jira_statuses.done` | Nombre del estado "hecho" en tu Jira |
| `branch_pattern` | Regex para extraer el número de tarea del nombre de rama |

Los nombres de estado deben coincidir **exactamente** con los configurados en tu Jira (el tracker hace búsqueda insensible a mayúsculas como fallback).

---

## Notificaciones de MRs obsoletas

Configura `notify_channel` en `config.json`. El tracker detecta el tipo de canal automáticamente.

**Microsoft Teams (Incoming Webhook):**
```json
"notify_channel": "https://miempresa.webhook.office.com/webhookb2/..."
```
Se envían como **MessageCards**: cada MR aparece como sección con título, reviewer, tiempo sin actividad y enlace directo a la MR.

Para crear el webhook en Teams:
1. Canal de Teams → `···` → **Conectores** → **Incoming Webhook** → Configurar
2. Dale un nombre (ej: `git-jira-tracker`) y copia la URL
3. Pégala en `notify_channel`

**Slack (Incoming Webhook):**
```json
"notify_channel": "https://hooks.slack.com/services/T.../B.../xxx"
```

**Email (SMTP):**
```json
"notify_channel": "smtp:smtp.gmail.com:587:tu@email.com:contraseña:destino@email.com"
```

Luego ejecuta:
```bash
tracker stale --notify
```

Para automatizarlo, añade a cron (Mac/Linux):
```bash
# Cada día a las 9:00
0 9 * * * tracker stale --notify
```

En Windows, crea una tarea en Task Scheduler que ejecute:
```
tracker stale --notify
```

---

## Cómo desactivarlo temporalmente

**Opción 1 — Variable de entorno (por sesión):**
```bash
# Los hooks comprueban esta variable y se saltan silenciosamente
export JIRA_TRACKER_DISABLED=1
git checkout feature/otra-rama   # no trackea
unset JIRA_TRACKER_DISABLED
```

**Opción 2 — Hacer los hooks no ejecutables:**
```bash
chmod -x .git/hooks/post-checkout .git/hooks/post-commit
# Para reactivar:
chmod +x .git/hooks/post-checkout .git/hooks/post-commit
```

**Opción 3 — Un commit puntual:**
```bash
git commit --no-verify   # salta todos los hooks
```

> Nota: los hooks nunca bloquean `git checkout` ni `git commit` aunque fallen. Si el tracker tiene un error, Git continúa normalmente.

---

## Troubleshooting

**El tracker no detecta mi rama**
Verifica que el nombre sigue el patrón `tipo/PROYECTO-123-descripcion`. Prueba el patrón en `config.json` o consulta con:
```bash
python3 -c "from config_loader import extract_jira_key; print(extract_jira_key('feature/QMS-123-test'))"
```

**Error: Missing environment variable**
El `.env` no se está cargando. Comprueba que existe en el directorio del tracker (`~/git-jira-tracker/.env`) y tiene las variables necesarias. Ejecuta desde el directorio del tracker.

**Jira responde 401**
El token de Jira ha expirado o es incorrecto. Genera uno nuevo en [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).

**GitLab responde 404 en el proyecto**
Verifica que `GITLAB_PROJECT_ID` es el ID numérico (no el nombre). Está en GitLab → Settings → General.

**Tiempos acumulados en pending.json**
```bash
python tracker.py pending --retry
# o
python tracker.py sync
```
Si siguen fallando, revisa las credenciales en `.env`.

**Los hooks no se ejecutan en Windows**
Asegúrate de que Git para Windows está instalado y que `sh.exe` está en `C:\Program Files\Git\bin\sh.exe`. Reinstala los hooks con `install.ps1`.

**Quiero ver el log completo**
```bash
cat ~/.jira-tracker/log.json       # log de API calls
cat ~/.jira-tracker/time_log.json  # log de sesiones de tiempo
cat ~/.jira-tracker/state.json     # estado actual
cat ~/.jira-tracker/pending.json   # pendientes
```

---

## Cómo contribuir

1. Haz fork del repositorio
2. Crea una rama: `git checkout -b feature/mi-mejora`
3. Haz tus cambios y añade tests si aplica
4. Abre una MR describiendo qué cambia y por qué

Para añadir un nuevo comando al CLI, añade la función `cmd_<nombre>` en `tracker.py` y regístrala en el dict `dispatch` y en `build_parser()`.

Para modificar la plantilla de MR, edita `mr_template.py`.
