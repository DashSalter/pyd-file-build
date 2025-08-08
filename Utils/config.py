from Utils.configbase import Section, ConfigMeta


class Config(metaclass=ConfigMeta):
    """注册配置项"""
    program_path = Section('ProgramPath')
    project_path = program_path.add(option='host', default_value='')
    interpreter_path = program_path.add(option='user', default_value='')
    output_path = program_path.add(option='output', default_value='')

    exclude_dirs = program_path.add(option='exclude_dirs', default_value=[".venv", ".git", "__pycache__", ".idea"])
    is_remove_exist_output_dir = program_path.add(option='is_remove_exist_output_dir', default_value=False)

    # 默认是否压缩
    default_is_compress = program_path.add(option='default_is_compress', default_value=False)

    theme = program_path.add(option='theme', default_value=True)