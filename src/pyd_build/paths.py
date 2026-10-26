"""程序运行时路径解析。

开发态（源码运行或 editable 安装）以项目根目录为程序目录；
打包成单文件 exe 后以 exe 所在目录为程序目录，保证配置跟着程序走。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _resolve_app_dir() -> Path:
    """返回程序目录（配置与缓存都放在这里）。"""
    if getattr(sys, "frozen", False):  # PyInstaller 等打包器会设置该标记
        return Path(sys.executable).resolve().parent
    # src/pyd_build/paths.py -> src/pyd_build -> src -> 项目根
    return Path(__file__).resolve().parents[2]


APP_DIR: Path = _resolve_app_dir()
CONFIG_PATH: Path = APP_DIR / "config.json"
