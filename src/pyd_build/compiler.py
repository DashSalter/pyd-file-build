"""编译核心：复制项目、生成 setup.py、调用 Cython 编译、清理临时文件并压缩。"""

import ast
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from textwrap import dedent

from pyd_build.config import Config

# 编译缓存文件名：统一名称，固定放在编译输出目录内
CACHE_FILE_NAME = "pyd_build_cache.json"

# 单个文件超过这个大小就不参与拷贝（通常不是源码）
MAX_COPY_FILE_SIZE = 100 * 1024 * 1024

# 编译产物后缀：清理输出目录时始终保留
BUILD_ARTIFACT_SUFFIXES = {".pyd", ".pdb", ".exp", ".lib", ".obj", ".c"}

# 不作为编译目标、保留为源码的入口类文件
SKIP_SOURCE_NAMES = {"__init__.py", "main.py"}

# 版本控制/缓存类文件对编译产物没有意义，默认不拷贝（与用户配置合并，配置优先保留原样）
DEFAULT_EXCLUDE_DIRS = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    ".idea",
    ".vscode",
    ".mypy_cache",
    ".pytest_cache",
)
DEFAULT_EXCLUDE_FILES = (
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    "*.pyc",
    "*.pyo",
)


def merge_exclude_patterns(configured, defaults) -> list[str]:
    """把默认忽略项并入用户配置，去重且用户配置在前"""
    merged = list(configured or [])
    for pattern in defaults:
        if pattern not in merged:
            merged.append(pattern)
    return merged


setup_template = """ # 生成 setup.py 文件
from setuptools import setup, Extension
from Cython.Build import cythonize

ext_modules = [
    %s
]

setup(
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={"language_level": "3"},
        nthreads=0,  # 使用所有CPU核心并行编译
        cache=True
    )
)
"""


def get_file_hash(file_path) -> str | None:
    """计算文件的 SHA256 哈希值，读取失败时返回 None"""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):  # 1MB 分块，减少系统调用
                sha256.update(chunk)
    except OSError:
        return None
    return sha256.hexdigest()


class CompileCore:
    """一次编译任务的执行器：拷贝项目 → 生成 setup.py → 编译 → 清理 →（可选）压缩。

    所有对外汇报都走 ``log_callback``，便于 GUI 与 CLI 共用。
    """

    def __init__(self, log_callback) -> None:
        self.log_callback = log_callback

        # 过滤规则：用户配置在前，再并入内置默认项
        self.exclude_dirs = merge_exclude_patterns(Config.exclude_dirs.value, DEFAULT_EXCLUDE_DIRS)
        self.exclude_files = merge_exclude_patterns(
            Config.exclude_files.value, DEFAULT_EXCLUDE_FILES
        )
        self.is_remove_exist_output_dir = bool(Config.is_remove_exist_output_dir.value)

        # 目录（源目录由 GUI 从入口文件推导，输出目录 = 源目录同级 + _pyd）
        self.source_dir = Config.project_path.value
        self.output_dir = Config.output_path.value
        self.project_path: Path | None = None
        self.output_path: Path | None = None
        self.setup_py_path: Path | None = None
        self._source_root: Path | None = None

        # 解释器与其版本（版本写进缓存 meta）
        self.interpreter_file = self._pick_interpreter()
        self.python_version = self._detect_python_version()

        # 缓存文件固定放在输出目录内；_loaded_cache 让一次任务只读一次磁盘
        self.cache_file = Path(self.output_dir) / CACHE_FILE_NAME
        self._loaded_cache: dict[str, dict] | None = None
        self._hash_cache: dict[Path, str] = {}

        # 入口模式：留空表示全量拷贝，填写后只拷贝依赖到的代码
        self.entry_file = (Config.entry_file.value or "").strip()
        self.extra_copy = [
            str(item).strip() for item in (Config.extra_copy.value or []) if str(item).strip()
        ]
        self.entry_path: Path | None = None
        self.keep_py_keys: set[str] | None = None

        # 拷贝回调里的忽略统计，避免每个目录都写一条日志
        self._ignored_dirs = 0
        self._ignored_files = 0

    def _pick_interpreter(self) -> str:
        """取配置里选中的解释器；索引越界或列表为空时安全回退"""
        interpreters = list(Config.interpreter_path.value or [])
        index = Config.interpreter_index.value
        if not interpreters:
            return sys.executable
        if not isinstance(index, int) or not 0 <= index < len(interpreters):
            return interpreters[0]
        return interpreters[index]

    def _detect_python_version(self) -> str:
        """读取解释器版本，失败时返回空串而不是让整个任务崩掉"""
        try:
            result = subprocess.run(
                [self.interpreter_file, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            self.log_callback(f"[警告] 无法获取解释器版本: {exc}")
            return ""
        return (result.stdout or "").strip().removeprefix("Python ").strip()

    def load_cache(self) -> dict[str, dict]:
        """读取缓存里的文件条目（meta 由 save_cache 维护，旧格式条目直接丢弃）

        一次任务内只读一次磁盘，结果缓存在 ``_loaded_cache``。
        """
        if self._loaded_cache is not None:
            return self._loaded_cache
        self._loaded_cache = {}

        if not self.cache_file.exists():
            return self._loaded_cache
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.log_callback(f"[警告] 无法读取缓存文件: {exc}")
            return self._loaded_cache

        files = data.get("files") if isinstance(data, dict) else None
        if isinstance(files, dict):
            for key, value in files.items():
                # 结构不完整的条目直接丢弃，由本次编译重建
                if isinstance(value, dict) and "hash" in value and "pyd_path" in value:
                    self._loaded_cache[key] = value
        return self._loaded_cache

    def save_cache(self, cache_data: dict[str, dict]) -> None:
        """保存缓存，并记录本次使用的解释器版本与原始项目路径"""
        payload = {
            "meta": {
                "python_version": self.python_version,
                "source_dir": self.source_dir,
                "updated_at": datetime.now().isoformat(),
            },
            "files": cache_data,
        }
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            self.log_callback(f"[警告] 无法保存缓存文件: {exc}")
            return
        self._loaded_cache = dict(cache_data)

    def _hash_of(self, py_file: Path) -> str | None:
        """带内存缓存的哈希值，同一文件一次任务内只算一次"""
        if py_file not in self._hash_cache:
            digest = get_file_hash(py_file)
            if digest is None:
                return None
            self._hash_cache[py_file] = digest
        return self._hash_cache[py_file]

    def _entry_key(self) -> str | None:
        """入口文件相对源目录的 POSIX 路径（未启用入口模式时为 None）"""
        if self.entry_path is None:
            return None
        try:
            return self.entry_path.relative_to(self._abs_source()).as_posix()
        except ValueError:  # 入口文件不在源目录内
            return None

    @staticmethod
    def _matches_any(name: str, patterns) -> bool:
        """名字是否命中任一过滤模式（支持通配符）"""
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    # ---------- 入口文件依赖分析 ----------

    def _abs_source(self) -> Path:
        """源目录的绝对路径（缓存一次，copytree 回调里会频繁调用）"""
        if self._source_root is None:
            self._source_root = Path(self.source_dir).resolve()
        return self._source_root

    def _module_roots(self) -> list[Path]:
        """模块搜索根：源码根，以及标准 src 布局下的 src 目录

        有些项目把包放在 src/ 下、运行时再把 src 加进 sys.path（例如
        ``src/pyd_build/app.py`` 被 ``from pyd_build.app import ...`` 导入），
        所以解析 import 时也要把 src 目录当作模块根。
        """
        source_root = self._abs_source()
        roots = [source_root]
        src_dir = source_root / "src"
        if src_dir.is_dir():
            roots.append(src_dir)
        return roots

    def prepare_entry_mode(self) -> bool:
        """解析入口文件并收集它依赖的本地模块

        - 未配置入口文件：不启用裁剪，返回 True，后续按原来的全量拷贝走
        - 配置了入口文件：解析 import 依赖，得到需要保留的 py 相对路径集合
        """
        if not self.entry_file:
            self.entry_path = None
            self.keep_py_keys = None
            return True

        source_root = self._abs_source()
        if not source_root.is_dir():
            self.log_callback(f"[错误] 源目录不存在: {source_root}")
            return False

        entry = Path(self.entry_file)
        if not entry.is_absolute():
            entry = source_root / entry
        entry = entry.resolve()

        if not entry.is_file():
            self.log_callback(f"[错误] 入口文件不存在: {entry}")
            return False
        if entry.suffix.lower() != ".py":
            self.log_callback(f"[错误] 入口文件必须是 .py 文件: {entry}")
            return False
        try:
            entry.relative_to(source_root)
        except ValueError:
            self.log_callback(f"[错误] 入口文件不在源目录内: {entry}")
            return False

        self.log_callback("[入口模式] 从入口文件解析依赖")
        self.log_callback(f"  -入口文件: {entry}")
        self.entry_path = entry
        dependencies = self._collect_dependencies(entry)
        dependencies = self._with_parent_inits(dependencies)
        self.keep_py_keys = {
            path.relative_to(source_root).as_posix() for path in dependencies
        }
        self.log_callback(
            f"  -收集到 {len(self.keep_py_keys)} 个 py 文件（入口 + 本地依赖 + 各级 __init__.py）"
        )
        return True

    def _collect_dependencies(self, entry: Path) -> set[Path]:
        """从入口出发，递归收集源目录内可解析的模块（遍历顺序不影响结果）"""
        roots = self._module_roots()
        seen: set[Path] = set()
        pending: list[Path] = [entry]
        dynamic_hits = 0

        while pending:
            current = pending.pop()
            resolved = current.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)

            targets, dynamic = self._iter_imports(resolved)
            dynamic_hits += dynamic
            for dotted in targets:
                for found in self._resolve_module(dotted, roots):
                    candidate = found.resolve()
                    if candidate not in seen:
                        pending.append(candidate)

        if dynamic_hits:
            self.log_callback(
                f"  [提示] 发现 {dynamic_hits} 处 importlib/__import__ 动态导入，无法静态追踪"
            )
        return seen

    def _iter_imports(self, py_file: Path) -> tuple[list[str], int]:
        """解析单个文件的 import 目标（点分模块名），并统计动态导入次数"""
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            self.log_callback(f"  [警告] 解析失败，跳过依赖分析: {py_file} ({exc})")
            return [], 0

        try:
            package_parts = list(py_file.relative_to(self._abs_source()).parts[:-1])
        except ValueError:
            package_parts = []

        targets: list[str] = []
        dynamic = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                targets.extend(self._from_targets(node, package_parts))
            elif isinstance(node, ast.Call):
                func = node.func
                func_name = getattr(func, "id", None) or getattr(func, "attr", None)
                if func_name in ("import_module", "__import__"):
                    dynamic += 1
        return targets, dynamic

    @staticmethod
    def _from_targets(node: ast.ImportFrom, package_parts: list[str]) -> list[str]:
        """把 from ... import ... 转成候选模块名（含相对导入）"""
        if node.level:
            drop = node.level - 1
            if drop > len(package_parts):
                return []
            base_parts = package_parts[: len(package_parts) - drop]
        else:
            base_parts = []

        module_parts = node.module.split(".") if node.module else []
        base = ".".join(base_parts + module_parts)

        targets = [base] if base else []
        for alias in node.names:
            if alias.name == "*":
                continue
            targets.append(f"{base}.{alias.name}" if base else alias.name)
        return targets

    @staticmethod
    def _resolve_module(dotted: str, roots: list[Path]) -> list[Path]:
        """点分模块名 → 各模块根下真实存在的 py 文件（模块或包的 __init__）"""
        if not dotted:
            return []
        found: list[Path] = []
        parts = dotted.split(".")
        for root in roots:
            base = root.joinpath(*parts)
            module_file = base.with_suffix(".py")
            if module_file.is_file() and module_file not in found:
                found.append(module_file)
            package_init = base / "__init__.py"
            if package_init.is_file() and package_init not in found:
                found.append(package_init)
        return found

    def _with_parent_inits(self, files: set[Path]) -> set[Path]:
        """补齐各级父包的 __init__.py，保证包结构完整"""
        source_root = self._abs_source()
        result = set(files)
        for file in files:
            try:
                relative = file.relative_to(source_root)
            except ValueError:
                continue
            parent = relative.parent
            while parent.parts:
                init = source_root / parent / "__init__.py"
                if init.is_file():
                    result.add(init.resolve())
                parent = parent.parent
        return result

    def clean_stale_py_in_output(self) -> None:
        """入口模式下清掉输出目录里不属于本次依赖范围的 py 文件"""
        if self.keep_py_keys is None:
            return
        output_dir = Path(self.output_dir)
        if not output_dir.is_dir():
            return

        removed = 0
        for py_file in output_dir.rglob("*.py"):
            if py_file.relative_to(output_dir).as_posix() in self.keep_py_keys:
                continue
            try:
                py_file.unlink()
                removed += 1
            except OSError as exc:
                self.log_callback(f"  [警告] 无法删除旧 py: {py_file} ({exc})")
        if removed:
            self.log_callback(f"  -已清除 {removed} 个不在依赖范围内的旧 py 文件")

    def clean_non_code_in_output(self) -> None:
        """入口模式下清掉输出目录里不属于本次要拷贝范围的文件

        只保留编译产物（.pyd/.c 等）、缓存文件，以及 extra_copy 显式指定的
        文件/目录；.py 交给 clean_stale_py_in_output 处理。
        """
        if self.keep_py_keys is None:
            return
        output_dir = Path(self.output_dir)
        if not output_dir.is_dir():
            return

        keep_suffixes = BUILD_ARTIFACT_SUFFIXES
        removed = 0
        for path in output_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(output_dir).as_posix()
            if path.name in (CACHE_FILE_NAME, "setup.py"):
                continue
            if path.suffix.lower() in keep_suffixes:
                continue
            if path.suffix.lower() == ".py":
                continue  # 交给 clean_stale_py_in_output
            if self._extra_allows(rel):
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                self.log_callback(f"  [警告] 无法删除残留文件: {path} ({exc})")
        if removed:
            self.log_callback(f"  -已清除 {removed} 个不在代码范围内的旧文件")

    @staticmethod
    def _remove_empty_dirs(root: Path) -> int:
        """入口模式裁剪后可能留下空目录，自底向上删掉"""
        removed = 0
        for dir_path, _, _ in os.walk(root, topdown=False):
            current = Path(dir_path)
            if current == root:
                continue
            try:
                if not any(current.iterdir()):
                    current.rmdir()
                    removed += 1
            except OSError:
                pass
        return removed

    def _relative_dir(self, dirname) -> str:
        """把 copytree 回调里的目录转成相对源目录的 POSIX 路径（根目录返回空串）"""
        try:
            rel = Path(dirname).resolve().relative_to(self._abs_source()).as_posix()
        except ValueError:
            return ""
        return "" if rel == "." else rel

    def _extra_allows(self, rel_posix: str) -> bool:
        """rel_posix 是否被 extra_copy 指定（自身被指定，或位于被指定的目录内）"""
        if not rel_posix:
            return False
        for raw in self.extra_copy:
            pattern = str(raw).strip().replace("\\", "/").strip("/")
            if not pattern:
                continue
            if "*" in pattern or "?" in pattern:
                if fnmatch.fnmatch(rel_posix, pattern):
                    return True
            elif rel_posix == pattern or rel_posix.startswith(pattern + "/"):
                return True
        return False

    def _extra_has_descendant(self, rel_posix: str) -> bool:
        """extra_copy 里是否有条目位于该目录下（目录必须保留，否则进不去）"""
        for raw in self.extra_copy:
            pattern = str(raw).strip().replace("\\", "/").strip("/")
            if not pattern:
                continue
            prefix = pattern
            if "*" in pattern or "?" in pattern:
                head = pattern[: min(i for i, ch in enumerate(pattern) if ch in "*?")]
                prefix = head.rsplit("/", 1)[0] if "/" in head else ""
                if not prefix:
                    return True  # 通配符可能落在任意层级
            if prefix == rel_posix or prefix.startswith(rel_posix + "/"):
                return True
        return False

    def combined_ignore(self, dirname, filenames):
        """copytree 的 ignore 回调

        - 全量模式（入口留空）：按 exclude_dirs / exclude_files / 大文件过滤
        - 入口模式：白名单，只拷贝依赖到的 .py 与 extra_copy 指定的文件/目录，
          不叠加 exclude_* 规则（两套规则会互相打架）
        """
        if self.keep_py_keys is not None:
            ignored = self._entry_mode_ignore(dirname, filenames)
        else:
            ignored = self._full_mode_ignore(dirname, filenames)

        if ignored:
            self._ignored_dirs += 1
            self._ignored_files += len(ignored)
        return ignored

    def _entry_mode_ignore(self, dirname, filenames) -> set[str]:
        """入口模式白名单：只有依赖到的 .py 与 extra_copy 命中项放行"""
        dir_rel = self._relative_dir(dirname)
        ignored: set[str] = set()
        for name in filenames:
            rel = f"{dir_rel}/{name}" if dir_rel else name
            full_path = os.path.join(dirname, name)
            if os.path.isdir(full_path):
                if not self._keep_dir(rel):
                    ignored.add(name)
            elif name.lower().endswith(".py"):
                if rel not in self.keep_py_keys:
                    ignored.add(name)
            elif not self._extra_allows(rel):
                ignored.add(name)
        return ignored

    def _keep_dir(self, rel: str) -> bool:
        """目录要进得去：自身被额外拷贝指定、下层还有额外拷贝项，或里面有依赖的 py"""
        return (
            self._extra_allows(rel)
            or self._extra_has_descendant(rel)
            or any(key == rel or key.startswith(rel + "/") for key in self.keep_py_keys)
        )

    def _full_mode_ignore(self, dirname, filenames) -> set[str]:
        """全量模式黑名单：目录/文件名规则 + 超大文件，一次遍历完成"""
        ignored: set[str] = set()
        for name in filenames:
            full_path = os.path.join(dirname, name)
            if os.path.isdir(full_path):
                if self._matches_any(name, self.exclude_dirs):
                    ignored.add(name)
                continue
            if self._matches_any(name, self.exclude_files):
                ignored.add(name)
                continue
            try:
                if os.path.getsize(full_path) > MAX_COPY_FILE_SIZE:
                    ignored.add(name)
            except OSError:  # 无权限读取时按忽略处理
                ignored.add(name)
        return ignored

    def clean_excluded_in_output(self) -> None:
        """拷贝前按过滤规则清除输出目录中的残留项

        输出目录若已存在，上一轮拷进去、此后才被规则排除的内容仍会留在里面：
        copytree 的 ignore 只决定「这次拷什么」，不会删除旧内容。
        """
        output_dir = self.output_dir
        if not os.path.isdir(output_dir):
            return

        removed_dirs = []
        removed_files = []
        failures = []
        for root, dir_names, file_names in os.walk(output_dir, topdown=True):
            for dir_name in list(dir_names):
                full_path = os.path.join(root, dir_name)
                if self._matches_any(dir_name, self.exclude_dirs):
                    try:
                        shutil.rmtree(full_path)
                        removed_dirs.append(os.path.relpath(full_path, output_dir))
                    except OSError as e:
                        failures.append(f"{full_path}: {e}")
                    dir_names.remove(dir_name)

            for file_name in file_names:
                full_path = os.path.join(root, file_name)
                if self._matches_any(file_name, self.exclude_files):
                    try:
                        os.remove(full_path)
                        removed_files.append(os.path.relpath(full_path, output_dir))
                    except OSError as e:
                        failures.append(f"{full_path}: {e}")

        if removed_dirs or removed_files:
            self.log_callback(
                f"  -已清除 {len(removed_dirs)} 个目录、{len(removed_files)} 个文件"
            )
        else:
            self.log_callback("  -输出目录干净，没有需要清除的过滤项")
        for item in failures:
            self.log_callback(f"  [警告] 清除失败: {item}")

    def copy_the_folder(self) -> bool:
        """把源项目按当前模式拷贝到输出目录（入口模式只拷代码）"""
        self.log_callback("[拷贝目录]")
        if not self.prepare_entry_mode():
            return False

        output_dir = self.output_dir  # 输出目录，名称后面 +_pyd

        if os.path.exists(output_dir) and self.is_remove_exist_output_dir:
            try:  # 如果文件已经存在则先删除
                shutil.rmtree(output_dir)
            except OSError as exc:
                self.log_callback(f"删除输出目录失败: {output_dir}, {exc} \n 请手动删除输出目录后再尝试")
                return False
            self.log_callback("将已经存在的文件夹清除完成")

        # 拷贝前先清掉输出目录里不该保留的内容（不影响增量编译的 .pyd）
        if self.keep_py_keys is not None:
            self.log_callback("  -按代码白名单清理输出目录")
            self.clean_non_code_in_output()
        else:
            self.log_callback("  -按过滤规则清理输出目录")
            self.clean_excluded_in_output()
        self.clean_stale_py_in_output()

        self.log_callback("  -开始拷贝目录")
        shutil.copytree(
            self.source_dir, str(output_dir), dirs_exist_ok=True, ignore=self.combined_ignore
        )
        if self._ignored_dirs or self._ignored_files:
            self.log_callback(
                f"  -已忽略 {self._ignored_dirs} 个目录 / {self._ignored_files} 个文件"
            )
        if self.keep_py_keys is not None:
            removed = self._remove_empty_dirs(Path(output_dir))
            if removed:
                self.log_callback(f"  -清理裁剪后留下的空目录 {removed} 个")
        self.log_callback("  -目录拷贝完成\n\n")

        return True

    def get_input_output_dir(self) -> bool:
        """确认源目录与输出目录都存在，并记录绝对路径"""
        self.log_callback("[确认输出目录和源目录]")
        self.project_path = Path(self.source_dir).resolve()
        self.output_path = Path(self.output_dir).resolve()

        if not self.project_path.is_dir():
            self.log_callback(f"[错误] 源目录不存在: {self.project_path}")
            return False

        if not self.output_path.is_dir():
            self.log_callback(f"[错误] 输出目录不存在: {self.output_path}")
            return False

        self.log_callback(f"  -源目录: {self.project_path}")
        self.log_callback(f"  -输出目录: {self.output_path}")
        self.log_callback(f"  -解释器路径: {self.interpreter_file}\n\n")

        return True

    def find_py_files(self):
        """查找输出目录里待编译的 py 文件（跳过入口、main、__init__ 与未改动的已编译项）"""
        self.log_callback("[正在查找需编译的 py 文件]")

        cache = self.load_cache()
        py_files_to_compile: list[Path] = []
        all_py_files: list[Path] = []
        skipped = 0
        entry_key = self._entry_key()
        if self.keep_py_keys is not None:
            self.log_callback(f"  -入口模式：仅处理依赖范围内的 {len(self.keep_py_keys)} 个 py 文件")

        for py_file in self.output_path.rglob("*.py"):
            if py_file.name in SKIP_SOURCE_NAMES or ".venv" in py_file.parts:
                continue
            relative = py_file.relative_to(self.output_path)
            if entry_key is not None and relative.as_posix() == entry_key:
                continue

            all_py_files.append(py_file)
            file_hash = self._hash_of(py_file)
            cached = cache.get(relative.as_posix())
            if (
                cached is not None
                and file_hash is not None
                and cached.get("hash") == file_hash
                and Path(cached.get("pyd_path", "")).exists()
            ):
                skipped += 1
            else:
                py_files_to_compile.append(py_file)

        self.log_callback(f"  -已经编译完成 {skipped} 个文件，这些文件将不再重复编译！")
        self.log_callback(f"  -找到 {len(py_files_to_compile)} 个需编译的.py 文件\n\n")

        return py_files_to_compile, all_py_files

    def create_setup(self, py_files) -> str:
        """为每个 py 生成一个 Extension，保证编译后的导入路径与原结构一致"""
        self.log_callback("[setup_content 生成]")
        extensions = []
        for py_file in py_files:
            relative_path = py_file.relative_to(self.output_path)
            # 将路径 'a/b/c.py' 转换为模块名 'a.b.c'
            module_name = ".".join(relative_path.with_suffix("").parts)
            extensions.append(f'Extension("{module_name}", [r"{py_file}"])')

        setup_content = dedent(setup_template % (",\n    ".join(extensions)))
        self.log_callback("  -setup_content 生成完成\n\n")
        return setup_content

    def clean_temp_files(self, py_files) -> None:
        """删除编译用的 py/c 临时文件、setup.py 与 build 目录"""
        self.log_callback("[清理临时文件]")

        for py_file in py_files:
            if py_file.exists():
                py_file.unlink()
            c_path = py_file.with_suffix(".c")
            if c_path.exists():
                c_path.unlink()
        self.log_callback(f"  -已清理 {self.output_path} 中的所有 py 文件和 c 文件")

        if self.setup_py_path is not None and self.setup_py_path.exists():
            self.setup_py_path.unlink()
            self.log_callback("  -已删除 setup.py")

        for build_dir in (self.output_path / "build", self.project_path / "build"):
            if build_dir.is_dir():
                shutil.rmtree(build_dir, ignore_errors=True)
                self.log_callback(f"  -已删除 {build_dir} 目录")

        self.log_callback("  -清理任务执行完成。\n\n")

    def binary_compile(self, setup_content: str, py_files) -> bool:
        """调用解释器执行 setup.py build_ext，把 py 编译成 pyd"""
        self.log_callback("[编译 py 文件]")

        # setup.py 写在输出目录，避免覆盖源项目里的同名文件
        self.setup_py_path = self.output_path / "setup.py"
        self.setup_py_path.write_text(setup_content, encoding="utf-8")

        self.log_callback(f"  -使用解释器: {self.interpreter_file}")

        # --build-lib 指定最终 .pyd 的输出位置，--parallel 按 CPU 核数并行编译
        num_cores = os.cpu_count() or 4
        cmd = [
            self.interpreter_file,
            "setup.py",
            "build_ext",
            "--build-lib",
            str(self.output_path),
            "--parallel",
            str(num_cores),
        ]
        self.log_callback(f"  -编译命令: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            cwd=self.output_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        # 实时读取输出，只把错误行送进日志，避免刷屏
        error_lines = 0
        assert process.stdout is not None
        for line in process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if "error" in lowered or "failed" in lowered:
                error_lines += 1
                if error_lines <= 20:
                    self.log_callback(f"  -编译错误: {stripped}")
        process.wait()

        self.create_cache(py_files)

        if process.returncode != 0:
            self.log_callback(f"\n[错误] 编译过程返回错误码: {process.returncode}")
            self.log_callback("  -详细错误请查看上方编译输出")
            return False

        self.log_callback(f"-编译完成!编译结果已存放在: {self.output_path}\n\n")
        return True

    def _find_pyd_for(self, py_file: Path) -> str | None:
        """在输出目录里找某个 .py 对应的 .pyd（不依赖编译器命令行输出）"""
        relative = py_file.relative_to(self.output_path)
        directory = self.output_path / relative.parent
        if not directory.is_dir():
            return None

        stem = relative.stem
        for candidate in directory.glob(f"{stem}.*.pyd"):
            return str(candidate)

        fallback = directory / f"{stem}.pyd"
        return str(fallback) if fallback.exists() else None

    def create_cache(self, py_files) -> None:
        """把本次编译成功的 py → pyd 映射写入输出目录下的缓存文件"""
        cache = dict(self.load_cache())
        updated = 0
        for py_file in py_files:
            pyd_path = self._find_pyd_for(py_file)
            if pyd_path is None:
                continue
            cache[py_file.relative_to(self.output_path).as_posix()] = {
                "hash": self._hash_of(py_file),
                "pyd_path": pyd_path,
                "timestamp": datetime.now().isoformat(),
            }
            updated += 1

        if not updated:
            self.log_callback("  -未找到编译产物，本次不更新缓存")
            return

        self.save_cache(cache)
        self.log_callback(f"  -创建缓存完成（{updated} 个文件 → {self.cache_file}）")

    def start_compile_project(self, compression: bool) -> None:
        """完整流程：拷贝 → 找文件 → 生成 setup.py → 编译 → 清理 → 可选压缩"""
        start_time = time.time()

        if not self.copy_the_folder():
            self.log_callback("运行终止，拷贝文件夹失败！")
            return

        if not self.get_input_output_dir():
            self.log_callback("运行终止，获取输入输出目录失败！")
            return

        py_files, all_py_files = self.find_py_files()
        if py_files:
            setup_content = self.create_setup(py_files)
            if not self.binary_compile(setup_content, py_files):
                self.log_callback("运行终止，编译失败！请尝试重新进行编译操作！")
                return
            self.clean_temp_files(all_py_files)

        if compression:
            self.log_callback("[压缩输出目录]")
            self.compression_file(str(self.output_path))
            self.log_callback(f"  -压缩完成!压缩文件已存放在: {self.output_path}.zip\n\n")

        elapsed = time.time() - start_time
        self.log_callback("[运行结果]")
        self.log_callback(f"  -程序结束运行,运行时间: {elapsed:.2f} 秒")
        self.log_callback(f"  -编译结果已存放在: {self.output_path}")

    @staticmethod
    def compression_file(src_dir: str) -> None:
        """把指定目录打包成同名 zip（zip 内路径统一用 /）"""
        src = Path(src_dir).resolve()
        zip_path = Path(f"{src}.zip")
        if zip_path.exists():
            zip_path.unlink()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in src.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(src).as_posix())
