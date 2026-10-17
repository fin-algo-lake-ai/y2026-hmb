"""Flatten a nested dict/list structure into a single-level dict.

Эталонное решение для санити-прогона (`hmb sanity code-001-json-flatten`).
"""


def flatten_json(obj):
    out = {}

    def walk(prefix, node):
        if isinstance(node, dict):
            if not node:
                out[prefix] = {}
            else:
                for k, v in node.items():
                    walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, list):
            if not node:
                out[prefix] = []
            else:
                for i, v in enumerate(node):
                    walk(f"{prefix}[{i}]" if prefix else f"[{i}]", v)
        else:
            out[prefix] = node

    walk("", obj)
    return out
