import json
import os
from functools import partial
from typing import Any


class SingletonMeta(type):
    def __call__(cls, *args, **kwargs):
        if not hasattr(cls, '_instance'):
            cls._instance = super().__call__(*args, **kwargs)
        return cls._instance


class JsonConfig(metaclass=SingletonMeta):
    def __init__(self, json_file='settings.json', encoding='utf-8', is_allowed_modify_json_files=False):
        self.json_file = json_file
        self.encoding = encoding
        self._config = {}
        self.load_config()
        self.is_allowed_modify_json_files = is_allowed_modify_json_files  # 是否会修改配置文件，并且时刻使用最新的配置数据

    def load_config(self):
        if os.path.exists(self.json_file):
            with open(self.json_file, 'r', encoding=self.encoding) as f:
                self._config = json.load(f)
        else:
            self.write_config()

    def write_config(self):
        with open(self.json_file, 'w+', encoding=self.encoding) as f:
            json.dump(self._config, f, ensure_ascii=False, indent=4)

    def config_set(self, section, option, value, default_value):
        if section not in self._config:
            self._config[section] = {}
        if option not in self._config[section]:
            self._config[section][option] = default_value
        else:
            self._config[section][option] = value

        self.write_config()

    def config_get(self, section, option, default_value):
        if self.is_allowed_modify_json_files:
            # 每次请求都需要重新从配置文件中读取最新的数据，对于数据的读取频率会很慢，大多是一次性的
            self.load_config()

        if section not in self._config:
            self._config[section] = {}
        if option not in self._config[section]:
            self._config[section][option] = default_value
            self.write_config()
            return default_value
        else:
            return self._config[section][option]

    def config_del(self, section, option):
        if section not in self._config:
            return
        else:
            del self._config[section][option]
            self.write_config()


# 描述器 用于将配置文件中的选项映射为类的属性，并支持对这些属性的操作
class ConfigProperty:
    def __init__(self, section, option, default_value):
        """属性转换实际操作"""
        self.config_file = JsonConfig(is_allowed_modify_json_files=True)

        self.section = section
        self.option = option
        self.default_value = default_value

    def __get__(self, obj, obj_type=None):
        return self.ProxyInterception(self)

    def __set__(self, obj, value):
        if isinstance(value, self.ProxyInterception):
            value = value.value
        self.config_file.config_set(self.section, self.option, value, self.default_value)

    def __delete__(self, obj):
        self.config_file.config_del(self.section, self.option)

    class ProxyInterception:
        def __init__(self, parent):
            """属性代理,拓展更多属性的操作"""
            self.parent = parent
            self._config_get = self.parent.config_file.config_get
            self._config_set = self.parent.config_file.config_set

        def __str__(self):
            return f"<class '{self.parent.section}:{self.parent.option}'>"

        def reset(self):
            """重置属性值"""
            self._config_set(
                self.parent.section,
                self.parent.option,
                self.parent.default_value,
                self.parent.default_value
            )
            return self.parent.default_value

        @property
        def default_value(self):
            return self.parent.default_value

        @default_value.setter
        def default_value(self, value):
            raise ValueError("default_value is readonly")

        @property
        def value(self):
            return self._config_get(self.parent.section, self.parent.option, self.parent.default_value)

        @value.setter
        def value(self, value):
            self._config_set(self.parent.section, self.parent.option, value, self.parent.default_value)


# 配置块 用于注册配置节点
class Section:
    def __init__(self, section):
        self.section = section
        self._section_name = None
        self.__config = partial(ConfigProperty, section=self.section)
        self._section_dict = {}

    def add(self, option, default_value: Any = ''):
        """添加子节点"""
        self._section_dict[option] = default_value
        return self.__config(option=option, default_value=default_value)

    def __str__(self):
        __result = []
        for k, v in self._section_dict.items():
            __result.append(f"{k}={v}")
        return f"<{self.__class__.__name__}:{self.section} [{', '.join(__result)}]>"

    def get_sections(self):
        return list(self._section_dict.keys())

    def __getattr__(self, item):
        return self.__getitem__(item)

    def __getitem__(self, item):
        return self._section_dict.get(item)

    def __len__(self):
        return len(self._section_dict)


# 元类:配置类
# 用于将类对象直接返回为实例对象，方便调用配置节点
class ConfigMeta(type):
    property_list = []

    def __new__(cls, name, bases, attrs):
        __class = super().__new__(cls, name, bases, attrs)
        __instance = __class()  # 将 Config 类变成了类的唯一实例，实现天然单例模式

        for attr_name, attr_value in attrs.items():
            if isinstance(attr_value, ConfigProperty):  # 初始化注册节点
                cls.property_list.append(attr_name)
                setattr(__instance, attr_name, getattr(__instance, attr_name))
            elif isinstance(attr_value, Section):
                attr_value._section_name = attr_name  # 注入配置块属性名

        setattr(__instance, 'property_list', cls.property_list)
        return __instance
