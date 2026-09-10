"""Names used but never defined — the class of mistake py_compile cannot see.

A slice edit once removed seven functions from fetch.py and left every call to them
in place. The file still compiled, render still ran, the workflow still went green,
and the chain died on the first real run. This would have caught it on the spot.
"""
import ast
import builtins
import sys

BINDING = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def bound_in(node):
    """Every name bound anywhere under this node, nested scopes included.

    Deliberately generous: a closure reading its parent's variable is not the thing
    being looked for, and treating it as one would bury the thing that is.
    """
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, BINDING):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.Global) or isinstance(n, ast.Nonlocal):
            out |= set(n.names)
    return out


def undefined(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    module = set(dir(builtins)) | bound_in(tree)
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, BINDING[:2])]:
        known = module | bound_in(fn)
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in known:
                bad.append(f"{path}:{n.lineno}: {fn.name}() uses undefined {n.id!r}")
    return sorted(set(bad))


if __name__ == "__main__":
    found = [b for f in sys.argv[1:] for b in undefined(f)]
    for b in found:
        print(b)
    print(f"checked {' '.join(sys.argv[1:])} — {len(found)} problem(s)")
    sys.exit(1 if found else 0)
