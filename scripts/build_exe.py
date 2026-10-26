"""用 PyInstaller 把项目打包成单文件 exe。

用法：
    .\.venv\Scripts\python.exe -m pip install pyinstaller
    .\.venv\Scripts\python.exe scripts\build_exe.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "pyd-file-build"


def main() -> int:
    pyinstaller = Path(sys.executable).with_name("pyinstaller.exe")
    if not pyinstaller.exists():
        print("[错误] 未找到 PyInstaller，请先安装：python -m pip install pyinstaller")
        return 1

    cmd = [
        str(pyinstaller),
        "--noconfirm",
        "--clean",
        "--windowed",  # 不显示控制台窗口
        "--name",
        APP_NAME,
        "--icon",
        str(ROOT / "assets" / "icon.ico"),
        "--upx-dir",
        str(ROOT / "assets" / "upx"),
        "--paths",
        str(ROOT / "src"),
        str(ROOT / "main.py"),
    ]
    print("[打包命令]", subprocess.list2cmdline(cmd))

    result = subprocess.run(cmd, cwd=ROOT, check=False)
    if result.returncode != 0:
        print(f"[失败] PyInstaller 返回码 {result.returncode}")
        return result.returncode

    exe = ROOT / "dist" / f"{APP_NAME}.exe"
    if exe.exists():
        print(f"[完成] {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
