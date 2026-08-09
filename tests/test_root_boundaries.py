"""根目录边界测试：根目录只能有正式入口 app.py，源码不得再导入旧根模块。"""

import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT_ENTRY_MODULES = {"app.py"}
EXPECTED_ROOT_PY = ROOT_ENTRY_MODULES
BANNED_ROOT_MODULES = {
    "main", "settings", "database",
    "analyzer", "backup", "email_report", "fetcher", "pdf_reader",
}


def _iter_python_files(base):
    for path in base.rglob("*.py"):
        parts = path.parts
        if ".venv" in parts or "__pycache__" in parts or "temp" in parts:
            continue
        yield path


class RootBoundaryTests(unittest.TestCase):
    def test_root_directory_contains_only_official_entry(self):
        root_py_files = {p.name for p in REPO_ROOT.glob("*.py")}
        self.assertEqual(root_py_files, EXPECTED_ROOT_PY)

    def test_source_and_tests_do_not_import_deleted_root_modules(self):
        for base in (REPO_ROOT, REPO_ROOT / "source", REPO_ROOT / "tests"):
            for path in _iter_python_files(base):
                with self.subTest(path=path):
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            names = {alias.name.split(".")[0] for alias in node.names}
                        elif isinstance(node, ast.ImportFrom):
                            names = {node.module.split(".")[0]} if node.module else set()
                        else:
                            continue
                        self.assertFalse(
                            names & BANNED_ROOT_MODULES,
                            f"{path} 导入了已删除的根模块: {names & BANNED_ROOT_MODULES}",
                        )


if __name__ == "__main__":
    unittest.main()
