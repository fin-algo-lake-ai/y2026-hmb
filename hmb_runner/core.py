"""Общие функции hmb_runner (v0): ручной режим (run_manual) и headless CLI (run_cli)."""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results"
WORKSPACES_DIR = ROOT / "workspaces"
JOURNAL = RESULTS_DIR / "journal.xlsx"
EXTENSION_ID = "anthropic.claude-code"
RUN_META_FILE = ".hmb-run.json"     # метаданные прогона внутри workspace
CLI_SESSION_FILE = ".hmb-session.jsonl"  # stream-json лог сессии CLI (артефакт)
WS_TS_FORMAT = "%Y%m%dT%H%M%S"  # в имени workspace: без двоеточий, сортируется как время
WS_NAME_RE = re.compile(r"^(\d{8}T\d{6})-(.+)$")


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def check_journal_writable():
    """Ранняя проверка перед любой командой: файл журнала не заблокирован на запись
    (например, открыт в Excel — иначе save упадёт с PermissionError в конце прогона).
    """
    if not JOURNAL.exists():
        return
    try:
        with open(JOURNAL, "r+b"):
            pass
    except PermissionError:
        die(f"файл журнала заблокирован на запись (скорее всего, открыт в Excel):\n"
            f"  {JOURNAL}\n"
            f"  закрой его и повтори команду")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        die("нужен PyYAML: pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_task(task_id):
    """Возвращает (каталог задачи, конфиг task.yaml)."""
    for cfg_path in sorted(TASKS_DIR.glob("*/*/task.yaml")):
        cfg = load_yaml(cfg_path)
        if cfg.get("id") == task_id:
            return cfg_path.parent, cfg
    die(f"задача не найдена: {task_id} (см. `python -m hmb_runner list`)")


def all_tasks():
    return [load_yaml(p) for p in sorted(TASKS_DIR.glob("*/*/task.yaml"))]


def _run(cmdline, timeout=20):
    exe = shutil.which(cmdline[0])
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *cmdline[1:]], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except Exception:
        return None
    return (r.stdout or "").strip().splitlines()



def snapshot_versions():
    """Снимок версий: расширение / VSCode / CLI / python."""
    ext_ver = None
    for ln in _run(["code", "--list-extensions", "--show-versions"]) or []:
        if ln.lower().startswith(EXTENSION_ID.lower() + "@"):
            ext_ver = ln.split("@", 1)[1].strip()
    vlines = _run(["code", "--version"])
    claude = _run(["claude", "--version"])
    return {
        "captured_utc": now_utc(),
        "extension": ext_ver or "not found",
        "vscode": vlines[0] if vlines else "not found",
        "cli": claude[0] if claude else "not found",
        "python": sys.version.split()[0],
    }


def _fresh_workspace(ws, task_dir):
    if ws.exists():
        shutil.rmtree(ws)
    if (task_dir / "env").exists():
        shutil.copytree(task_dir / "env", ws)
    else:
        ws.mkdir(parents=True)


def workspace_dir(task_id):
    """Каталог нового прогона: workspaces/<таймстемп>-<task-id> (локальное время, до секунды)."""
    return WORKSPACES_DIR / f"{datetime.now().strftime(WS_TS_FORMAT)}-{task_id}"


def latest_workspace(task_id):
    """Самый свежий workspace задачи по таймстемпу в имени; None, если нет.

    Понимает и старое имя без таймстемпа (workspaces/<task-id>), считая его самым старым.
    """
    if not WORKSPACES_DIR.exists():
        return None
    candidates = []
    for p in WORKSPACES_DIR.iterdir():
        if not p.is_dir():
            continue
        m = WS_NAME_RE.match(p.name)
        if m and m.group(2) == task_id:
            candidates.append((m.group(1), p))
        elif p.name == task_id:  # старое имя без таймстемпа
            candidates.append(("", p))
    return max(candidates)[1] if candidates else None


def start_task(task_id, force=False):
    """Ручной прогон (run_manual): workspace + промпт для панели VSCode."""
    task_dir, cfg = find_task(task_id)
    ws = workspace_dir(task_id)
    if ws.exists() and not force:
        die(f"workspace уже существует: {ws}\n"
            "  (два старта в одну секунду? для пересоздания — `run_manual --force`)")
    _fresh_workspace(ws, task_dir)
    versions = snapshot_versions()
    meta = {"task_id": task_id, "started_utc": now_utc(),
            "versions": versions, "model": None, "mode": "manual"}
    (ws / RUN_META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (cfg.get("prompt") or "").rstrip() + "\n"
    (ws / "_prompt.txt").write_text(prompt, encoding="utf-8")

    print(f"workspace : {ws}")
    print(f"задача    : {task_id} [{cfg.get('category')} / {cfg.get('difficulty')}, "
          f"лимит {cfg.get('time_limit_min')} мин]")
    print(f"версии    : ext {versions['extension']} | vscode {versions['vscode']} | "
          f"cli {versions['cli']}")
    print(f"открыть   : code \"{ws}\"")
    print()
    print("промпт для панели Claude Code (также сохранён в _prompt.txt):")
    print("-" * 70)
    sys.stdout.write(prompt)
    print("-" * 70)
    print()
    print(f"проверка после завершения: python -m hmb_runner verify {task_id}")


def _parse_session(path):
    """Читает stream-json лог сессии CLI; возвращает metrics из финального
    события type="result" (последнего в логе; {} если его нет).

    terminal_reason берётся как есть (причина завершения, если CLI её отдал).
    """
    metrics = {}
    if not path.exists():
        return metrics
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if not (isinstance(obj, dict) and obj.get("type") == "result"):
            continue
        dur = obj.get("duration_ms")
        api = obj.get("duration_api_ms")
        metrics = {
            "session_id": obj.get("session_id"),
            "subtype": obj.get("subtype"),
            "is_error": obj.get("is_error"),
            "num_turns": obj.get("num_turns"),
            "duration_sec": round(dur / 1000, 1) if dur is not None else None,
            "api_sec": round(api / 1000, 1) if api is not None else None,
            "cost_usd": obj.get("total_cost_usd"),
            "usage": obj.get("usage"),
            "terminal_reason": obj.get("terminal_reason"),
            "result_excerpt": (obj.get("result") or "").strip()[:300] or None,
        }
    return metrics


def run_cli_agent(cfg, ws, model=None, timeout_min=None):
    """Headless-прогон агента: `claude -p` внутри workspace (вариант run_cli).

    Полный stream-json лог пишется в <ws>/.hmb-session.jsonl (артефакт прогона).
    Разрешения не запрашиваются (--dangerously-skip-permissions) — вызывающий
    код обязан до этого показать WARNING (см. run_cli_task).
    Возвращает (metrics, timed_out).
    """
    exe = shutil.which("claude")
    if not exe:
        die("CLI не найден: claude (проверь `python -m hmb_runner snapshot`)")
    cmdline = [exe, "-p",
               "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions"]
    if model:
        cmdline += ["--model", model]
    limit_min = timeout_min or cfg.get("time_limit_min") or 30
    env = os.environ.copy()
    env["DISABLE_AUTOUPDATER"] = "1"  # фиксация версии CLI на время прогона
    timed_out = False
    stderr_b = b""
    with open(ws / "_prompt.txt", "r", encoding="utf-8") as fin, \
         open(ws / CLI_SESSION_FILE, "w", encoding="utf-8") as flog:
        print(f"агент работает (лимит {limit_min} мин), лог: {ws / CLI_SESSION_FILE}")
        sys.stdout.flush()
        try:
            r = subprocess.run(cmdline, cwd=str(ws), stdin=fin, stdout=flog,
                               stderr=subprocess.PIPE, timeout=limit_min * 60)
        except subprocess.TimeoutExpired as e:
            r, timed_out, stderr_b = e, True, e.stderr or b""
        else:
            stderr_b = r.stderr or b""
    if timed_out:
        print(f"ТАЙМАУТ: агент не уложился в {limit_min} мин — процесс остановлен, "
              f"лог сессии сохранён, прогон помечен timed_out")
    err = stderr_b.decode("utf-8", errors="replace").strip()
    if err:
        print("stderr CLI (хвост):")
        print("\n".join(err.splitlines()[-10:]))
    metrics = _parse_session(ws / CLI_SESSION_FILE)
    if timed_out:
        metrics["timed_out"] = True
    return metrics, timed_out


def run_cli_task(task_id, model=None, notes=None, timeout_min=None,
                 force=False, record=True):
    """Автоматический прогон: workspace + headless-агент + verifier + журнал."""
    task_dir, cfg = find_task(task_id)
    ws = workspace_dir(task_id)
    if ws.exists() and not force:
        die(f"workspace уже существует: {ws}\n"
            "  (для пересоздания — `run_cli --force`)")
    _fresh_workspace(ws, task_dir)
    versions = snapshot_versions()
    meta = {"task_id": task_id, "started_utc": now_utc(),
            "versions": versions, "model": model, "mode": "cli"}
    (ws / RUN_META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (cfg.get("prompt") or "").rstrip() + "\n"
    (ws / "_prompt.txt").write_text(prompt, encoding="utf-8")

    print(f"workspace : {ws}")
    print(f"задача    : {task_id} [{cfg.get('category')} / {cfg.get('difficulty')}, "
          f"лимит {cfg.get('time_limit_min')} мин]")
    print(f"версии    : cli {versions['cli']} | python {versions['python']}"
          + (f" | модель {model}" if model else ""))
    print("=" * 70)
    print("WARNING: агент запускается с --dangerously-skip-permissions.")
    print("  Подтверждения не запрашиваются: shell-команды, запись и удаление")
    print("  файлов выполняются свободно, ограничение — только таймаут задачи.")
    print("  Рекомендуется изоляция (контейнер/VM); workspace:", ws)
    print("=" * 70)
    print()

    metrics, timed_out = run_cli_agent(cfg, ws, model=model,
                                       timeout_min=timeout_min)
    print()
    if metrics.get("num_turns") is not None:
        print(f"агент: {metrics['num_turns']} шагов, "
              f"{metrics.get('duration_sec')} с, "
              f"terminal_reason={metrics.get('terminal_reason')}")
    return verify_task(task_id, workspace=str(ws), notes=notes, model=model,
                       record=record, metrics=metrics)


CHECK_RE = re.compile(r"^(ok|FAIL)\s+(\S+)(?:\s+\((.*)\))?\s*$", re.M)


def _read_details(ws):
    """Читает детальные кейсы, дописанные verifier'ом в $HMB_DETAILS (jsonl).

    Возвращает список dict'ов (по одному на строку); мусорные строки игнорируются.
    """
    path = ws / ".hmb-details.jsonl"
    details = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if isinstance(obj, dict):
                details.append(obj)
        path.unlink()
    return details


def run_verifier(task_dir, cfg, ws, timeout_sec=180):
    """Запускает verifier задачи.

    Возвращает (score, passed, хвост вывода, verifier_suspect, checks, details),
    checks — список (ok, имя_проверки, пояснение) из строк "ok/FAIL <имя> (<пояснение>)",
    details — детальные кейсы из $HMB_DETAILS (см. _read_details).
    Контракт verifier'а: печатает строку "SCORE: <доля 0..1>", код выхода 0,
    если verifier сам отработал (даже при score=0); ненулевой код — verifier сломан.
    """
    vcfg = cfg.get("verify") or {}
    cmd = str(vcfg.get("cmd") or "")
    if not cmd:
        die(f"в {task_dir / 'task.yaml'} не задан verify.cmd")
    if cmd.startswith("python"):
        cmd = f'"{sys.executable}"' + cmd[len("python"):]
    details_path = ws / ".hmb-details.jsonl"
    if details_path.exists():  # хвост прошлого прогона (например, после таймаута)
        details_path.unlink()
    env = os.environ.copy()
    env["HMB_WORKSPACE"] = str(ws)
    env["HMB_DETAILS"] = str(ws / ".hmb-details.jsonl")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(task_dir), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout_sec,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 0.0, False, "verifier: таймаут", True, [], []
    out = r.stdout or ""
    if (r.stderr or "").strip():
        out += "\n[stderr]\n" + r.stderr.strip()
    tail = "\n".join(out.strip().splitlines()[-40:])
    checks = [(m.group(1) == "ok", m.group(2), m.group(3) or "")
              for m in CHECK_RE.finditer(out)]
    details = _read_details(ws)
    score = None
    for m in re.finditer(r"^SCORE:\s*([0-9]*\.?[0-9]+)\s*$", out, re.M):
        score = float(m.group(1))
    if r.returncode != 0:
        return 0.0, False, tail + "\n(verifier завершился с ошибкой — проверь verify.cmd)", True, checks, details
    if score is None:
        return 0.0, False, tail + "\n(verifier не напечатал SCORE:)", True, checks, details
    score = max(0.0, min(1.0, score))
    return score, score >= 1.0, tail, False, checks, details


def write_excel_report(rec, checks, details):
    """Отчёт прогона: results/report-<таймстемп>-<task-id>.xlsx.

    Лист «Статистика» — общие данные запуска, лист «Ошибки» — проваленные
    проверки (или причина сбоя verifier'а), лист «Кейсы» — детальные кейсы
    из $HMB_DETAILS (исходные данные, ответ системы, эталон).
    Возвращает путь или None (нет openpyxl).
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        print("внимание: openpyxl не установлен — excel-отчёт не сформирован "
              "(pip install openpyxl)")
        return None
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / (f"report-{datetime.now().strftime(WS_TS_FORMAT)}"
                          f"-{rec['task_id']}.xlsx")

    versions = rec.get("versions") or {}
    dur = rec.get("duration_sec")
    wb = Workbook()
    ws = wb.active
    ws.title = "Статистика"
    rows = [
        ("дата/время (utc)", rec.get("ts_utc")),
        ("задача", rec.get("task_id")),
        ("категория", rec.get("category")),
        ("scoring", rec.get("scoring")),
        ("score", rec.get("score")),
        ("итог", "PASS" if rec.get("passed") else "FAIL"),
        ("проверок", f"{len(checks)} всего, "
                     f"{sum(1 for ok, _, _ in checks if ok)} ok, "
                     f"{sum(1 for ok, _, _ in checks if not ok)} FAIL"),
        ("verifier корректен", "нет" if rec.get("verifier_suspect") else "да"),
        ("время" + ("" if rec.get("mode") == "cli" else " от start"),
         None if dur is None else f"{dur // 60} мин {dur % 60} с ({dur} с)"),
        ("модель", rec.get("model") or versions.get("model") or "не указана"),
        ("расширение", versions.get("extension")),
        ("vscode", versions.get("vscode")),
        ("cli", versions.get("cli")),
        ("python", versions.get("python")),
        ("workspace", rec.get("workspace")),
        ("заметки", rec.get("notes")),
    ]
    m = rec.get("metrics")
    if m:
        usage = m.get("usage") or {}
        rows += [
            ("шагов агента", m.get("num_turns")),
            ("время агента / api (с)", None if m.get("duration_sec") is None
             else f"{m.get('duration_sec')} / {m.get('api_sec')}"),
            ("токены in/out (кэш read/creation)",
             f"{usage.get('input_tokens', 0)} / {usage.get('output_tokens', 0)} "
             f"({usage.get('cache_read_input_tokens', 0)} / "
             f"{usage.get('cache_creation_input_tokens', 0)})"),
            ("стоимость (usd)", m.get("cost_usd")),
            ("terminal_reason", m.get("terminal_reason")),
            ("timed_out", bool(m.get("timed_out"))),
            ("session_id", m.get("session_id")),
        ]
    ws.append(("параметр", "значение"))
    for row in rows:
        ws.append(row)
    ws["A1"].font = Font(bold=True)
    ws["B1"].font = Font(bold=True)
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 100

    ws2 = wb.create_sheet("Ошибки")
    ws2.append(("проверка", "описание"))
    errors = [(name, hint) for ok, name, hint in checks if not ok]
    if rec.get("verifier_suspect"):
        errors.append(("verifier", rec.get("verifier_tail")
                       or "verifier не смог отработать корректно"))
    if errors:
        for name, hint in errors:
            ws2.append((name, hint))
    else:
        ws2.append(("нет", ""))
    ws2["A1"].font = Font(bold=True)
    ws2["B1"].font = Font(bold=True)
    ws2.column_dimensions["A"].width = 24
    ws2.column_dimensions["B"].width = 110

    if details:
        ws3 = wb.create_sheet("Кейсы")
        keys = []
        for d in details:
            for k in d:
                if k not in keys:
                    keys.append(k)
        ws3.append(keys)
        for d in details:
            ws3.append([d.get(k) for k in keys])
        ws3["A1"].font = Font(bold=True)
        for i, k in enumerate(keys):
            longest = max([len(str(d.get(k, ""))) for d in details] + [len(str(k))])
            ws3.column_dimensions[ws3.cell(row=1, column=i + 1).column_letter] \
               .width = min(60, max(12, longest + 2))

    wb.save(path)
    return path


# Колонки журнала, которые код пишет сам (раскладка по заголовкам в append_journal).
JOURNAL_COLS = ["ts_utc", "mode", "task_id", "category", "scoring", "score",
                "passed", "duration_sec", "model", "extension", "vscode", "cli",
                "python", "notes", "workspace", "verifier_suspect",
                "num_turns", "agent_sec", "api_sec", "cost_usd",
                "terminal_reason", "session_id"]

# Ручные колонки журнала — заполняет оператор, код в них не пишет; список нужен
# только чтобы создать их в шапке нового журнала (_new_journal_book).
JOURNAL_MANUAL_COLS = ["manual\nseconds", "manual\nz\ncredits", "manual\nz\nusd", "manual\nclaude\napi\nusd"]

JOURNAL_ALL_COLS = JOURNAL_COLS + JOURNAL_MANUAL_COLS


def _new_journal_book():
    """Новый журнал: лист «Журнал», шапка, ширины колонок, закреплённая шапка."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Журнал"
    ws.append(JOURNAL_ALL_COLS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    widths = {"A": 20, "B": 7, "C": 22, "D": 9, "E": 8, "F": 7, "G": 8,
              "H": 12, "I": 15, "J": 12, "K": 12, "L": 18, "M": 10, "N": 9,
              "O": 20, "P": 8, "Q": 30, "R": 60, "S": 9, "T": 9, "U": 10,
              "V": 8, "W": 10, "X": 16, "Y": 38}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return wb


def append_journal(rec):
    """Дописывает прогон строкой в общий excel-журнал results/journal.xlsx.

    Лист «Журнал» — одна строка на прогон, шапка создаётся при первом запуске.
    Значения раскладываются по заголовкам файла, так что колонки можно вручную
    переставлять и дополнять своими (новые строки придут с пустыми значениями).
    Версии из rec.versions и метрики CLI-прогона из rec.metrics разворачиваются
    в отдельные колонки. Если журнал заблокирован на запись (например, открыт
    в Excel) — при старте это ловит check_journal_writable(), а здесь, на случай
    блокировки прямо во время прогона, запись уходит во временный
    journal-DRAFT-<таймстемп>.xlsx. Возвращает путь или None (нет openpyxl).
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("внимание: openpyxl не установлен — запись в журнал пропущена "
              "(pip install openpyxl)")
        return None
    RESULTS_DIR.mkdir(exist_ok=True)
    versions = rec.get("versions") or {}
    m = rec.get("metrics") or {}
    vals = {
        "ts_utc": rec.get("ts_utc"),
        "mode": rec.get("mode"),
        "task_id": rec.get("task_id"),
        "category": rec.get("category"),
        "scoring": rec.get("scoring"),
        "score": rec.get("score"),
        "passed": rec.get("passed"),
        "duration_sec": rec.get("duration_sec"),
        "model": rec.get("model") or versions.get("model"),
        "extension": versions.get("extension"),
        "vscode": versions.get("vscode"),
        "cli": versions.get("cli"),
        "python": versions.get("python"),
        "notes": rec.get("notes"),
        "workspace": rec.get("workspace"),
        "verifier_suspect": bool(rec.get("verifier_suspect")),
        "num_turns": m.get("num_turns"),
        "agent_sec": m.get("duration_sec"),
        "api_sec": m.get("api_sec"),
        "cost_usd": m.get("cost_usd"),
        "terminal_reason": m.get("terminal_reason"),
        "session_id": m.get("session_id"),
    }
    history_lost = False
    if JOURNAL.exists():
        try:
            wb = load_workbook(JOURNAL)
        except PermissionError:
            # журнал уже заблокирован (чтение тоже запрещено) — draft будет
            # только с этой записью, прежние строки остаются в основном журнале
            history_lost = True
            wb = _new_journal_book()
    else:
        wb = _new_journal_book()
    ws = wb.active
    if history_lost:
        ws.append([vals.get(c) for c in JOURNAL_ALL_COLS])
    else:
        # значения раскладываются по заголовкам файла, а не позиционно:
        # колонки можно вручную переставлять и дополнять своими
        header = [c.value for c in ws[1]]
        if header != JOURNAL_ALL_COLS:
            missing = [c for c in JOURNAL_COLS if c not in header]
            extra = [c for c in header if c and c not in JOURNAL_COLS]
            if missing:
                print(f"внимание: в журнале нет колонок ({', '.join(missing)}) "
                      f"— эти поля в новой строке не сохранены")
            if extra:
                print(f"свои колонки ({', '.join(extra)}) — в новых строках останутся пустыми")
        ws.append([vals.get(c) for c in header])
    try:
        wb.save(JOURNAL)
        return JOURNAL
    except PermissionError:
        draft = RESULTS_DIR / (f"journal-DRAFT-{datetime.now().strftime(WS_TS_FORMAT)}.xlsx")
        wb.save(draft)
        tail = ("вся история + эта запись" if not history_lost
                else "только эта запись, прежние строки остались в journal.xlsx")
        print(f"внимание: журнал заблокирован на запись (открыт в Excel?) — результат "
              f"записан во временный:\n  {draft}\n"
              f"  ({tail})\n"
              f"  после закрытия Excel перенеси данные в {JOURNAL.name}, а draft удали")
        return draft


def verify_task(task_id, workspace=None, notes=None, model=None, record=True,
                metrics=None):
    task_dir, cfg = find_task(task_id)
    if workspace:
        ws = Path(workspace).resolve()
        if not ws.exists():
            die(f"workspace не найден: {ws}")
    else:
        ws = latest_workspace(task_id)
        if ws is None:
            die(f"workspace задачи {task_id} не найден (workspaces/<таймстемп>-{task_id}); "
                f"сначала `run_manual {task_id}` или `run_cli {task_id}`")
    started = None
    mode = "manual"
    versions = None
    meta_model = None
    meta_path = ws / RUN_META_FILE
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        started = meta.get("started_utc")
        versions = meta.get("versions")
        mode = meta.get("mode") or "manual"
        meta_model = meta.get("model")
    if model:
        versions = dict(versions or snapshot_versions())
        versions["model"] = model
    else:
        model = meta_model  # модель из метаданных прогона (run_cli --model)
    # у CLI-прогона точное время агента берём из лога сессии (артефакт workspace)
    metrics = dict(metrics or _parse_session(ws / CLI_SESSION_FILE)) or None
    duration = None
    if metrics and metrics.get("duration_sec") is not None:
        duration = metrics["duration_sec"]
    elif started:
        duration = round((datetime.now(timezone.utc)
                          - datetime.fromisoformat(started)).total_seconds())
    score, passed, tail, suspect, checks, details = run_verifier(task_dir, cfg, ws)
    rec = {
        "ts_utc": now_utc(),
        "mode": mode,
        "task_id": task_id,
        "category": cfg.get("category"),
        "scoring": (cfg.get("verify") or {}).get("scoring"),
        "score": round(score, 4),
        "passed": passed,
        "duration_sec": duration,
        "model": model or (versions or {}).get("model"),
        "versions": versions,
        "notes": notes,
        "workspace": str(ws),
        "verifier_suspect": suspect,
        "verifier_tail": tail,
    }
    if metrics:
        rec["metrics"] = metrics
    print(("PASS " if passed else "FAIL ") + f"{task_id}  score={rec['score']}  [{rec['scoring']}]"
          + (f"  [{mode}]" if mode != "manual" else ""))
    if duration is not None:
        print(f"время: {int(duration) // 60} мин {int(duration) % 60} с")
    if suspect:
        print("внимание: verifier не смог отработать корректно — записан score=0")
    print()
    print(tail)
    report = write_excel_report(rec, checks, details)
    if report:
        print()
        if details:
            print(f"детальные кейсы: {len(details)} (лист «Кейсы» в отчёте)")
        print(f"отчёт: {report}")
    if record:
        journal = append_journal(rec)
        if journal:
            print()
            print(f"записано: {journal}")
    return passed


def sanity_task(task_id):
    """Санити задачи: эталонное решение обязано набрать score = 1.0.

    workspaces/_sanity/<task-id> — служебная папка, без таймстемпа в имени.
    """
    task_dir, cfg = find_task(task_id)
    sol = task_dir / "solution"
    if not sol.exists():
        die(f"у задачи {task_id} нет solution/")
    ws = WORKSPACES_DIR / "_sanity" / task_id
    _fresh_workspace(ws, task_dir)
    for p in sol.rglob("*"):
        if p.is_file():
            dst = ws / p.relative_to(sol)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    score, passed, tail, suspect, _, _ = run_verifier(task_dir, cfg, ws)
    ok = passed and not suspect
    print(("SANITY PASS " if ok else "SANITY FAIL ") + f"{task_id}  score={round(score, 4)}")
    if not ok:
        print(tail)
        print(f"(workspace санити сохранён для разбора: {ws})")
    return ok


def list_tasks():
    print(f"{'id':30}{'category':10}{'difficulty':11}{'min':>4}  scoring")
    for c in all_tasks():
        v = c.get("verify") or {}
        print(f"{c.get('id', '?'):30}{str(c.get('category', '')):10}"
              f"{str(c.get('difficulty', '')):11}{str(c.get('time_limit_min', '')):>4}  "
              f"{v.get('scoring', '')}")
