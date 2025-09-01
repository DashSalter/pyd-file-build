from Utils.configbase import Section, ConfigMeta


class Config(metaclass=ConfigMeta):
    """注册配置项"""
    program_path = Section('ProgramPath')
    project_path = program_path.add(option='project_path', default_value='')
    interpreter_path = program_path.add(option='interpreter_path', default_value=[])
    interpreter_index = program_path.add(option='interpreter_index', default_value=0)

    output_path = program_path.add(option='output_path', default_value='')

    exclude_dirs = program_path.add(option='exclude_dirs', default_value=[".venv", ".git", "__pycache__", ".idea"])
    exclude_files = program_path.add(
        option='exclude_files',
        default_value=[".gitignore", "requirements.txt", "cookies.json"]
    )

    is_remove_exist_output_dir = program_path.add(option='is_remove_exist_output_dir', default_value=False)

    # 默认是否压缩
    default_is_compress = program_path.add(option='default_is_compress', default_value=False)

    theme = program_path.add(option='theme', default_value=True)
