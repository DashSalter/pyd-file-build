import os
import sys
import shutil
import asyncio
import subprocess
from pathlib import Path
from textwrap import dedent
from concurrent.futures import ThreadPoolExecutor, as_completed

# 要排除的目录名列表（支持通配符）
exclude_dirs = [".venv", ".git", "__pycache__", ".idea", "node_modules", "dist", "build"]


class AsyncCompiler:
    """异步编译管理器，支持并行任务执行和实时日志回调"""

    def __init__(self, max_workers=None):
        # 根据CPU核心数自动设置最大工作线程数
        self.max_workers = max_workers or os.cpu_count() or 4
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self.active_tasks = set()

    async def compile_project_async(self, source_dir: str, log_callback, interpreter_file):
        """
        异步编译指定项目目录

        参数:
            source_dir (str): Python源代码目录
            log_callback (function): 日志回调函数
            interpreter_file (str): Python解释器路径

        返回:
            bool: 编译成功返回True，否则False
        """
        output_dir = ""
        try:
            # 创建输出目录路径
            file_path, file_name = os.path.split(source_dir)
            output_dir = os.path.join(file_path, file_name + "_pyd")

            # 清理并创建输出目录
            await self._prepare_output_dir(output_dir, log_callback)

            # 复制项目文件到输出目录
            await self._copy_project_files(source_dir, output_dir, log_callback)

            # 查找所有需要编译的Python文件
            py_files = await self._find_py_files(output_dir, log_callback)

            if not py_files:
                log_callback("[错误] 未找到可编译的.py文件")
                return False

                # 创建扩展模块列表
            extensions = await self._create_extensions(output_dir, py_files, log_callback)

            # 生成setup.py 内容
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
                        nthreads={self.max_workers}   # 使用所有CPU核心并行编译
                    )
                )
            """)

            # 写入setup.py 文件
            setup_py_path = Path(output_dir) / "setup.py"
            setup_py_path.write_text(setup_content, encoding='utf-8')
            log_callback(f"已生成 setup.py  在: {setup_py_path}")

            # 异步执行编译命令
            compile_success = await self._run_compile_command(
                output_dir, interpreter_file, log_callback
            )

            if not compile_success:
                return False

                # 清理临时文件
            await self._cleanup_files(output_dir, py_files, setup_py_path, log_callback)

            log_callback(f"\n[成功] 异步编译完成！结果在: {output_dir}")
            return True

        except Exception as e:
            log_callback(f"\n[严重错误] 异步编译失败: {e}")
            import traceback
            log_callback(traceback.format_exc())
            return False
        finally:
            # 确保清理任务
            await self._final_cleanup(output_dir, log_callback)

    async def _prepare_output_dir(self, output_dir, log_callback):
        """准备输出目录"""
        if os.path.exists(output_dir):
            try:
                log_callback(f"删除现有输出目录: {output_dir}")
                await asyncio.to_thread(shutil.rmtree, output_dir)
            except Exception as e:
                log_callback(f"删除输出目录失败: {e}")
                raise RuntimeError(f"无法删除输出目录: {output_dir}") from e

        log_callback(f"创建输出目录: {output_dir}")
        os.makedirs(output_dir, exist_ok=True)

    async def _copy_project_files(self, source_dir, output_dir, log_callback):
        """异步复制项目文件"""
        log_callback(f"开始异步复制目录: {source_dir} -> {output_dir}")

        # 在独立线程中执行复制操作
        await asyncio.to_thread(
            shutil.copytree,
            source_dir,
            output_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*exclude_dirs)
        )

        log_callback("目录复制完成")
        log_callback(f"源目录: {source_dir}")
        log_callback(f"输出目录: {output_dir}")
        log_callback("-" * 60)

    async def _find_py_files(self, output_dir, log_callback):
        """查找所有需要编译的Python文件"""
        log_callback("--- 步骤 1: 正在异步查找 .py 文件 ---")

        output_path = Path(output_dir)
        py_files = []

        # 使用线程池遍历目录
        with ThreadPoolExecutor() as executor:
            future = executor.submit(
                lambda: [p for p in output_path.rglob("*.py")
                         if p.name != "__init__.py" and
                         ".venv" not in p.parts and
                         p.name != "main.py"]
            )
            py_files = await asyncio.wrap_future(future)

        log_callback(f"找到 {len(py_files)} 个需要编译的.py 文件")
        return py_files

    async def _create_extensions(self, output_dir, py_files, log_callback):
        """创建扩展模块列表"""
        log_callback("--- 步骤 2: 创建扩展模块 ---")

        output_path = Path(output_dir)
        extensions = []

        # 并行处理每个文件
        futures = []
        for py_file in py_files:
            futures.append(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    self._create_extension,
                    output_path, py_file
                )
            )

        # 收集所有扩展
        for future in asyncio.as_completed(futures):
            extension = await future
            extensions.append(extension)

        log_callback(f"已创建 {len(extensions)} 个扩展模块")
        return extensions

    def _create_extension(self, output_path, py_file):
        """为单个文件创建扩展（在独立线程中执行）"""
        relative_path = py_file.relative_to(output_path)
        module_name = ".".join(relative_path.with_suffix("").parts)
        return f'Extension("{module_name}", [r"{relative_path}"])'

    async def _run_compile_command(self, output_dir, interpreter_file, log_callback):
        """异步执行编译命令"""
        log_callback("\n--- 步骤 3: 异步编译开始 ---")
        log_callback(f"使用解释器: {interpreter_file}")
        log_callback(f"最大工作线程数: {self.max_workers}")

        cmd = [
            interpreter_file,
            "setup.py",
            "build_ext",
            "--build-lib",
            output_dir,
            "--parallel",
            str(self.max_workers)
        ]
        log_callback(f"编译命令: {' '.join(cmd)}")

        # 创建异步子进程
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=output_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        # 实时读取输出流
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            log_callback(line.decode(errors='replace').strip())

            # 等待进程完成
        await process.wait()

        if process.returncode != 0:
            log_callback(f"\n[错误] 编译失败，返回码: {process.returncode}")
            return False

        log_callback("\n[成功] 异步编译完成")
        return True

    async def _cleanup_files(self, output_dir, py_files, setup_py_path, log_callback):
        """清理临时文件"""
        log_callback("\n--- 步骤 4: 清理临时文件 ---")

        # 并行删除.py源文件
        py_delete_tasks = [
            asyncio.create_task(self._delete_file(py_file, log_callback))
            for py_file in py_files
        ]
        await asyncio.gather(*py_delete_tasks)
        log_callback(f"  - 已删除 {len(py_files)} 个.py源文件")

        # 删除setup.py
        if setup_py_path.exists():
            await asyncio.to_thread(setup_py_path.unlink)
            log_callback("  - 已删除 setup.py")

            # 删除build目录
        build_dir = Path(output_dir) / "build"
        if build_dir.exists():
            await asyncio.to_thread(shutil.rmtree, build_dir)
            log_callback("  - 已删除 build 目录")

        # 删除所有.c文件
        c_files = list(Path(output_dir).rglob("*.c"))
        c_delete_tasks = [
            asyncio.create_task(self._delete_file(c_file, log_callback))
            for c_file in c_files
        ]
        await asyncio.gather(*c_delete_tasks)

        if c_files:
            log_callback(f"  - 已删除 {len(c_files)} 个临时.c文件")

    async def _delete_file(self, file_path, log_callback):
        """异步删除单个文件"""
        try:
            await asyncio.to_thread(os.remove, file_path)
        except Exception as e:
            log_callback(f"删除文件失败 {file_path}: {e}")

    async def _final_cleanup(self, output_dir, log_callback):
        """最终清理"""
        # 检查并删除空目录
        log_callback("\n执行最终清理...")
        for root, dirs, files in os.walk(output_dir, topdown=False):
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                if not os.listdir(dir_path):
                    try:
                        os.rmdir(dir_path)
                        log_callback(f"  - 删除空目录: {dir_path}")
                    except OSError:
                        pass
        log_callback("清理完成")


async def async_compile_example(source_dir, log_callback, interpreter_file):
    """异步编译示例用法"""
    compiler = AsyncCompiler()

    # # 自定义日志回调函数
    # def log_callback(message):
    #     print(f"[LOG] {message}")

    # 执行异步编译
    await compiler.compile_project_async(
        source_dir=source_dir,
        log_callback=log_callback,
        interpreter_file=interpreter_file
    )


def main(source_dir, log_callback, interpreter_file):
    """同步编译示例用法"""
    asyncio.run(async_compile_example(source_dir, log_callback, interpreter_file))


if __name__ == '__main__':
    # 运行异步编译示例
    asyncio.run(async_compile_example())
