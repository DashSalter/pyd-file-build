"""配置项声明，使用 conf_base 持久化到程序目录下的 config.json。"""

from conf_base import ConfigBase, Section

from pyd_build.paths import CONFIG_PATH


class Config(ConfigBase):
    """注册配置项"""

    # 显式指定配置文件位置，避免配置文件随当前工作目录漂移
    _path = str(CONFIG_PATH)
    _auto_reload = True

    program_path = Section('ProgramPath')
    project_path = program_path.field(default='', option='project_path')
    interpreter_path = program_path.field(option='interpreter_path', default=[])
    interpreter_index = program_path.field(option='interpreter_index', default=0)
    output_path = program_path.field(option='output_path', default='')
    # 入口文件（如 main.py）：填写后只拷贝入口及其依赖的本地模块，留空则全量拷贝
    entry_file = program_path.field(option='entry_file', default='')
    # 入口模式下额外要拷贝的文件/目录（相对源目录，支持通配符），例如 ["data", "assets/*.png"]
    extra_copy = program_path.field(option='extra_copy', default=[])
    # 过滤目录
    exclude_dirs = program_path.field(
        option='exclude_dirs',
        default=[".venv", ".git", "__pycache__", ".idea"]
    )
    # 过滤文件
    exclude_files = program_path.field(
        option='exclude_files',
        default=[".gitignore", "requirements.txt", "cookies.json"]
    )

    is_remove_exist_output_dir = program_path.field(option='is_remove_exist_output_dir', default=False)

    # 默认是否压缩
    default_is_compress = program_path.field(option='default_is_compress', default=False)

    theme = program_path.field(option='theme', default=True)
