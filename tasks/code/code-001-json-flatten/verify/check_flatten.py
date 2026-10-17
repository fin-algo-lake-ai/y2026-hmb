"""Verifier для code-001-json-flatten.

Загружает <HMB_WORKSPACE>/src/flatten.py и прогоняет независимые проверки.
Печатает по строке на проверку и в конце "SCORE: <доля пройденных>".
Код выхода 0 — verifier отработал (даже при score=0); ненулевой — verifier сломан.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path

WS = Path(os.environ["HMB_WORKSPACE"])
MODULE = WS / "src" / "flatten.py"

CASES = [
    ("flat",
     {"a": 1, "b": 2},
     {"a": 1, "b": 2}),
    ("nested_dicts",
     {"a": {"b": {"c": 3}}},
     {"a.b.c": 3}),
    ("list_of_scalars",
     {"xs": [1, 2]},
     {"xs[0]": 1, "xs[1]": 2}),
    ("dict_in_list",
     {"xs": [{"y": 1}, 5]},
     {"xs[0].y": 1, "xs[1]": 5}),
    ("deep_mixed",
     {"a": [{"b": [{"c": 7}]}]},
     {"a[0].b[0].c": 7}),
    ("empty_dict",
     {"e": {}, "k": 1},
     {"e": {}, "k": 1}),
    ("empty_list",
     {"e": [], "k": 1},
     {"e": [], "k": 1}),
    ("scalar_types",
     {"s": "x", "n": None, "b": False, "f": 1.5},
     {"s": "x", "n": None, "b": False, "f": 1.5}),
    ("root_list",
     [{"a": 1}, 2],
     {"[0].a": 1, "[1]": 2}),
]


def load_fn():
    spec = importlib.util.spec_from_file_location("flatten_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.flatten_json


def main():
    results = []
    try:
        fn = load_fn()
    except Exception as e:
        print(f"FAIL import src/flatten.py: {e}")
        print("SCORE: 0.00")
        return

    for name, inp, exp in CASES:
        try:
            got = fn(copy.deepcopy(inp))
            assert got == exp, f"got {got!r}, expected {exp!r}"
            results.append((name, True, ""))
        except Exception as e:
            results.append((name, False, str(e)))

    src = {"a": [{"b": 1}]}
    snap = json.dumps(src)
    try:
        fn(src)
        assert json.dumps(src) == snap, "входной объект был изменён"
        results.append(("no_mutation", True, ""))
    except Exception as e:
        results.append(("no_mutation", False, str(e)))

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, hint in results:
        print(("ok   " if ok else "FAIL ") + name + (f" ({hint})" if hint else ""))
    print(f"SCORE: {passed / len(results):.2f}")


if __name__ == "__main__":
    main()
