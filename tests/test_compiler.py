"""编译核心纯函数与压缩逻辑的单元测试。"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pyd_build.compiler import CompileCore, get_file_hash, merge_exclude_patterns  # noqa: E402


class TestGetFileHash(unittest.TestCase):
    def test_matches_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.py"
            target.write_text("print('hello')\n", encoding="utf-8")
            self.assertEqual(get_file_hash(target), hashlib.sha256(target.read_bytes()).hexdigest())

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(get_file_hash(Path("__no_such_file__.py")))


class TestMergeExcludePatterns(unittest.TestCase):
    """默认忽略项与用户配置的合并规则。"""

    def test_user_patterns_first_then_defaults(self) -> None:
        merged = merge_exclude_patterns([".git", "custom.txt"], [".git", ".gitattributes"])
        self.assertEqual(merged, [".git", "custom.txt", ".gitattributes"])

    def test_empty_config_uses_defaults(self) -> None:
        self.assertEqual(merge_exclude_patterns(None, ["a", "b"]), ["a", "b"])


class TestCompressionFile(unittest.TestCase):
    def test_compresses_directory_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "project"
            (source / "sub").mkdir(parents=True)
            (source / "a.txt").write_text("a", encoding="utf-8")
            (source / "sub" / "b.txt").write_text("b", encoding="utf-8")

            CompileCore.compression_file(str(source))

            archive = Path(str(source) + ".zip")
            self.assertTrue(archive.exists())
            with zipfile.ZipFile(archive) as zf:
                names = {name.replace("\\", "/") for name in zf.namelist()}
            self.assertEqual(names, {"a.txt", "sub/b.txt"})

    def test_replaces_existing_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "project"
            source.mkdir()
            (source / "a.txt").write_text("a", encoding="utf-8")
            stale = Path(str(source) + ".zip")
            stale.write_bytes(b"stale")

            CompileCore.compression_file(str(source))

            with zipfile.ZipFile(stale) as zf:
                self.assertEqual(zf.namelist(), ["a.txt"])


class TestCleanExcludedInOutput(unittest.TestCase):
    """拷贝前按 exclude_dirs / exclude_files 清理输出目录残留。"""

    @staticmethod
    def _make_core(output_dir: Path) -> CompileCore:
        core = CompileCore.__new__(CompileCore)  # 不触发 __init__ 的配置/解释器读取
        core.log_callback = lambda _message: None
        core.exclude_dirs = [".venv", "__pycache__"]
        core.exclude_files = ["requirements.txt"]
        core.output_dir = str(output_dir)
        core._source_root = None
        core._loaded_cache = None
        core._hash_cache = {}
        core._ignored_dirs = 0
        core._ignored_files = 0
        return core

    def test_removes_excluded_and_keeps_others(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo_pyd"
            (out / ".venv").mkdir(parents=True)
            (out / ".venv" / "v.txt").write_text("x", encoding="utf-8")
            (out / "requirements.txt").write_text("old", encoding="utf-8")
            (out / "keep.py").write_text("old", encoding="utf-8")
            (out / "mod.cp312-win_amd64.pyd").write_bytes(b"pyd")

            self._make_core(out).clean_excluded_in_output()

            self.assertFalse((out / ".venv").exists())
            self.assertFalse((out / "requirements.txt").exists())
            self.assertTrue((out / "keep.py").exists())
            self.assertTrue((out / "mod.cp312-win_amd64.pyd").exists())

    def test_missing_output_dir_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self._make_core(Path(tmp) / "not_there").clean_excluded_in_output()


class TestEntryMode(unittest.TestCase):
    """入口文件的 import 依赖解析与拷贝裁剪。"""

    def _make_core(
        self,
        source_dir: Path,
        output_dir: Path,
        entry_file: str = "",
        extra_copy: list[str] | None = None,
    ) -> CompileCore:
        core = CompileCore.__new__(CompileCore)  # 跳过 __init__ 的配置与解释器读取
        core.log_callback = lambda _message: None
        core.exclude_dirs = []
        core.exclude_files = []
        core.source_dir = str(source_dir)
        core.output_dir = str(output_dir)
        core.entry_file = entry_file
        core.extra_copy = list(extra_copy or [])
        core.entry_path = None
        core.keep_py_keys = None
        core._source_root = None
        core._loaded_cache = None
        core._hash_cache = {}
        core._ignored_dirs = 0
        core._ignored_files = 0
        return core

    @staticmethod
    def _make_project(tmp: str) -> Path:
        project = Path(tmp) / "proj"
        (project / "pkg").mkdir(parents=True)
        (project / "main.py").write_text(
            "import json\nfrom pkg.a import foo\nimport pkg.b\n", encoding="utf-8"
        )
        (project / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (project / "pkg" / "a.py").write_text(
            "from . import b\nfrom .b import bar\nimport os\n", encoding="utf-8"
        )
        (project / "pkg" / "b.py").write_text("bar = 1\n", encoding="utf-8")
        (project / "unused.py").write_text("pass\n", encoding="utf-8")
        (project / "data").mkdir()
        (project / "data" / "x.json").write_text("{}", encoding="utf-8")
        return project

    def test_collects_local_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            core = self._make_core(project, Path(tmp) / "out", "main.py")

            self.assertTrue(core.prepare_entry_mode())
            self.assertEqual(
                core.keep_py_keys,
                {"main.py", "pkg/__init__.py", "pkg/a.py", "pkg/b.py"},
            )

    def test_empty_entry_disables_trimming(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            core = self._make_core(project, Path(tmp) / "out", "")

            self.assertTrue(core.prepare_entry_mode())
            self.assertIsNone(core.keep_py_keys)

    def test_entry_outside_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            outside = Path(tmp) / "other.py"
            outside.write_text("pass\n", encoding="utf-8")
            core = self._make_core(project, Path(tmp) / "out", str(outside))

            self.assertFalse(core.prepare_entry_mode())

    def test_ignore_keeps_only_code_by_default(self) -> None:
        """入口模式默认只拷贝代码：无关 py、数据目录、其他文件都被忽略"""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            core = self._make_core(project, Path(tmp) / "out", "main.py")
            self.assertTrue(core.prepare_entry_mode())

            ignored = core.combined_ignore(str(project), ["unused.py", "data", "README.md"])

            self.assertIn("unused.py", ignored)
            self.assertIn("data", ignored)
            self.assertIn("README.md", ignored)
            # 依赖所在的包目录必须保留，否则里面的 py 拷不进去
            self.assertNotIn("pkg", ignored)

    def test_extra_copy_allows_files_and_dirs(self) -> None:
        """extra_copy 指定的目录与文件会被保留"""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            (project / "README.md").write_text("doc", encoding="utf-8")
            core = self._make_core(
                project, Path(tmp) / "out", "main.py", extra_copy=["data", "README.md"]
            )
            self.assertTrue(core.prepare_entry_mode())

            ignored = core.combined_ignore(str(project), ["data", "README.md", "unused.py"])

            self.assertNotIn("data", ignored)
            self.assertNotIn("README.md", ignored)
            self.assertIn("unused.py", ignored)

    def test_extra_copy_keeps_parent_dir_for_nested_file(self) -> None:
        """指定 data/x.json 时，data 目录本身必须保留，目录内只放行指定文件"""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            core = self._make_core(
                project, Path(tmp) / "out", "main.py", extra_copy=["data/x.json"]
            )
            self.assertTrue(core.prepare_entry_mode())

            self.assertNotIn("data", core.combined_ignore(str(project), ["data"]))
            inner = core.combined_ignore(
                str(project / "data"), ["x.json", "y.txt"]
            )
            self.assertNotIn("x.json", inner)
            self.assertIn("y.txt", inner)

    def test_clean_non_code_removes_stale_files(self) -> None:
        """入口模式清理输出目录里不在白名单的旧文件，保留 pyd 与 extra_copy"""
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            out = Path(tmp) / "out"
            (out / "pkg").mkdir(parents=True)
            (out / "main.py").write_text("x", encoding="utf-8")
            (out / "stale.txt").write_text("x", encoding="utf-8")
            (out / "data").mkdir()
            (out / "data" / "x.json").write_text("{}", encoding="utf-8")
            (out / "pkg" / "a.cp312-win_amd64.pyd").write_bytes(b"pyd")
            core = self._make_core(project, out, "main.py", extra_copy=["data"])
            self.assertTrue(core.prepare_entry_mode())

            core.clean_non_code_in_output()

            self.assertFalse((out / "stale.txt").exists())
            self.assertTrue((out / "data" / "x.json").exists())
            self.assertTrue((out / "pkg" / "a.cp312-win_amd64.pyd").exists())
            self.assertTrue((out / "main.py").exists())

    def test_src_layout_dependency_collection(self) -> None:
        """包放在 src/ 下（标准 src 布局）时也能解析到依赖"""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "src" / "mypkg").mkdir(parents=True)
            (project / "main.py").write_text(
                "import sys\n"
                "sys.path.insert(0, 'src')\n"
                "from mypkg.core import run\n",
                encoding="utf-8",
            )
            (project / "src" / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
            (project / "src" / "mypkg" / "core.py").write_text(
                "from .util import helper\n\ndef run():\n    return helper()\n",
                encoding="utf-8",
            )
            (project / "src" / "mypkg" / "util.py").write_text(
                "def helper():\n    return 1\n", encoding="utf-8"
            )
            (project / "src" / "mypkg" / "unused.py").write_text("pass\n", encoding="utf-8")
            core = self._make_core(project, Path(tmp) / "out", "main.py")

            self.assertTrue(core.prepare_entry_mode())
            self.assertEqual(
                core.keep_py_keys,
                {
                    "main.py",
                    "src/mypkg/__init__.py",
                    "src/mypkg/core.py",
                    "src/mypkg/util.py",
                },
            )

    def test_stale_py_removed_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            out = Path(tmp) / "out"
            (out / "pkg").mkdir(parents=True)
            (out / "main.py").write_text("x", encoding="utf-8")
            (out / "old.py").write_text("x", encoding="utf-8")
            (out / "pkg" / "a.py").write_text("x", encoding="utf-8")
            (out / "pkg" / "a.cp312-win_amd64.pyd").write_bytes(b"pyd")

            core = self._make_core(project, out, "main.py")
            self.assertTrue(core.prepare_entry_mode())
            core.clean_stale_py_in_output()

            self.assertTrue((out / "main.py").exists())
            self.assertTrue((out / "pkg" / "a.py").exists())
            self.assertFalse((out / "old.py").exists())
            self.assertTrue((out / "pkg" / "a.cp312-win_amd64.pyd").exists())


if __name__ == "__main__":
    unittest.main()
