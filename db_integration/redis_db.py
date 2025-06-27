"""
两个功能：
往redis中存数据
从redis中取数据
"""

import redis
from typing import Optional, Any, Union, List
import logging
import json
from urllib.parse import quote
from config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_PASSWORD

# 配置日志记录
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RedisDB")


class RedisDB:
    """Redis数据库操作封装类"""

    """实现live_id跟线程thread_id的映射关系==>可通过live_id获取对应的thread_id==>逆操作：可通过thread_id获取对应的live_id
        实现live_ids集合，程序将通过live_ids集合对直播间进行监控
    实现添加逻辑，全部删除与单个删除逻辑，查询逻辑"""

    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        db: int = REDIS_DB,
        password: Optional[str] = REDIS_PASSWORD,
        socket_timeout: int = 5000,
        max_connections: int = 10,
    ):
        """
        初始化Redis连接
        :param host: Redis服务器地址
        :param port: Redis端口
        :param db: 数据库编号
        :param password: 认证密码
        :param socket_timeout: 超时时间（秒）
        :param max_connections: 连接池最大连接数
        """
        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            password=password if password else None,
            socket_timeout=socket_timeout,
            max_connections=max_connections,
            decode_responses=True,
        )

        self.conn = redis.Redis(connection_pool=self.pool)
        # 测试是否连接成功
        self._test_connection()

    def _test_connection(self) -> None:
        """测试连接是否成功"""
        try:
            if not self.conn.ping():
                raise redis.ConnectionError("Redis服务器无响应")
            logger.info("成功连接到Redis服务器")
        except redis.RedisError as e:
            logger.error(f"连接Redis失败: {str(e)}")
            raise

    def add_liveID(self, live_id: str) -> bool:
        """
        添加直播间ID到Redis列表（自动去重）
        :param live_id: 直播间ID
        :return: 是否添加成功（True=新添加，False=已存在/添加失败）
        """
        try:
            with self.conn.pipeline() as pipe:
                # 使用集合实现高效去重
                key = "live_ids"

                # 1. 检查是否存在
                # print('555555')
                exists = pipe.sismember(key, live_id).execute()[0]
                if exists:
                    logger.debug(f"直播间 {live_id} 已存在，跳过添加")
                    return False

                # 2. 原子性添加操作
                pipe.multi()
                pipe.sadd(key, live_id)  # 使用集合存储保证唯一性
                # pipe.expire(key,84600)  # 设置24小时过期时间
                results = pipe.execute()

                # 检查添加结果（SADD返回1表示新增，0表示已存在）
                return results[0] == 1
        except redis.RedisError as e:
            logger.error(f"添加直播间ID失败: {str(e)}")
            return False

    def delete_all_liveIDs(self) -> int:
        """
        清空直播间ID集合
        :return: 删除的元素数量（0表示集合不存在）
        """
        try:
            deleted = self.conn.delete("live_ids")

            logger.debug(f"已清空直播间ID集合，删除元素数: {deleted}")
            return deleted
        except redis.RedisError as e:
            logger.error(f"清空操作失败: {str(e)}")
            return 0

    def delete_liveID(self, live_id: str) -> bool:
        """
        单个删除直播间ID
        :param live_id: 直播间ID
        :return: 是否删除成功（True=删除成功，False=删除失败）
        """
        try:
            deleted = self.conn.srem("live_ids", live_id)

            logger.debug(f"已删除直播间ID: {live_id}, 删除元素数: {deleted}")
            return deleted > 0
        except redis.RedisError as e:
            logger.error(f"删除失败: {str(e)}")
            return False

    def get_live_ids(self) -> List[str]:
        """
        从Redis列表中获取所有直播间ID
        :return: 直播间ID列表
        """
        try:
            return self.conn.smembers("live_ids")
        except redis.RedisError as e:
            logger.error(f"查询失败: {str(e)}")
            return []

    # 在 RedisDB 类中添加以下方法

    def get_cookie(self, user_id: str, service: str) -> Optional[dict]:
        """
        获取指定用户的 Cookie 信息
        :param user_id: 用户ID
        :param service: 服务名称（如 'taobao'）
        :return: Cookie 字典（存在时）/ None（不存在或出错时）
        """
        try:
            key = f"user:{user_id}:cookies"
            cookie_data = self.conn.hget(key, service)

            if not cookie_data:
                logger.debug(f"未找到用户 {user_id} 的 {service} Cookie")
                return None

            # 反序列化 JSON 数据
            try:
                return json.loads(cookie_data)
            except json.JSONDecodeError as e:
                logger.error(f"Cookie 数据解析失败: {str(e)}")
                return None

        except redis.RedisError as e:
            logger.error(f"获取 Cookie 失败: {str(e)}")
            return None

    def update_cookie(self, user_id: str, service: str, cookie: dict) -> bool:
        """
        更新/存储用户 Cookie（自动编码处理）
        """
        try:
            # 核心修改：对cookie值进行编码预处理
            # encoded_cookie = {
            #     k: quote(str(v), safe='')  # 关键编码操作
            #     for k, v in cookie.items()
            # }

            key = f"user:{user_id}:cookies"

            with self.conn.pipeline() as pipe:
                pipe.multi()
                pipe.hset(key, service, json.dumps(cookie))  # 存储编码后的值
                # pipe.expire(key, expire)
                pipe.execute()

            logger.info(f"成功更新用户 {user_id} 的 {service} Cookie（已编码）")
            return True

        except Exception as e:
            logger.error(f"Cookie存储失败: {str(e)}")
            return False

    # 以下部分为可逆映射关系操作
    def add_room_mapping(self, live_id: str, thread_id: str) -> bool:
        """添加双向映射关系到集合"""
        try:
            with self.conn.pipeline() as pipe:
                # 使用事务保证原子性
                pipe.multi()
                pipe.sadd("live_map", f"{live_id}:{thread_id}")
                pipe.sadd("thread_map", f"{thread_id}:{live_id}")
                pipe.execute()
                logger.info(f"成功添加映射: {live_id} <-> {thread_id}")
                return True
        except redis.RedisError as e:
            logger.error(f"添加映射失败: {str(e)}")
            return False

    def get_all_mappings(self, format: str = "dict") -> Union[dict, list, tuple]:
        """
        获取所有直播间与线程的映射关系
        :param format: 返回格式
            - 'dict' : 返回正向字典 {live_id: thread_id}
            - 'list' : 返回元组列表 [(live_id, thread_id)]
            - 'both' : 返回包含两个字典的元组 ({live_id: thread_id}, {thread_id: live_id})
        :return: 根据format参数返回不同格式的数据
        """
        try:
            # 获取所有映射关系
            live_mappings = self.conn.smembers("live_map")
            thread_mappings = self.conn.smembers("thread_map")

            # 解析映射关系
            live_to_thread = {}
            thread_to_live = {}
            invalid_count = 0

            # 处理正向映射
            for mapping in live_mappings:
                parts = mapping.split(":", 1)
                if len(parts) == 2:
                    live_to_thread[parts[0]] = parts[1]
                else:
                    invalid_count += 1

            # 处理反向映射（用于验证数据一致性）
            for mapping in thread_mappings:
                parts = mapping.split(":", 1)
                if len(parts) == 2:
                    thread_to_live[parts[0]] = parts[1]

            # 记录无效数据
            if invalid_count > 0:
                logger.warning(f"发现 {invalid_count} 条无效的映射格式")

            # 根据格式参数返回结果
            if format == "dict":
                return live_to_thread
            elif format == "list":
                return list(live_to_thread.items())
            elif format == "both":
                return live_to_thread, thread_to_live
            else:
                logger.error("不支持的格式参数，使用默认字典格式")
                return live_to_thread

        except redis.RedisError as e:
            logger.error(f"查询映射关系失败: {str(e)}")
            return {} if format == "dict" else [] if format == "list" else ({}, {})

    def delete_room_mapping(self, live_id: str) -> bool:
        """通过直播间ID删除双向映射关系
        :param:live_id: 直播间ID
        :return: 是否删除成功（True=删除成功，False=删除失败）"""
        try:
            # 先获取对应的线程ID
            thread_id = self.get_thread_id(live_id)
            if not thread_id:
                logger.warning(f"未找到直播间 {live_id} 的映射关系")
                return False

            with self.conn.pipeline() as pipe:
                # 使用事务保证原子操作
                pipe.multi()
                # 删除正向映射
                pipe.srem("live_map", f"{live_id}:{thread_id}")

                # 删除反向映射
                pipe.srem("thread_map", f"{thread_id}:{live_id}")

                # 执行事务
                results = pipe.execute()

                # 检查删除结果（两个操作都应成功）
                if all(results):
                    logger.info(f"成功删除映射: {live_id} <-> {thread_id}")
                    return True
                return False
        except redis.RedisError as e:
            logger.error(f"删除映射失败: {str(e)}")
            return False

    def flush_all_str(self, confirm: bool = False) -> int:
        """
        安全删除所有live_map和thread_map字符串类型数据（危险操作）

        :param confirm: 必须显式确认为True才会执行
        :return: 删除的哈希数量（-1表示操作取消或失败）
        """
        card_num = self.conn.scard("live_map")
        thread_num = self.conn.scard("thread_map")
        print("直播间数量:", card_num)
        print("线程数量:", thread_num)
        print("清空中")
        self.conn.delete("live_map")
        self.conn.delete("thread_map")
        card_num = self.conn.scard("live_map")
        thread_num = self.conn.scard("thread_map")
        print("直播间数量:", card_num)
        print("线程数量:", thread_num)

    def get_thread_id(self, live_id: str) -> Optional[str]:
        """通过直播间ID获取线程ID"""
        try:
            # 从集合中获取所有live_id:thread_id映射
            mappings = self.conn.smembers("live_map")

            # 遍历查找匹配的直播间ID
            for mapping in mappings:
                parts = mapping.split(":")
                if len(parts) == 2 and parts[0] == live_id:
                    logger.debug(f"成功匹配直播间 {live_id} -> 线程 {parts[1]}")
                    return parts[1]

            logger.warning(f"未找到直播间 {live_id} 对应的线程ID")
            return None
        except redis.RedisError as e:
            logger.error(f"查询失败: {str(e)}")
            return None

    def close(self) -> None:
        """关闭连接池"""
        try:
            self.pool.disconnect()
            logger.info("Redis连接已关闭")
        except Exception as e:
            logger.error(f"关闭连接时出错: {str(e)}")

    def pipeline(self) -> redis.client.Pipeline:
        """获取管道对象"""
        return self.conn.pipeline()

    def connect(self):
        """获取客户端对象"""
        return self.conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 使用示例
if __name__ == "__main__":
    # 初始化配置（建议从环境变量读取）
    config = {
        "host": "localhost",
        "port": 6379,
        "db": 0,
        # "password": "duoan8855//S",
        "socket_timeout": 5,
        "max_connections": 20,
    }

    # try:
    with RedisDB(**config) as db:
        # 设置键值

        # 获取值
        # 获取所有的直播间列表
        print(db.get_live_ids())
        print(db.get_all_mappings())

        db.delete_all_liveIDs()
        db.flush_all_str(confirm=True)
        # db.add_liveID('509182694111')
        # db.add_liveID('509491417549')
        # db.add_liveID('509941561182')
        # db.add_room_mapping(live_id='509881602444',thread_id='我是线程')
        # op=db.get_thread_id('509881602444')
        # print(op)
        # print('添加成功')

        # time.sleep(60)

        # # 管道操作
        # pipe = db.pipeline()
        # pipe.set("test:pipe1", "value1")
        # pipe.set("test:pipe2", "value2")
        # pipe.execute()

        # # 批量删除
        # deleted = db.delete("test:string", "test:pipe1", "test:pipe2")
        # print(f"删除 {deleted} 个键")

    # cept Exception as e:
    #   logger.error(f"操作Redis时发生错误: {str(e)}")
