"""CLI: python -m hmb_runner {list,run_manual,run_cli,verify,sanity,snapshot}."""

import argparse
import sys

from hmb_runner import core


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(prog="hmb",
                                 description="HMB runner v0 — прогон бенчмарка: "
                                             "вручную из VSCode (run_manual) "
                                             "или headless-CLI (run_cli)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="список задач")

    p = sub.add_parser("run_manual", aliases=["start"],
                       help="развернуть чистый workspace и напечатать "
                            "промпт для панели VSCode (бывш. `start`)")
    p.add_argument("task_id")
    p.add_argument("--force", action="store_true",
                   help="пересоздать workspace, даже если он уже есть")

    p = sub.add_parser("run_cli", help="автоматический прогон: headless-агент "
                                       "`claude -p` + verifier + журнал")
    p.add_argument("task_id")
    p.add_argument("--model", help="модель для агента (иначе модель по умолчанию CLI)")
    p.add_argument("--notes", help="заметка к прогону")
    p.add_argument("--timeout-min", type=int,
                   help="лимит агента в минутах (иначе time_limit_min задачи)")
    p.add_argument("--force", action="store_true",
                   help="пересоздать workspace, даже если он уже есть")
    p.add_argument("--no-record", action="store_true", help="не писать в журнал")

    p = sub.add_parser("verify", help="проверить workspace и записать результат в журнал")
    p.add_argument("task_id")
    p.add_argument("--workspace", help="каталог workspace (по умолчанию самый свежий задачи)")
    p.add_argument("--notes", help="заметка к прогону")
    p.add_argument("--model", help="модель в панели, если известна")
    p.add_argument("--no-record", action="store_true", help="только проверить, не писать в журнал")

    p = sub.add_parser("sanity", help="verifier на эталонном решении (score должен быть 1.0)")
    p.add_argument("task_id", nargs="?")
    p.add_argument("--all", action="store_true", help="все задачи")

    sub.add_parser("snapshot", help="показать снимок versions")

    a = ap.parse_args()

    core.check_journal_writable()  # журнал не должен быть открыт в Excel и т.п.

    if a.cmd == "list":
        core.list_tasks()
    elif a.cmd in ("run_manual", "start"):
        core.start_task(a.task_id, a.force)
    elif a.cmd == "run_cli":
        core.run_cli_task(a.task_id, model=a.model, notes=a.notes,
                          timeout_min=a.timeout_min, force=a.force,
                          record=not a.no_record)
    elif a.cmd == "verify":
        core.verify_task(a.task_id, a.workspace, a.notes, a.model,
                         record=not a.no_record)
    elif a.cmd == "sanity":
        if not a.all and not a.task_id:
            ap.error("укажи task_id или --all")
        ids = [c["id"] for c in core.all_tasks()] if a.all else [a.task_id]
        ok = all([core.sanity_task(i) for i in ids])
        sys.exit(0 if ok else 2)
    elif a.cmd == "snapshot":
        for k, v in core.snapshot_versions().items():
            print(f"{k:14} {v}")


if __name__ == "__main__":
    main()
