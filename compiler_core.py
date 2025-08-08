import os
import sys
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent
import json
import hashlib
from datetime import datetime

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


def get_file_hash(file_path):
    """计算文件的 SHA256 哈希值"""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception as e:
        print(f"[错误] 无法读取文件 {file_path}: {e}")
        return None


class CompileCore:
    def __init__(self, source_dir: str, log_callback, interpreter_file):
        self.exclude_dirs = [".venv", ".git", "__pycache__", ".idea"]  # 拷贝要排除的目录名列表（支持通配符）
        self.sep_len = 60  # 分割线长度
        self.is_remove_exist_output_dir = False  # 是否删除已存在的输出目录

        self.source_dir = source_dir
        self.source_file_name = os.path.basename(source_dir)  # 源文件名

        self.log_callback = log_callback
        self.interpreter_file = interpreter_file

        self.output_dir = ''  # 输出目录
        self.project_path: Path | None = None  # 源项目路径对象
        self.output_path: Path | None = None  # 输出目录对象
        self.setup_py_path: Path | None = None  # setup.py 文件路径

        self.cache_file = Path(fr"pyd_cache\.{self.source_file_name}.json").resolve()  # 缓存文件路径
        self.pyd_map = {}  # 保存编译后的.pyd 文件路径和文件名的映射关系

    def load_cache(self):
        """读取缓存数据"""
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.log_callback(f"[ 警告] 无法读取缓存文件: {e}")
            return {}

    def save_cache(self, cache_data):
        """保存缓存数据"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log_callback(f"[ 警告] 无法保存缓存文件: {e}")

    def copy_the_folder(self) -> bool:
        """将项目进行复制"""
        # 拷贝整个文件夹，并且将最外面的文件夹重命名
        file_path, file_name = os.path.split(self.source_dir)
        output_dir = os.path.join(file_path, file_name + "_pyd")  # 获取新的输出目录,名称后面+_pyd
        self.output_dir = output_dir
        if os.path.exists(output_dir) and self.is_remove_exist_output_dir:
            try:  # 如果文件已经存在则先删除
                shutil.rmtree(output_dir)
            except Exception as e:
                self.log_callback(f"删除输出目录失败: {output_dir}, {e} \n 请手动删除输出目录后再尝试")
                return False
            self.log_callback(f"将已经存在的文件夹清除完成")

        self.log_callback(f"开始拷贝目录")
        shutil.copytree(
            self.source_dir, output_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*self.exclude_dirs)
        )
        self.log_callback(f"目录拷贝完成")
        self.log_callback("-" * self.sep_len)
        return True

    def get_input_output_dir(self) -> bool:
        # 确认输出目录和源目录
        self.project_path: Path = Path(self.source_dir).resolve()
        self.output_path: Path = Path(self.output_dir).resolve()

        if not self.project_path.is_dir():
            self.log_callback(f"[错误] 源目录不存在: {self.project_path}")
            return False

        if not self.output_path.is_dir():
            self.log_callback(f"[错误] 输出目录不存在: {self.output_path}")
            return False

        self.log_callback(f"源目录: {self.project_path}")
        self.log_callback(f"输出目录: {self.output_path}")
        self.log_callback("-" * self.sep_len)
        return True

    def find_py_files(self):
        """查找所有 .py 文件，并过滤出需要编译的"""
        self.log_callback("---  正在查找需编译的 py 文件 ---")

        py_files_to_compile = []
        all_py_files = []
        cache = self.load_cache()

        for py_file in self.output_path.rglob("*.py"):
            if py_file.name in ("__init__.py", "main.py") or ".venv" in py_file.parts:
                continue

            file_hash = get_file_hash(py_file)
            relative_path = str(py_file.relative_to(self.output_path))
            cached = cache.get(relative_path)

            if cached and cached["hash"] == file_hash and os.path.exists(cached["pyd_path"]):
                self.log_callback(f" 跳过已编译且未更改的文件: {relative_path}")
            else:
                py_files_to_compile.append(py_file)
            all_py_files.append(py_file)

        self.log_callback(f" 找到 {len(py_files_to_compile)} 个需编译的.py 文件")
        self.log_callback("-" * self.sep_len)
        return py_files_to_compile, all_py_files

    def create_setup(self, py_files):
        # 这种为每个文件创建 Extension 的方式最健壮，能确保模块导入路径正确
        extensions = []
        for py_file in py_files:
            relative_path = py_file.relative_to(self.output_path)
            # 将路径 'a/b/c.py' 转换为模块名 'a.b.c'
            module_name = ".".join(relative_path.with_suffix("").parts)
            extensions.append(f'Extension("{module_name}", [r"{relative_path}"])')

        setup_content = dedent(setup_template % (",\n    ".join(extensions)))
        self.log_callback("--- setup_content 生成完成 ---")
        self.log_callback("-" * self.sep_len)
        return setup_content

    def clean_temp_files(self, py_files):
        # 4. 清理临时文件
        self.log_callback("\n--- 步骤 3: 正在清理临时文件 ---")

        for py_file in py_files:
            print(py_file)
            os.remove(py_file)
        self.log_callback(f"  - 已清理{self.output_path}中的所有py文件")

        if self.setup_py_path.exists():
            self.setup_py_path.unlink()
            self.log_callback("  - 已删除 setup.py")

        build_dir = os.path.join(self.output_path, "build")
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)
            self.log_callback("  - 已删除 build 目录")

        build_dir = os.path.join(self.project_path, "build")
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)
            self.log_callback("  - 已删除 build 目录")

        c_files = self.output_path.rglob("*.c")
        for c_file in c_files:
            c_file.unlink()
        self.log_callback(f"  - 已删除 {self.output_path} 中临时的 .c 文件")

        c_files = self.project_path.rglob("*.c")
        for c_file in c_files:
            c_file.unlink()
        self.log_callback(f"  - 已删除 {self.project_path} 中临时的 .c 文件")

        self.log_callback("  - 清理任务执行完成。")

    def binary_compile(self, setup_content) -> bool:
        # 1. 编译 .py 文件为 .c 文件
        self.log_callback("\n--- 步骤 1: 正在编译 py 文件 ---")

        # # 3. 执行编译
        self.log_callback("\n--- 步骤 2: 正在执行编译 ---")
        self.setup_py_path = self.project_path / "setup.py"
        self.setup_py_path.write_text(setup_content, encoding='utf-8')

        self.log_callback(f"使用解释器: {self.interpreter_file}")

        # 使用 Popen 实时流式传输输出，而不是等待命令结束
        # --build-lib 参数指定了最终.pyd文件的输出位置

        # 获取CPU核心数，如果获取不到则默认使用4个线程
        num_cores = os.cpu_count() or 4
        cmd = [self.interpreter_file, "setup.py", "build_ext", "--build-lib", str(self.output_path),
               "--parallel", str(num_cores)]
        self.log_callback(f"编译命令: {' '.join(cmd)}")

        # Windows下，C编译器输出通常是GBK编码
        encoding = 'gbk' if sys.platform == 'win32' else 'utf-8'

        process = subprocess.Popen(
            cmd, cwd=self.project_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding=encoding, errors='replace'
        )

        # 实时读取输出并发送到UI
        self.pyd_map.clear()
        while True:
            line = process.stdout.readline()
            if not line:
                break
            out_data = line.strip()
            self.log_callback(out_data)
            if '/OUT:' in out_data:
                pyd_path = out_data.split('/OUT:')[-1].split(r' /IMPLIB')[0]
                file_name = os.path.basename(pyd_path).split('.')[0] + '.py'

                self.pyd_map[file_name] = pyd_path

        process.wait()

        if process.returncode != 0:
            self.log_callback(f"\n[错误] 编译过程返回错误码: {process.returncode}")
            return False

        self.log_callback(f"\n[成功] 编译完成！编译结果已存放在: {self.output_path}")
        return True

    def creat_cache(self, py_files):
        # 执行编译命令后，更新缓存
        cache = self.load_cache()
        for py_file in py_files:
            file_name = os.path.basename(py_file)
            file_hash = get_file_hash(py_file)
            pyd_path = self.pyd_map.get(file_name)

            relative_path = Path(pyd_path).resolve()

            if relative_path.exists():
                cache[str(file_name)] = {
                    "hash": file_hash,
                    "pyd_path": str(pyd_path),
                    "timestamp": datetime.now().isoformat()
                }

        self.save_cache(cache)

    def start_compile_project(self) -> None:
        if self.copy_the_folder() is False:
            return self.log_callback(f"运行终止，拷贝文件夹失败！")

        if self.get_input_output_dir() is False:
            return self.log_callback(f"运行终止，获取输入输出目录失败！")

        py_files, all_py_files = self.find_py_files()  # 找到需要编译的py文件
        setup_content = self.create_setup(py_files)
        if self.binary_compile(setup_content) is False:
            return self.log_callback(f"运行终止，编译失败！")

        self.creat_cache(py_files)
        self.clean_temp_files(all_py_files)
        self.log_callback("程序结束运行")
        return None


if __name__ == '__main__':
    # 示例用法
    CompileCore(r"D:\pyd-file-build\db_integration", print, r'D:/Python312/python.exe').start_compile_project()
