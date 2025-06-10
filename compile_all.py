import os
import sys
import subprocess
import shutil
import argparse
from textwrap import dedent

def compile_directory(target_dir: str, output_dir: str = None):
    """
    编译指定目录下的所有 .py 文件为 .pyd 文件，支持自定义输出目录
    
    参数:
        target_dir (str): 要编译的源代码目录
        output_dir (str): 编译结果输出目录（可选）
    """
    print("-" * 60)
    print(f"开始处理目录: {target_dir}")
    
    # 验证目标目录
    if not os.path.isdir(target_dir):
        print(f"错误: 目录 '{target_dir}' 不存在。")
        return
    
    # 设置输出目录（默认为目标目录下的 DataCenter）
    output_dir = output_dir or os.path.join(target_dir, "DataCenter")
    print(f"输出目录: {output_dir}")
    
    # 确保输出目录存在
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"已创建输出目录: {output_dir}")
    
    # 步骤 1: 创建所有必要的子目录结构
    print("\n创建目标目录结构...")
    for root, dirs, files in os.walk(target_dir):
        # 计算相对路径
        rel_path = os.path.relpath(root, target_dir)
        # 构建目标目录路径
        dest_dir = os.path.join(output_dir, rel_path)
        
        # 确保目录存在
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
            print(f"  - 创建: {rel_path}")
    
    # 步骤 2: 查找所有需要编译的 .py 文件
    py_files_to_compile = []
    for root, _, files in os.walk(target_dir):
        for file in files:
            if file.endswith(".py") and file != "__init__.py":
                full_path = os.path.join(root, file)
                # 计算文件相对于目标根目录的相对路径
                relative_path = os.path.relpath(full_path, target_dir)
                py_files_to_compile.append(relative_path)
    
    if not py_files_to_compile:
        print("未找到需要编译的 .py 文件。")
        return
    
    print("\n找到以下文件进行编译:")
    for py_file in py_files_to_compile:
        print(f"  - {py_file}")
    
    # 步骤 3: 动态创建 setup.py 文件
    setup_py_path = os.path.join(target_dir, "setup.py")
    
    # 使用绝对路径确保正确性
    abs_output_dir = os.path.abspath(output_dir)
    
    # 优化后的 setup.py 模板
    setup_content = dedent(f"""
        from setuptools import setup
        from Cython.Build import cythonize
        
        setup(
            name="DataCenter",
            ext_modules=cythonize(
                {py_files_to_compile},
                compiler_directives={{"language_level": "3"}}
            ),
            options={{
                "build_ext": {{
                    "inplace": True,
                    "build_lib": r"{abs_output_dir}"
                }}
            }}
        )
    """)
    
    # 定义清理清单
    cleanup_items = {
        "files": [setup_py_path],
        "dirs": [os.path.join(target_dir, "build")],
    }
    
    # 添加.c文件到清理列表
    for py_file in py_files_to_compile:
        c_file = os.path.join(target_dir, py_file.replace(".py", ".c"))
        cleanup_items["files"].append(c_file)
    
    try:
        # 步骤 4: 写入临时的 setup.py 文件
        print(f"\n在 {target_dir} 中创建临时的 setup.py 文件...")
        with open(setup_py_path, "w", encoding="utf-8") as f:
            f.write(setup_content)
        
        # 步骤 5: 执行编译命令
        python_executable = sys.executable
        print(f"\n使用解释器 '{python_executable}' 开始编译...")
        
        try:
            process = subprocess.run(
                [python_executable, "setup.py", "build_ext", "--inplace"],
                cwd=target_dir,
                capture_output=True,
                text=True,
                encoding="GBK",
                check=True  # 如果返回非零状态码则抛出异常
            )
            print("\n[成功] 编译完成！")
            print("--- STDOUT ---")
            print(process.stdout)
            
        except subprocess.CalledProcessError as e:
            print("\n[错误] 编译失败。查看下面的错误信息:")
            print("--- STDOUT ---")
            print(e.stdout)
            print("--- STDERR ---")
            print(e.stderr)
            print(f"返回码: {e.returncode}")
            
            # 保留 setup.py 用于调试
            print("\n[警告] 保留 setup.py 用于调试")
            if setup_py_path in cleanup_items["files"]:
                cleanup_items["files"].remove(setup_py_path)
            
            return False
    
    except Exception as e:
        print(f"\n[严重错误] 编译过程中发生意外错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # 步骤 6: 清理临时文件
        print("\n开始清理临时文件...")
        for file_path in cleanup_items["files"]:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"  - 已删除文件: {os.path.basename(file_path)}")
                except Exception as e:
                    print(f"  - 删除文件失败 {file_path}: {str(e)}")
        
        for dir_path in cleanup_items["dirs"]:
            if os.path.exists(dir_path):
                try:
                    shutil.rmtree(dir_path)
                    print(f"  - 已删除目录: {os.path.basename(dir_path)}")
                except Exception as e:
                    print(f"  - 删除目录失败 {dir_path}: {str(e)}")
        
        print("\n清理完成。")
        print("-" * 60)
    
    return True


if __name__ == "__main__":
    # 使用 argparse 处理命令行参数
    parser = argparse.ArgumentParser(description='编译Python项目为.pyd文件')
    parser.add_argument('directory', help='D:\下载\Python\DataCenter\DataCenter')   #需要编译成pyd的文件
    parser.add_argument('-o', '--output', help='D:\下载\Python\DataCenter\加密机制\DataCenter', default=None)  #编译后的文件存放位置
    
    args = parser.parse_args()
    
    # 处理路径中的波浪号(~)
    target_dir = os.path.expanduser(args.directory)
    output_dir = os.path.expanduser(args.output) if args.output else None
    
    # 确保路径是绝对路径
    target_dir = os.path.abspath(target_dir)
    if output_dir:
        output_dir = os.path.abspath(output_dir)
    
    compile_directory(target_dir, output_dir)
