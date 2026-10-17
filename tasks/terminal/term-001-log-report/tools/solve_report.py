"""Строит эталонный solution/report.txt из env/data/access.log.

Запуск из каталога задачи:  python tools/solve_report.py
"""
from collections import Counter
from pathlib import Path

TASK = Path(__file__).resolve().parents[1]
LOG = TASK / "env" / "data" / "access.log"

cnt = Counter()
for ln in LOG.read_text(encoding="utf-8").splitlines():
    parts = ln.split()
    if len(parts) < 9:
        continue
    if 500 <= int(parts[-2]) <= 599:
        cnt[parts[0]] += 1

top = sorted(cnt.items(), key=lambda t: (-t[1], t[0]))[:5]
out = TASK / "solution" / "report.txt"
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write("".join(f"{ip} {c}\n" for ip, c in top))
print(f"wrote {out}: {top}")
