"""Preparation resources and the concrete work they ask for.

Definitions live in sources/resources.toml. Completion lives in SQLite, because
SQLite is the source of truth for state and the TOML has to stay hand-editable.

This is a separate surface from the watcher on purpose. The PRD calls this system
a deadline-miss-prevention tool rather than a news summariser, so preparation work
gets one quiet line in the weekly roundup and never competes with a live posting
for attention.
"""

import tomllib
from dataclasses import dataclass, field

from . import config, db


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    resource_key: str
    resource_title: str
    detail: str = ""
    weight: int = 3
    effort: str = ""
    url: str = ""


@dataclass(frozen=True)
class Resource:
    key: str
    title: str
    url: str
    author: str = ""
    summary: str = ""
    relevance: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)


def load_resources(path=None) -> list[Resource]:
    path = path or config.RESOURCES_PATH
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    resources = []
    seen_tasks: set[str] = set()

    for entry in data.get("resource", []):
        tasks = []
        for t in entry.get("task", []):
            if t["key"] in seen_tasks:
                raise ValueError(f"duplicate task key across resources: {t['key']}")
            seen_tasks.add(t["key"])
            tasks.append(
                Task(
                    key=t["key"],
                    label=t["label"],
                    resource_key=entry["key"],
                    resource_title=entry["title"],
                    detail=t.get("detail", ""),
                    weight=int(t.get("weight", 3)),
                    effort=t.get("effort", ""),
                    url=t.get("url", ""),
                )
            )
        resources.append(
            Resource(
                key=entry["key"],
                title=entry["title"],
                url=entry.get("url", ""),
                author=entry.get("author", ""),
                summary=(entry.get("summary") or "").strip(),
                relevance=entry.get("relevance", []),
                tasks=tasks,
            )
        )
    return resources


def all_tasks(resources=None) -> list[Task]:
    return [t for r in (resources or load_resources()) for t in r.tasks]


def completion(conn) -> dict[str, str]:
    """Map of task_key to the timestamp it was completed."""
    rows = conn.execute("SELECT task_key, done_at FROM prep_tasks WHERE done_at IS NOT NULL")
    return {r["task_key"]: r["done_at"] for r in rows}


def mark_done(conn, task_key: str, notes: str = "") -> bool:
    """Returns False if the key is not a real task, so typos do not vanish silently."""
    tasks = {t.key: t for t in all_tasks()}
    if task_key not in tasks:
        return False
    conn.execute(
        """INSERT INTO prep_tasks (task_key, resource_key, done_at, notes)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(task_key) DO UPDATE SET done_at=excluded.done_at,
                                               notes=excluded.notes""",
        (task_key, tasks[task_key].resource_key, db.now(), notes),
    )
    conn.commit()
    return True


def mark_undone(conn, task_key: str) -> None:
    conn.execute("UPDATE prep_tasks SET done_at = NULL WHERE task_key = ?", (task_key,))
    conn.commit()


def open_tasks(conn, resources=None) -> list[Task]:
    """Unfinished tasks, heaviest first, then cheapest, so the next move is obvious."""
    done = completion(conn)
    pending = [t for t in all_tasks(resources) if t.key not in done]
    effort_order = {"hours": 0, "days": 1, "weeks": 2}
    return sorted(pending, key=lambda t: (-t.weight, effort_order.get(t.effort, 9)))


def digest_line(conn) -> str:
    """One line for the weekly roundup. Deliberately small."""
    tasks = all_tasks()
    done = completion(conn)
    pending = [t for t in tasks if t.key not in done]
    if not pending:
        return f"Preparation: all {len(tasks)} tasks complete."
    nxt = open_tasks(conn)[0]
    return (
        f"Preparation: {len(done)}/{len(tasks)} tasks done. "
        f"Highest leverage open item is \"{nxt.label}\" ({nxt.effort}), "
        f"from {nxt.resource_title}."
    )
