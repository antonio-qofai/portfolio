"""Track the preparation work your vetted resources ask for.

    python -m tools.prep                    # what is open, highest leverage first
    python -m tools.prep --all              # include finished tasks
    python -m tools.prep done scaling-book  # mark one finished
    python -m tools.prep undo scaling-book  # change your mind

Definitions live in sources/resources.toml. Progress lives in SQLite, so editing
the TOML never wipes what you have already done.
"""

import argparse

from agent import db, prep


def show(conn, show_all: bool) -> None:
    resources = prep.load_resources()
    done = prep.completion(conn)
    tasks = prep.all_tasks(resources)

    print(f"{len(done)}/{len(tasks)} tasks complete\n")

    for resource in resources:
        print(f"{resource.title}")
        if resource.author:
            print(f"  {resource.author}  {resource.url}")
        print()
        for task in sorted(resource.tasks, key=lambda t: -t.weight):
            finished = task.key in done
            if finished and not show_all:
                continue
            mark = "x" if finished else " "
            effort = f"[{task.effort}]" if task.effort else ""
            print(f"  [{mark}] {task.label}  {effort}")
            print(f"      key: {task.key}  weight: {task.weight}/5")
            if task.detail:
                print(f"      {task.detail}")
            if task.url:
                print(f"      {task.url}")
            print()

    pending = prep.open_tasks(conn, resources)
    if pending:
        print(f"Start here: {pending[0].label}")
        print(f"  python -m tools.prep done {pending[0].key}")
    else:
        print("Everything on the list is done.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["done", "undo"])
    parser.add_argument("task_key", nargs="?")
    parser.add_argument("--notes", default="", help="why, or what you actually built")
    parser.add_argument("--all", action="store_true", help="include finished tasks")
    args = parser.parse_args()

    conn = db.connect()

    if args.command == "done":
        if not args.task_key:
            print("Which task? Run `python -m tools.prep` to see the keys.")
            return 1
        if not prep.mark_done(conn, args.task_key, args.notes):
            print(f"No task with key '{args.task_key}'. Run `python -m tools.prep` for the list.")
            return 1
        print(f"Marked done: {args.task_key}")
        print(prep.digest_line(conn))
    elif args.command == "undo":
        prep.mark_undone(conn, args.task_key)
        print(f"Reopened: {args.task_key}")
    else:
        show(conn, args.all)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
