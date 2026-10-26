# pyd-file-build

把 Python 项目批量编译成 Cython `.pyd` 扩展的图形工具。

流程：复制源码目录 → 在输出目录里生成 `setup.py` → 调 Cython/setuptools 编译 → 清理 `.py`、`.c`、`build` → 可选打 zip。`setup.py` 只写在输出目录并随后删除，不会动源项目里的同名文件。

编译结果输出到选定目录；增量编译缓存写在输出目录内的 `pyd_build_cache.json`，其中记录所用解释器版本与原始项目路径。

界面上只需选一个入口文件：源目录与输出目录自动推导，只拷贝入口引用到的代码；运行时要用的其他文件在 `config.json` 的 `extra_copy` 里按需添加，见下文。

## 环境要求

- Windows + MSVC 生成工具（编译 `.pyd` 需要 C 编译器）
- Python 3.10+

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 运行

```powershell
.\.venv\Scripts\python.exe main.py
```

`main.py` 会把 `src` 加入 `sys.path`，不安装也能直接运行。

## 入口文件（只拷贝代码）

界面上只需要选一个「入口文件」（比如 `main.py`），源目录和输出目录都由它推导：

- 源目录 = 入口文件所在目录
- 输出目录 = 源目录同级、名字加 `_pyd` 的目录（如 `F:\proj` → `F:\proj_pyd`）

入口输入框可以手输或点「浏览...」选择，失焦或回车时自动补全成完整路径；下方只读地显示推导出的项目目录与输出目录。

工具从入口出发用 `ast` 静态解析 `import` / `from ... import ...`（含相对导入），**只把源目录内真实存在的代码模块**拷进输出目录，并补齐各级 `__init__.py`；没有被引用到的 `.py` 不会进入输出目录。标准库和第三方库在目标环境本来就有，不参与拷贝。

- 入口模式是白名单：除代码外**什么都不拷**，`exclude_dirs` / `exclude_files` 在全量模式下才生效。
- 数据、模板、图片等运行时要用的文件，在 `config.json` 的 `extra_copy` 里按需添加：相对源目录的路径，支持通配符，例如 `["data", "assets/*.png", "config.ini"]`。写了目录拷整个目录，写了文件只拷该文件（父目录自动保留）。
- 入口文件本身保留为 `.py` 启动脚本，不编译成 `.pyd`（与 `main.py` 的跳过规则一致）。
- 入口留空时沿用 `config.json` 里保存的源目录，做全量拷贝。
- `importlib.import_module()`、`__import__()` 这类动态导入无法静态追踪，日志会给出数量提示；需要时在入口里显式 `import` 一次即可。
- 输出目录里上次残留、本次不在代码范围与额外拷贝里的文件会被清掉，编译好的 `.pyd` 与缓存文件不受影响。

## 运行测试（验证编译结果）

编译完成后点「运行测试」，工具会用界面上选的解释器，在输出目录里以它作为工作目录运行入口文件，stdout / stderr 实时回显到日志框，结束时给出退出码：`0` 为通过，非 0 为失败。

- 测试跑的就是**编译后的输出目录**（只有 `.pyd` + 入口 `.py` + `extra_copy` 的文件），能直接暴露 `PyInit_xxx` 名字错、漏拷贝模块、运行时异常等问题。
- 想中途结束：再点一次按钮（此时显示「停止测试」），会杀掉测试进程。
- 入口是 GUI 程序时会真的弹出窗口，这是正常的——说明 pyd 能被正常导入并启动。
- 这个测试用的是本机已装的第三方包。要验证「目标机器没装某个包」的场景，用目标机器上同版本的解释器直接跑：

  ```powershell
  cd F:\proj_pyd
  D:\Python312\python.exe main.py
  ```

## 打包成 exe

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe scripts\build_exe.py
```

产物：`dist/pyd-file-build.exe`（窗口程序，带程序图标，UPX 压缩）。

## 目录结构

```
pyd-file-build/
├── main.py                  # 入口（打包入口）
├── requirements.txt         # 运行时依赖
├── src/
│   └── pyd_build/
│       ├── __init__.py      # 版本号
│       ├── __main__.py      # python -m pyd_build
│       ├── app.py           # 图形界面
│       ├── compiler.py      # 编译核心流程
│       ├── config.py        # 配置项声明
│       └── paths.py         # 运行时路径（程序目录 / 配置文件位置）
├── assets/
│   ├── icon.ico             # 程序图标
│   └── upx/                 # UPX 可执行文件压缩器（打包用）
├── scripts/
│   └── build_exe.py         # PyInstaller 打包脚本
├── tests/
│   └── test_compiler.py     # 单元测试
└── config.json              # 运行时生成，不入库
```

## 过滤规则

**入口模式**（填了入口文件）下拷贝是白名单：只拷贝入口依赖到的 `.py`、各级 `__init__.py`，以及 `extra_copy` 指定的文件/目录，`exclude_*` 不参与。

**全量模式**（入口留空）下按 `config.json` 里的 `exclude_dirs` / `exclude_files` 跳过目录和文件（都支持通配符），另外内置忽略版本控制与缓存类文件，不用手动配置：

- 目录：`.git`、`.hg`、`.svn`、`__pycache__`、`.venv`、`.idea`、`.vscode`、`.mypy_cache`、`.pytest_cache`
- 文件：`.gitignore`、`.gitattributes`、`.gitmodules`、`*.pyc`、`*.pyo`
- 超过 100MB 的文件

`config.json` 是本程序自己的运行时配置，全量模式下建议留在 `exclude_files` 里，避免被当成项目文件拷进输出目录。

## 运行时文件

| 路径 | 说明 |
| --- | --- |
| `config.json` | 界面配置，首次运行自动生成；与程序同目录 |
| `<输出目录>/pyd_build_cache.json` | 增量编译缓存，按文件 SHA256 判断是否重编；记录解释器版本与原始项目路径 |

`config.json` 不入库；缓存文件随编译产物留在输出目录里，是否保留由你决定。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
