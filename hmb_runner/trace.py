"""Трасса решения из лога сессии CLI: trace.md + trace.html в <workspace>/_verifier_reports.

Генерируется на этапе verify (вне времени прогона бенчмарка). Источник —
.hmb-session.jsonl (stream-json артефакт run_cli): блоки tool_use из событий
assistant (что делал агент) и tool_result из событий user (что получилось).
Для ручных прогонов лога нет — генерация пропускается.
"""

import html
import json
import re
import sys
from pathlib import Path

TRACE_DIR = "_verifier_reports"   # внутри workspace
MD_MAX_LINES = 50                 # md: вывод длиннее обрезается до приложения
TOOL_COLORS = {"Bash": "#0e7490", "Edit": "#b45309", "Write": "#15803d",
               "Read": "#6d28d9"}
_PERSISTED_RE = re.compile(r"Full output saved to:\s*(.+?)\s*\n")


def _read_events(log_path):
    events = []
    for ln in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _tool_input(tool, inp):
    """Канонический текст действия: у Bash — команда, у остальных — их input."""
    if tool == "Bash":
        return inp.get("command", "")
    return json.dumps(inp, ensure_ascii=False, indent=1)


def _extract_zcode(events):
    """Разбор zcode stream-json (события с payload и dotted-типами).

    -> (meta, steps): та же структура, что у claude-ветки _extract.
    Текст/thinking копятся из model.streaming (text_delta/reasoning_delta);
    шаги — из model.streaming kind=tool_call (полный вызов с input) и
    tool.updated kind=result (вывод инструмента по toolCallId).
    """
    meta, steps = {}, []
    cur_notes, cur_thinking = [], []   # закрытые сегменты до текущего шага
    buf, seg = None, []                # открытый сегмент: "text" или "thinking"
    by_call = {}                       # toolCallId -> step
    completed = None                   # payload события turn.completed
    for e in events:
        t = e.get("type")
        p = e.get("payload") or {}
        if t == "turn.started":
            meta["session_id"] = e.get("sessionId")
            sel = ((p.get("intent") or {}).get("modelSelection") or {})
            if sel.get("modelId"):
                model = "%s/%s" % (sel.get("providerId", "?"), sel["modelId"])
                lvl = (sel.get("options") or {}).get("reasoningLevel")
                if lvl:
                    model += " (effort %s)" % lvl
                meta["model"] = model
        elif t == "model.streaming":
            k = p.get("kind")
            if k == "text_start":
                buf = "text"
            elif k == "reasoning_start":
                buf = "thinking"
            elif k == "text_delta" and buf == "text":
                seg.append(p.get("delta") or "")
            elif k == "reasoning_delta" and buf == "thinking":
                seg.append(p.get("delta") or "")
            elif k in ("text_end", "reasoning_end"):
                if buf == "text":
                    cur_notes.append("".join(seg))
                elif buf == "thinking":
                    cur_thinking.append("".join(seg))
                buf, seg = None, []
            elif k == "tool_call":
                step = {"idx": len(steps) + 1, "tool": p.get("toolName"),
                        "input": p.get("input") or {}, "output": None,
                        "is_error": False, "persisted": None,
                        "notes_before": cur_notes, "thinking_before": cur_thinking}
                steps.append(step)
                if p.get("toolCallId"):
                    by_call[p["toolCallId"]] = step
                cur_notes, cur_thinking = [], []
        elif t == "tool.updated" and p.get("kind") == "result":
            res = p.get("result") or {}
            out = res.get("content")
            if not isinstance(out, str):
                out = json.dumps(out, ensure_ascii=False, indent=1) if out is not None else ""
            st = by_call.get(p.get("toolCallId"))
            if st is not None:
                st["output"] = out
                st["is_error"] = not res.get("success", True)
            else:
                steps.append({"idx": len(steps) + 1, "tool": "?", "input": {},
                              "output": out, "is_error": not res.get("success", True),
                              "persisted": None, "notes_before": [], "thinking_before": []})
        elif t == "turn.completed":
            completed = p
        elif t == "result":
            # финальная строка дублирует turn.completed; берем response как fallback
            final = e
    if completed:
        u = completed.get("usage") or {}
        meta["result"] = {
            "session_id": meta.get("session_id"),
            "num_turns": completed.get("historyRoundCount"),
            "duration_ms": completed.get("duration"),
            "duration_api_ms": None,
            "total_cost_usd": None,
            "usage": {
                "input_tokens": u.get("inputTokens"),
                "output_tokens": u.get("outputTokens"),
                "cache_read_input_tokens": u.get("cacheReadTokens"),
            },
            "is_error": completed.get("resultType") not in (None, "success"),
            "terminal_reason": completed.get("resultType"),
        }
    if not meta.get("result"):
        meta["result"] = {}
    meta["result_text"] = (completed or {}).get("response") or ""
    if cur_notes:
        meta["final_notes"] = cur_notes
    return meta, steps


def _extract(log_path):
    """-> (meta, steps): meta — модель/сессия/метрики финального события,
    steps — [{idx, tool, input, output, is_error, notes_before, thinking_before}].
    Формат определяется по типам событий: dotted-типы (turn.started и т.п.) —
    zcode; иначе claude code stream-json. Вывод, усечённый CLI (persisted-output),
    добывается из файла с полным текстом (только claude).
    """
    events = _read_events(log_path)
    if any("." in str(e.get("type")) for e in events):
        return _extract_zcode(events)
    meta, steps, notes, thinking = {}, [], [], []
    for e in events:
        t = e.get("type")
        if t == "system" and e.get("subtype") == "init":
            meta["model"] = e.get("model")
            meta["session_id"] = e.get("session_id")
        elif t == "assistant":
            for b in e.get("message", {}).get("content", []):
                k = b.get("type")
                if k == "text":
                    notes.append(b.get("text", ""))
                elif k == "thinking":
                    thinking.append(b.get("thinking", ""))
                elif k == "tool_use":
                    steps.append({"idx": len(steps) + 1, "tool": b.get("name"),
                                  "input": b.get("input", {}), "output": None,
                                  "is_error": False, "persisted": None,
                                  "notes_before": notes, "thinking_before": thinking})
                    notes, thinking = [], []
        elif t == "user":
            content = e.get("message", {}).get("content")
            if isinstance(content, list):
                for b in content:
                    if b.get("type") != "tool_result":
                        continue
                    out = b.get("content")
                    if not isinstance(out, str):
                        out = json.dumps(out, ensure_ascii=False, indent=1)
                    persisted = None
                    m = _PERSISTED_RE.search(out or "")
                    if m:  # вывод усечён CLI; полный текст лежит в файле tool-results
                        persisted = Path(m.group(1).strip())
                        try:
                            out = persisted.read_text(encoding="utf-8", errors="replace")
                            persisted = None
                        except OSError:
                            pass
                    if steps and steps[-1]["output"] is None:
                        steps[-1].update(output=out, is_error=bool(b.get("is_error")),
                                         persisted=persisted)
                    else:
                        steps.append({"idx": len(steps) + 1, "tool": "?", "input": {},
                                      "output": out, "is_error": bool(b.get("is_error")),
                                      "persisted": None, "notes_before": [],
                                      "thinking_before": []})
        elif t == "result":
            meta["result"] = {k: e.get(k) for k in
                              ("session_id", "num_turns", "duration_ms", "duration_api_ms",
                               "total_cost_usd", "usage", "is_error", "terminal_reason")}
            meta["result_text"] = (e.get("result") or "").strip()
    if notes:
        meta["final_notes"] = notes
    return meta, steps


def _write(path, text):
    """write_text без newline= (python 3.9 не умеет) — журналы пишем с LF."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _fmt_duration(ms):
    return "%.1f с" % (ms / 1000.0) if ms is not None else None


def write_trace_reports(ws, rec=None):
    """Пишет trace.md и trace.html в <ws>/_verifier_reports. Возвращает путь
    каталога или None (нет лога сессии / ошибка — verify не должен упасть).
    rec — запись прогона из verify_task: дополняет шапку, если в логе чего-то нет.
    """
    from hmb_runner import core
    log = ws / core.CLI_SESSION_FILE
    if not log.exists():
        return None
    try:
        return _write_reports(log, ws / TRACE_DIR, rec or {})
    except Exception as e:  # трасса — вспомогательный артефакт, не повод падать
        print(f"внимание: не удалось построить трассу решения: {e}", file=sys.stderr)
        return None


def _write_reports(log, out_dir, rec):
    meta, steps = _extract(log)
    res = meta.get("result") or {}
    usage = res.get("usage") or {}
    versions = rec.get("versions") or {}
    run_id = Path(rec.get("workspace") or out_dir.parent).name

    out_dir.mkdir(parents=True, exist_ok=True)

    # длинные выводы в md уносятся в приложения (в html они целиком)
    att_dir = out_dir / "attachments"
    for s in steps:
        out = s.get("output") or ""
        s["n_lines"] = out.count("\n") + 1
        s["attachment"] = None
        if s["n_lines"] > MD_MAX_LINES:
            att_dir.mkdir(exist_ok=True)
            fn = "step-%02d-output.txt" % s["idx"]
            _write(att_dir / fn, out)
            s["attachment"] = "attachments/" + fn

    def rows():
        yield "Модель", meta.get("model") or rec.get("model")
        yield "Режим", rec.get("mode")
        if rec.get("agent"):
            yield "Агент", rec.get("agent")
        if rec.get("host"):
            yield "Хост", rec.get("host")
        if versions:
            yield "Версии", "cli %s, vscode %s, python %s" % (
                versions.get("cli"), versions.get("vscode"), versions.get("python"))
        if versions.get("zcode_cli"):
            yield "ZCode", "%s%s" % (versions.get("zcode_cli"),
                                     ", модель %s" % versions["zcode_model"]
                                     if versions.get("zcode_model") else "")
        yield "Session", res.get("session_id") or meta.get("session_id")
        yield "Шаги / ходы", "%d / %s" % (len(steps), res.get("num_turns"))
        dur = _fmt_duration(res.get("duration_ms"))
        if dur:
            yield "Время", dur
        cost = res.get("total_cost_usd")
        if cost is not None:
            yield "Стоимость", "$%.4f" % cost
        if usage:
            yield "Токены", "in %s / out %s / cache-read %s" % (
                usage.get("input_tokens"), usage.get("output_tokens"),
                usage.get("cache_read_input_tokens"))

    stats = list(rows())

    # ---------------- trace.md ----------------
    md = ["# Трасса решения: %s / %s" % (rec.get("task_id", "?"), run_id), "",
          "| | |", "|---|---|"]
    md += ["| %s | %s |" % (k, v) for k, v in stats]
    md.append("")
    for s in steps:
        md += ["## Шаг %d — %s" % (s["idx"], s["tool"]), ""]
        for n in s["notes_before"]:
            md += ["> " + n.replace("\n", "\n> "), ""]
        md += ["```", _tool_input(s["tool"], s["input"]), "```", ""]
        md += ["**Вывод** (%d строк%s):" % (s["n_lines"],
                ", ошибка" if s["is_error"] else ""), "", "```text"]
        lines = (s.get("output") or "").splitlines()
        if len(lines) > MD_MAX_LINES:
            md += lines[:MD_MAX_LINES]
            md += ["... (%d строк полностью: %s)" % (len(lines), s["attachment"])]
        else:
            md += lines
        md += ["```", ""]
    if meta.get("final_notes"):
        md += ["## Итог агента", "", "\n\n".join(meta["final_notes"]), ""]
    _write(out_dir / "trace.md", "\n".join(md))

    # ---------------- trace.html ----------------
    def esc(s):
        return html.escape(str(s))

    parts = ['''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Трасса: %s</title><style>
:root{--bg:#f8fafc;--fg:#0f172a;--mut:#64748b;--card:#fff;--bd:#e2e8f0;--codebg:#f1f5f9;--code:#0f172a}
@media(prefers-color-scheme:dark){:root{--bg:#0b1220;--fg:#e2e8f0;--mut:#94a3b8;--card:#111a2c;--bd:#1e293b;--codebg:#0f172a;--code:#e2e8f0}}
body{font-family:"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;line-height:1.5}
main{max-width:900px;margin:0 auto;padding:24px 16px}
h1{font-size:20px}
table{border-collapse:collapse;font-size:13px}
td{border:1px solid var(--bd);padding:4px 10px}
td:first-child{color:var(--mut)}
details{background:var(--card);border:1px solid var(--bd);border-radius:8px;margin:8px 0}
summary{cursor:pointer;padding:10px 14px;font-weight:600;list-style:none;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
summary::before{content:"\\25B8";color:var(--mut)} details[open] summary::before{content:"\\25BE"}
.body{padding:0 14px 14px}
.note{color:var(--mut);font-size:14px;border-left:3px solid var(--bd);padding:4px 10px;margin:6px 0;white-space:pre-wrap}
pre{background:var(--codebg);color:var(--code);padding:10px;border-radius:6px;overflow:auto;font-size:12.5px;white-space:pre-wrap;word-break:break-word;max-height:420px}
.err pre{border:1px solid #ef4444}
.badge{color:#fff;border-radius:4px;padding:2px 8px;font-size:12px;font-weight:600}
.badge-err{background:#dc2626}
.meta{color:var(--mut);font-size:12px}
</style></head><body><main>''' % esc("%s / %s" % (rec.get("task_id", "?"), run_id))]

    parts.append("<h1>Трасса решения: %s / %s</h1><table>%s</table>" % (
        esc(rec.get("task_id", "?")), esc(run_id),
        "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(v)) for k, v in stats)))

    for s in steps:
        color = TOOL_COLORS.get(s["tool"], "#475569")
        body = []
        for n in s["notes_before"]:
            body.append('<div class="note">%s</div>' % esc(n))
        for th in s["thinking_before"]:
            body.append('<details style="margin:4px 0"><summary style="font-weight:400;'
                        'color:var(--mut)">thinking</summary><div class="note">%s</div></details>'
                        % esc(th))
        body.append("<pre>%s</pre>" % esc(_tool_input(s["tool"], s["input"])))
        body.append('<p class="meta">вывод: %d строк%s</p>' % (
            s["n_lines"], " · ОШИБКА" if s["is_error"] else ""))
        body.append("<pre>%s</pre>" % esc(s.get("output") or ""))
        badge = '<span class="badge badge-err">ошибка</span>' if s["is_error"] else ""
        parts.append('<details class="%s"><summary><span class="badge" style="background:%s">%s'
                     '</span> Шаг %d %s<span class="meta">%s</span></summary><div class="body">%s'
                     '</div></details>' % ("err" if s["is_error"] else "", color, esc(s["tool"]),
                                           s["idx"], badge, esc(_tool_input(s["tool"], s["input"])[:60]),
                                           "".join(body)))
    if meta.get("final_notes"):
        parts.append('<details open><summary>Итог агента</summary><div class="body">%s</div></details>'
                     % "".join('<div class="note">%s</div>' % esc(n)
                               for n in meta["final_notes"]))
    parts.append("</main></body></html>")
    _write(out_dir / "trace.html", "\n".join(parts))
    return out_dir
