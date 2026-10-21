"""Verifier для xls-002-sentiment1 (нужен openpyxl).

Эталон считается из исходного env/data/orders.xlsx: тональность каждой фразы
feedback берётся из фиксированного лексикона LEXICON (детерминированный
ground truth, не зависит от генератора). Сверяется колонка G "sentiment"
листа Orders и лист "Summary" в workspace. Печатает по строке на проверку
и в конце "SCORE: <доля пройденных>". Код выхода 0 — verifier отработал.
"""
import os
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

WS = Path(os.environ["HMB_WORKSPACE"])
WB = WS / "data" / "orders.xlsx"
PRISTINE = Path(__file__).resolve().parents[1] / "env" / "data" / "orders.xlsx"

CLASSES = ("positive", "negative", "neutral")
LEXICON = {
    # positive
    "all is good": "positive",
    "great service": "positive",
    "fast delivery, thanks": "positive",
    "product works perfectly": "positive",
    "very satisfied": "positive",
    "excellent quality": "positive",
    "will order again": "positive",
    # negative
    "all is bad": "negative",
    "terrible support": "negative",
    "item arrived broken": "negative",
    "too expensive for this quality": "negative",
    "very disappointed": "negative",
    "slow shipping": "negative",
    "would not recommend": "negative",
    # neutral
    "where is my invoice": "neutral",
    "please confirm delivery date": "neutral",
    "do you have this in black": "neutral",
    "how to return the item": "neutral",
    "tracking number not received": "neutral",
    "is warranty included": "neutral",
}


def canon(text):
    return " ".join(str(text).split()).casefold()


def rows_of(ws):
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


def main():
    checks = []

    def add(name, ok, hint=""):
        checks.append((name, bool(ok), hint))

    pristine = load_workbook(PRISTINE, data_only=True)["Orders"]
    base = [r[:6] for r in rows_of(pristine)]  # колонки A:F
    try:
        expected = [LEXICON[canon(r[5])] for r in base[1:] if r[0] is not None]
    except KeyError as e:
        print(f"FAIL lexicon_coverage: фразы {e} нет в LEXICON — "
              f"дополни verifier")
        print("SCORE: 0.00")
        return
    exp_counts = Counter(expected)

    wsv = load_workbook(WB, data_only=True)
    wsf = load_workbook(WB, data_only=False)

    # --- лист Orders ---
    if "Orders" not in wsv.sheetnames:
        add("orders_sheet_exists", False, "нет листа Orders")
        add("orders_base_intact", False)
        add("sentiment_header", False)
        add("sentiment_values", False)
    else:
        s, sf = wsv["Orders"], wsf["Orders"]
        got = rows_of(s)
        add("orders_sheet_exists", True)
        add("orders_base_intact",
            len(got) == len(base) and all(g[:6] == b for g, b in zip(got, base)),
            "колонки A:F листа Orders изменены")

        hdr = s.cell(row=1, column=7).value
        add("sentiment_header",
            isinstance(hdr, str) and hdr.strip().casefold() == "sentiment",
            f"G1 = {hdr!r}")

        bad = []
        formulas = 0
        for i, exp in enumerate(expected):
            r = 2 + i
            v = s.cell(row=r, column=7).value
            f = sf.cell(row=r, column=7).value
            if isinstance(f, str) and f.startswith("="):
                formulas += 1
            elif not (isinstance(v, str) and v.strip().casefold() == exp):
                bad.append((r, v, exp))
        hint = ""
        if formulas:
            hint = f"{formulas} ячеек — формулы без кэша значений; " \
                   f"запиши вычисленный текст"
        elif bad:
            hint = f"несовпадений: {len(bad)}, например " \
                   f"строка {bad[0][0]}: got={bad[0][1]!r}, " \
                   f"expected={bad[0][2]!r}"
        add("sentiment_values", not bad and not formulas, hint)

        extra_col = s.cell(row=1, column=8).value is not None
        extra_row = s.cell(row=2 + len(expected), column=1).value is not None
        add("orders_no_extras", not extra_col and not extra_row,
            f"лишняя колонка H: {extra_col}, лишняя строка: {extra_row}")

    # --- лист Summary ---
    if "Summary" not in wsv.sheetnames:
        add("summary_sheet_exists", False, "нет листа Summary")
        add("summary_rows", False)
    else:
        sm = wsv["Summary"]
        hdr = tuple(sm.cell(row=1, column=c).value for c in (1, 2))
        add("summary_sheet_exists", True)
        add("summary_rows", hdr == ("sentiment", "count"), f"A1:B1 = {hdr}")
        for i, cls in enumerate(CLASSES):
            r = 2 + i
            got_cls = sm.cell(row=r, column=1).value
            got_cnt = sm.cell(row=r, column=2).value
            ok = (isinstance(got_cls, str)
                  and got_cls.strip().casefold() == cls
                  and isinstance(got_cnt, (int, float))
                  and int(got_cnt) == exp_counts[cls])
            add(f"summary[{cls}]", ok,
                f"got=({got_cls!r}, {got_cnt!r}), "
                f"expected=({cls!r}, {exp_counts[cls]})")
        no_extra = sm.cell(row=2 + len(CLASSES), column=1).value is None
        add("summary_no_extras", no_extra, "лишние строки в Summary")

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, hint in checks:
        print(("ok   " if ok else "FAIL ") + name + (f" ({hint})" if hint and not ok else ""))
    print(f"SCORE: {passed / len(checks):.2f}")


if __name__ == "__main__":
    main()
