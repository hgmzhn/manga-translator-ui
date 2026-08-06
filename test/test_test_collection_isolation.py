import _bootstrap  # noqa: F401

import ast
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
_MUTATING_METHODS = {
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "__delitem__",
    "__setitem__",
}


def _is_sys_modules(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "modules"
    )


def _targets_sys_modules(node: ast.AST) -> bool:
    return _is_sys_modules(node) or (
        isinstance(node, ast.Subscript) and _is_sys_modules(node.value)
    )


class _CollectionMutationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.lines: set[int] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(_targets_sys_modules(target) for target in node.targets):
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if _targets_sys_modules(node.target):
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _targets_sys_modules(node.target):
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        if any(_targets_sys_modules(target) for target in node.targets):
            self.lines.add(node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and _is_sys_modules(node.func.value)
            and node.func.attr in _MUTATING_METHODS
        ):
            self.lines.add(node.lineno)
        self.generic_visit(node)


def _collection_mutation_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _CollectionMutationVisitor()
    visitor.visit(tree)
    return sorted(visitor.lines)


def test_modules_do_not_mutate_sys_modules_during_collection():
    violations = {
        path.name: lines
        for path in sorted(TEST_DIR.glob("test_*.py"))
        if (lines := _collection_mutation_lines(path))
    }
    assert not violations, f"module-level sys.modules mutations: {violations}"


def main() -> int:
    test_modules_do_not_mutate_sys_modules_during_collection()
    print("test collection isolation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
