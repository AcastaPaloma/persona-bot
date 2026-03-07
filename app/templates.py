"""Strict note templates per category.

Every note created by the bot must follow one of these templates.
References always go in the ## Related section at the end.
"""

from datetime import datetime
from typing import Optional


def _format_related(related_notes: list[str]) -> str:
    if not related_notes:
        return "## Related\n"
    lines = ["## Related"]
    for title in related_notes:
        lines.append(f"- [[{title}]]")
    return "\n".join(lines) + "\n"


def _format_aliases(aliases: list[str]) -> str:
    if not aliases:
        return "aliases: []"
    items = ", ".join(f'"{a}"' for a in aliases)
    return f"aliases: [{items}]"


def daily_summary(
    date_str: str,
    weekday: str,
    highlights: list[str],
    captures_summary: str,
    related_notes: list[str],
    created: Optional[datetime] = None,
) -> str:
    ts = (created or datetime.now().astimezone()).strftime("%Y-%m-%dT%H:%M:%S")
    hl = "\n".join(f"- {h}" for h in highlights) if highlights else "- (no captures today)"

    return f"""---
date: {date_str}
type: daily-summary
created: {ts}
tags:
  - daily
---

# Daily Summary — {weekday}, {date_str}

## Highlights
{hl}

## Captures
{captures_summary}

{_format_related(related_notes)}"""


def topic_note(
    title: str,
    summary: str,
    content: str,
    related_notes: list[str],
    aliases: Optional[list[str]] = None,
    created: Optional[datetime] = None,
) -> str:
    ts = (created or datetime.now().astimezone()).strftime("%Y-%m-%dT%H:%M:%S")

    return f"""---
title: {title}
type: topic
created: {ts}
{_format_aliases(aliases or [])}
---

# {title}

## Summary
{summary}

## Content
{content}

{_format_related(related_notes)}"""


def person_note(
    title: str,
    summary: str,
    notes_content: str,
    related_notes: list[str],
    aliases: Optional[list[str]] = None,
    created: Optional[datetime] = None,
) -> str:
    ts = (created or datetime.now().astimezone()).strftime("%Y-%m-%dT%H:%M:%S")

    return f"""---
title: {title}
type: person
created: {ts}
{_format_aliases(aliases or [])}
---

# {title}

## Summary
{summary}

## Notes
{notes_content}

{_format_related(related_notes)}"""


def project_note(
    title: str,
    summary: str,
    progress: str,
    related_notes: list[str],
    aliases: Optional[list[str]] = None,
    status: str = "active",
    created: Optional[datetime] = None,
) -> str:
    ts = (created or datetime.now().astimezone()).strftime("%Y-%m-%dT%H:%M:%S")

    return f"""---
title: {title}
type: project
created: {ts}
{_format_aliases(aliases or [])}
status: {status}
---

# {title}

## Summary
{summary}

## Progress
{progress}

{_format_related(related_notes)}"""


def technique_note(
    title: str,
    summary: str,
    details: str,
    related_notes: list[str],
    domain: str = "",
    aliases: Optional[list[str]] = None,
    created: Optional[datetime] = None,
) -> str:
    ts = (created or datetime.now().astimezone()).strftime("%Y-%m-%dT%H:%M:%S")

    return f"""---
title: {title}
type: technique
created: {ts}
{_format_aliases(aliases or [])}
domain: {domain}
---

# {title}

## Summary
{summary}

## Details
{details}

{_format_related(related_notes)}"""


def school_note(
    title: str,
    summary: str,
    content: str,
    related_notes: list[str],
    subject: str = "",
    aliases: Optional[list[str]] = None,
    created: Optional[datetime] = None,
) -> str:
    ts = (created or datetime.now().astimezone()).strftime("%Y-%m-%dT%H:%M:%S")

    return f"""---
title: {title}
type: school
created: {ts}
{_format_aliases(aliases or [])}
subject: {subject}
---

# {title}

## Summary
{summary}

## Content
{content}

{_format_related(related_notes)}"""


TEMPLATE_MAP = {
    "daily-summary": daily_summary,
    "topic": topic_note,
    "person": person_note,
    "project": project_note,
    "technique": technique_note,
    "school": school_note,
}
