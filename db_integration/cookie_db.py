import json
import os
import logging
from typing import Optional
from threading import Lock

# 配置日志记录
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CookieDB")

"""
将cookie数据存储到json文件中，提供Cookie管理接口。
"""


class CookieDB:
    def __init__(self, file_path: str = "cookies.json"):
        self.file_path = file_path
        self.lock = Lock()  # 用于线程安全的文件操作，类似于Redis的原子操作
        self._ensure_file_exists()
        self.user_key = "user:{user_id}:cookies"

    def create_cookie_file(self) -> None:
        """创建json文件"""
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        logger.info(f"创建新的Cookie存储文件: {self.file_path}")

    @staticmethod
    def valid_json_str(f) -> dict:
        """文件中的json内容是否符合json格式"""
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _ensure_file_exists(self) -> None:
        """确保JSON文件存在，如果不存在则创建"""
        if not os.path.exists(self.file_path):
            self.create_cookie_file()

    def _read_data(self) -> dict:
        """读取JSON文件中的所有数据"""
        try:
            with self.lock:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return self.valid_json_str(f)
        except json.JSONDecodeError as e:
            logger.error(f"读取Cookie文件失败，JSON解析错误: {str(e)}")
            return {}
        except Exception as e:
            logger.error(f"读取Cookie文件失败: {str(e)}")
            return {}

    def _write_data(self, data: dict) -> bool:
        """写入数据到JSON文件"""
        try:
            with self.lock:
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"写入Cookie文件失败: {str(e)}")
            return False

    def get_cookie(self, user_id: str, service: str) -> Optional[dict]:
        """
        获取指定用户的Cookie信息
        
        :param user_id: 用户ID
        :param service: 服务名称（如 'taobao'）
        :return: Cookie字典（存在时）/ None（不存在或出错时）
        """
        try:
            cookie_dict = self._read_data()
            user_key = self.user_key.format(user_id=user_id)
            if user_key not in cookie_dict or service not in cookie_dict[user_key]:
                logger.debug(f"未找到用户 {user_id} 的 {service} Cookie")
                return None
            return cookie_dict[user_key][service]
        except Exception as e:
            logger.error(f"获取Cookie失败: {str(e)}")
            return None

    def update_cookie(self, user_id: str, service: str, cookie: dict) -> bool:
        """
        更新/存储用户Cookie
        
        :param user_id: 用户ID
        :param service: 服务名称（如 'taobao'）
        :param cookie: Cookie数据字典
        :return: 是否更新成功
        """
        if not isinstance(cookie, dict):
            raise TypeError("Cookie数据必须为字典类型")

        try:
            cookie_dict = self._read_data()
            user_key = self.user_key.format(user_id=user_id)

            # 确保用户的cookie容器存在
            if user_key not in cookie_dict:
                cookie_dict[user_key] = {}

            # 更新cookie
            cookie_dict[user_key][service] = cookie

            # 写入文件
            if self._write_data(cookie_dict):
                logger.info(f"成功更新用户 {user_id} 的 {service} Cookie")
                return True
            return False

        except Exception as e:
            logger.error(f"Cookie存储失败: {str(e)}")
            return False


# 使用示例
if __name__ == "__main__":
    # 创建CookieDBJson实例
    cookie_db = CookieDB()

    # 示例cookie数据
    test_cookie = {
        "session_id": "test123",
        "user_token": "abc456",
        "expires": "2024-12-31"
    }

    # 存储cookie
    cookie_db.update_cookie("test_user", "taobao", test_cookie)

    # 读取cookie
    stored_cookie = cookie_db.get_cookie("test_user", "taobao")
    print("存储的Cookie:", stored_cookie)
