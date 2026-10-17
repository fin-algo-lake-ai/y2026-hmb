"""Пересоздаёт env/data/access.log детерминированно (фиксированный сид).

Запуск из каталога задачи:  python tools/gen_access_log.py
"""
import random
from pathlib import Path

SEED = 20261010
N = 2000
IPS = [f"203.0.113.{i}" for i in range(1, 41)] + ["198.51.100.9", "192.0.2.77"]
PATHS = ["/", "/index.html", "/api/v1/items", "/api/v1/orders",
         "/login", "/static/app.js", "/static/app.css", "/health"]
STATUSES = [(200, 70), (201, 5), (301, 5), (404, 10), (500, 6), (502, 2), (503, 2)]


def main():
    rng = random.Random(SEED)
    statuses, weights = zip(*STATUSES)
    lines = []
    for _ in range(N):
        ip = rng.choice(IPS)
        st = rng.choices(statuses, weights)[0]
        path = rng.choice(PATHS)
        b = rng.randint(64, 8192)
        sec = 8 * 3600 + rng.randint(0, 7000)
        hh, rem = divmod(sec, 3600)
        mm, ss = divmod(rem, 60)
        lines.append(f'{ip} - - [10/Oct/2026:{hh:02d}:{mm:02d}:{ss:02d} +0300] '
                     f'"GET {path} HTTP/1.1" {st} {b}')
    out = Path(__file__).resolve().parents[1] / "env" / "data" / "access.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out} ({N} lines)")


if __name__ == "__main__":
    main()
