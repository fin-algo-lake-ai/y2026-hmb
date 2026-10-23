"""Общие функции hmb_runner (v0, ручной режим)."""

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
JOURNAL = RESULTS_DIR / "journal.jsonl"
EXTENSION_ID = "anthropic.claude-code"
WS_TS_FORMAT = "%Y%m%dT%H%M%S"  # в имени workspace: без двоеточий, сортируется как время
WS_NAME_RE = re.compile(r"^(\d{8}T\d{6})-(.+)$")


def die(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


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
    task_dir, cfg = find_task(task_id)
    ws = workspace_dir(task_id)
    if ws.exists() and not force:
        die(f"workspace уже существует: {ws}\n"
            "  (два старта в одну секунду? для пересоздания — `start --force`)")
    _fresh_workspace(ws, task_dir)
    versions = snapshot_versions()
    meta = {"task_id": task_id, "started_utc": now_utc(),
            "versions": versions, "model": None}
    (ws / ".hmb-run.json").write_text(
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
        ("время от start", None if dur is None
         else f"{dur // 60} мин {dur % 60} с ({dur} с)"),
        ("модель", rec.get("model") or versions.get("model") or "не указана"),
        ("расширение", versions.get("extension")),
        ("vscode", versions.get("vscode")),
        ("cli", versions.get("cli")),
        ("python", versions.get("python")),
        ("workspace", rec.get("workspace")),
        ("заметки", rec.get("notes")),
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


def verify_task(task_id, workspace=None, notes=None, model=None, record=True):
    task_dir, cfg = find_task(task_id)
    if workspace:
        ws = Path(workspace).resolve()
        if not ws.exists():
            die(f"workspace не найден: {ws}")
    else:
        ws = latest_workspace(task_id)
        if ws is None:
            die(f"workspace задачи {task_id} не найден (workspaces/<таймстемп>-{task_id}); "
                f"сначала `python -m hmb_runner start {task_id}`")
    started = None
    versions = None
    meta_path = ws / ".hmb-run.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        started = meta.get("started_utc")
        versions = meta.get("versions")
    if model:
        versions = dict(versions or snapshot_versions())
        versions["model"] = model
    duration = None
    if started:
        duration = round((datetime.now(timezone.utc)
                          - datetime.fromisoformat(started)).total_seconds())

    score, passed, tail, suspect, checks, details = run_verifier(task_dir, cfg, ws)
    rec = {
        "ts_utc": now_utc(),
        "mode": "manual",
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
    print(("PASS " if passed else "FAIL ") + f"{task_id}  score={rec['score']}  [{rec['scoring']}]")
    if duration is not None:
        print(f"время от start: {duration // 60} мин {duration % 60} с")
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
        RESULTS_DIR.mkdir(exist_ok=True)
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print()
        print(f"записано: {JOURNAL}")
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
