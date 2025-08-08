from Utils.configbase import Section, ConfigMeta


class Config(metaclass=ConfigMeta):
    """注册配置项"""
    program_path = Section('ProgramPath')
    project_path = program_path.add(option='host', default_value='')
    interpreter_path = program_path.add(option='user', default_value='')
