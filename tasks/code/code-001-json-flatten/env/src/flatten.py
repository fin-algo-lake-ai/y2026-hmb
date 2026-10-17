"""Flatten a nested dict/list structure into a single-level dict.

flatten_json(obj) -> dict

Rules:
  - nested dict keys are joined with a dot:
        {"a": {"b": 1}}            -> {"a.b": 1}
  - list indices are appended as [i]:
        {"a": [1, 2]}              -> {"a[0]": 1, "a[1]": 2}
        {"a": [{"b": 2}]}          -> {"a[0].b": 2}
  - an empty dict {} or an empty list [] is kept as a value under its own key:
        {"e": {}}                  -> {"e": {}}
        {"e": []}                  -> {"e": []}
  - the root may be a dict or a list:
        [{"a": 1}]                 -> {"[0].a": 1}
  - scalar values (str, int, float, bool, None) are preserved as-is
  - the input object must not be mutated
"""


def flatten_json(obj):
    raise NotImplementedError
