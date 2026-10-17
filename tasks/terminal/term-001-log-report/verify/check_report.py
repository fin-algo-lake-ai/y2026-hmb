"""Verifier для term-001-log-report.

Считает эталонный ответ из неизменённого исходного лога (env/data/access.log,
лежит рядом с verifier'ом) и сверяет с report.txt в workspace.
Печатает по строке на проверку и в конце "SCORE: <доля пройденных>".
"""
import hashlib
import os
from collections import Counter
from pathlib import Path

WS = Path(os.environ["HMB_WORKSPACE"])
LOG = WS / "data" / "access.log"
REPORT = WS / "report.txt"
PRISTINE = Path(__file__).resolve().parents[1] / "env" / "data" / "access.log"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_top5():
    cnt = Counter()
    for ln in PRISTINE.read_text(encoding="utf-8").splitlines():
        parts = ln.split()
        if len(parts) < 9:
            continue
        if 500 <= int(parts[-2]) <= 599:
            cnt[parts[0]] += 1
    return sorted(cnt.items(), key=lambda t: (-t[1], t[0]))[:5]


def main():
    checks = []

    def add(name, ok, hint=""):
        checks.append((name, bool(ok), hint))

    add("input_unmodified",
        LOG.exists() and sha(LOG) == sha(PRISTINE),
        "data/access.log отсутствует или изменён")

    parsed = None
    if REPORT.exists():
        lines = [ln.strip() for ln in REPORT.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        add("five_lines", len(lines) == 5, f"непустых строк: {len(lines)}, нужно 5")
        fmt_ok = all(len(ln.split()) == 2 and ln.split()[1].isdigit() for ln in lines)
        add("line_format", fmt_ok, "каждая строка должна быть '<ip> <count>'")
        if fmt_ok:
            parsed = [(ln.split()[0], int(ln.split()[1])) for ln in lines]
    else:
        add("five_lines", False, "report.txt не найден")
        add("line_format", False, "report.txt не найден")

    exp = expected_top5()
    if parsed is not None:
        add("ips_correct", {ip for ip, _ in parsed} == {ip for ip, _ in exp},
            f"expected ips: {[ip for ip, _ in exp]}")
        add("counts_correct", dict(parsed) == dict(exp),
            f"expected: {dict(exp)}")
        add("order_correct", parsed == exp,
            f"expected order: {exp}")
    else:
        for name in ("ips_correct", "counts_correct", "order_correct"):
            add(name, False, "нет разобранного report.txt")

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, hint in checks:
        print(("ok   " if ok else "FAIL ") + name + (f" ({hint})" if hint and not ok else ""))
    print(f"SCORE: {passed / len(checks):.2f}")


if __name__ == "__main__":
    main()
