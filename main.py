"""项目入口：``python main.py`` 启动图形界面，PyInstaller 也以此文件为打包入口。"""

import sys
from pathlib import Path

# 未安装包时也允许直接运行：python main.py
_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pyd_build.app import main  # noqa: E402

if __name__ == "__main__":
    main()
