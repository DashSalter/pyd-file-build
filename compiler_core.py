# compiler_core.py
# 核心编译逻辑模块

import os
import sys
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent


def compile_project(source_dir: str, output_dir: str, log_callback, interpreter_file):
    """
    编译指定项目目录，并将实时日志通过回调函数发送。

    参数:
        source_dir (str): 要编译的Python源代码目录。
        output_dir (str): 存放编译结果的目标目录。
        log_callback (function): 用于接收日志消息的回调函数，例如 log_callback("some message")。

    返回:
        bool: 编译成功返回 True，否则返回 False。
    """
    try:
        project_path = Path(source_dir).resolve()
        output_path = Path(output_dir).resolve()

        if not project_path.is_dir():
            log_callback(f"[错误] 源目录不存在: {project_path}")
            return False

        log_callback(f"源目录: {project_path}")
        log_callback(f"输出目录: {output_path}")
        log_callback("-" * 60)

        # 1. 查找所有 .py 文件
        log_callback("--- 步骤 1: 正在查找 .py 文件 ---")

        # 排除 __init__.py 文件，因为它们通常不包含需要编译的代码
        py_files = [p for p in project_path.rglob("*.py") if p.name != "__init__.py"]

        if not py_files:
            log_callback("[警告] 未找到任何需要编译的 .py 文件。")
            return True  # technically not a failure

        log_callback(f"找到 {len(py_files)} 个 .py 文件进行编译。")

        # 2. 生成 setup.py 内容
        # 这种为每个文件创建 Extension 的方式最健壮，能确保模块导入路径正确
        extensions = []
        for py_file in py_files:
            relative_path = py_file.relative_to(project_path)
            # 将路径 'a/b/c.py' 转换为模块名 'a.b.c'
            module_name = ".".join(relative_path.with_suffix("").parts)
            extensions.append(f'Extension("{module_name}", ["{relative_path}"])')

        setup_content = dedent(f"""
            from setuptools import setup, Extension
            from Cython.Build import cythonize

            ext_modules = [
                {",    ".join(extensions)}
            ]

            setup(
                ext_modules=cythonize(
                    ext_modules,
                    compiler_directives={{"language_level": "3"}},
                    nthreads=0  # 使用所有CPU核心并行编译
                )
            )
        """)

        # 3. 执行编译
        log_callback("\n--- 步骤 2: 正在执行编译 ---")
        setup_py_path = project_path / "setup.py"
        setup_py_path.write_text(setup_content, encoding='utf-8')

        log_callback(f"使用解释器: {interpreter_file}")

        # 使用 Popen 实时流式传输输出，而不是等待命令结束
        # --build-lib 参数指定了最终.pyd文件的输出位置
        cmd = [interpreter_file, "setup.py", "build_ext", "--build-lib", str(output_path)]
        log_callback(f"编译 cmd: {' '.join(cmd)}")

        # Windows下，C编译器输出通常是GBK编码
        encoding = 'gbk' if sys.platform == 'win32' else 'utf-8'

        process = subprocess.Popen(cmd, cwd=project_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding=encoding, errors='replace')

        # 实时读取输出并发送到UI
        while True:
            line = process.stdout.readline()
            if not line:
                break
            log_callback(line.strip())

        process.wait()

        if process.returncode != 0:
            log_callback(f"\n[错误] 编译过程返回错误码: {process.returncode}")
            return False

        log_callback(f"\n[成功] 编译完成！编译结果已存放在: {output_path}")
        return True

    except Exception as e:
        import traceback
        log_callback(f"\n[严重错误] 发生意外异常: {e}")
        log_callback(traceback.format_exc())
        return False

    finally:
        # 4. 清理临时文件
        log_callback("\n--- 步骤 3: 正在清理临时文件 ---")
        build_dir = project_path / "build"
        c_files = project_path.rglob("*.c")

        # 删除临时 setup.py
        if 'setup_py_path' in locals() and setup_py_path.exists():
            setup_py_path.unlink()
            log_callback("  - 已删除 setup.py")

        # 删除 build 目录
        if build_dir.exists():
            shutil.rmtree(build_dir)
            log_callback("  - 已删除 build 目录")

        # 删除所有生成的 .c 文件
        c_files_deleted = 0
        for c_file in c_files:
            c_file.unlink()
            c_files_deleted += 1
        if c_files_deleted > 0:
            log_callback(f"  - 已删除 {c_files_deleted} 个临时的 .c 文件")

        log_callback("\n清理完成。")
