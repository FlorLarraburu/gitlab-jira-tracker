"""
MR description template generator for git-jira-tracker.
"""

from typing import Optional


def format_duration(seconds: int) -> str:
    """Convert seconds to human-readable duration."""
    if seconds <= 0:
        return "No registrado"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"


def generate_description(
    jira_summary: str,
    jira_description: str,
    jira_url: str,
    time_spent_seconds: int,
    parent_mr_url: Optional[str] = None,
    parent_mr_title: Optional[str] = None,
) -> str:
    """
    Generate the MR description from the standard template.

    Args:
        jira_summary:        Issue title from Jira
        jira_description:    Issue description from Jira (plain text)
        jira_url:            Direct URL to the Jira issue
        time_spent_seconds:  Total seconds tracked on this branch
        parent_mr_url:       URL of the parent MR if this is a stacked branch
        parent_mr_title:     Title of the parent MR
    """
    # What does this MR do
    what = jira_description.strip() if jira_description else "_Sin descripción en Jira_"

    # Related task
    related_task = f"[{jira_url}]({jira_url})" if jira_url else "_No vinculada_"

    # Time spent
    time_str = format_duration(time_spent_seconds)

    # Parent MR section
    if parent_mr_url and parent_mr_title:
        parent_section = f"[{parent_mr_title}]({parent_mr_url})"
    elif parent_mr_url:
        parent_section = f"[Ver MR padre]({parent_mr_url})"
    else:
        parent_section = "_No aplica — rama sale de la rama principal_"

    description = f"""## ¿Qué hace esta MR?
{what}

## Tarea relacionada
{related_task}

## Tiempo invertido
{time_str}

## MR padre (si es rama encadenada)
{parent_section}

## Checklist
- [ ] Tests añadidos o actualizados
- [ ] Documentación actualizada en Confluence si aplica
- [ ] La MR está vinculada a su tarea de Jira
- [ ] El código ha sido revisado por el autor antes de pedir review
- [ ] No hay console.log ni código de debug

## Notas para el reviewer
<!-- Dejar vacío o añadir contexto adicional -->
"""
    return description.strip()


def generate_title(issue_key: str, jira_summary: str, draft: bool = True) -> str:
    """Generate the MR title following the convention."""
    base = f"[{issue_key}] {jira_summary}"
    return f"Draft: {base}" if draft else base
