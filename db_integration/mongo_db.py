from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError
from bson import ObjectId
import logging
from typing import Dict, List, Optional, Generator, Union, Any


class MongoDB:

    def __init__(self, db_name: str, collection_name: str, host: str = 'localhost', port: int = 27017):
        """
        初始化数据库连接
        :param db_name: 数据库名称
        :param collection_name: 集合名称（类似关系型数据库的表）
        :param host: 数据库主机地址，默认localhost
        :param port: 数据库端口，默认27017
        """
        # 创建MongoDB连接客户端
        self.client = MongoClient(host=host, port=port)

        # 获取指定数据库和集合
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

        # 配置日志
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

    def insert_one(self, document: Dict) -> Optional[ObjectId]:
        """
        插入单个文档
        :param document: 要插入的文档数据（字典格式）
        :return: 成功返回插入文档的ObjectId，失败返回None
        """
        try:
            result = self.collection.insert_one(document)
            # self.logger.info(f"成功插入文档，ID: {result.inserted_id}")
            return result.inserted_id
        except PyMongoError as e:
            self.logger.error(f"插入文档失败: {str(e)}")
            return None

    def insert_many(self, documents: List[Dict]) -> Optional[List[ObjectId]]:
        """
        批量插入多个文档
        :param documents: 文档列表
        :return: 成功返回插入ID列表，失败返回None
        """
        try:
            result = self.collection.insert_many(documents)
            self.logger.info(f"成功插入{len(result.inserted_ids)}个文档")
            return result.inserted_ids
        except PyMongoError as e:
            self.logger.error(f"批量插入失败: {str(e)}")
            return None

    def get_last_document(self) -> Optional[Dict]:
        """获取最后插入的完整文档（排除 _id）"""
        try:
            doc = self.collection.find_one(
                filter={},
                sort=[('_id', -1)],
                projection={'_id': False}
            )
            return doc if doc else None
        except PyMongoError as e:
            self.logger.error(f"查询失败: {str(e)}")
            return None

    def get_last_field(self, field: str = "itemName") -> Optional[Any]:
        """获取指定字段的最后插入值（基于时间戳排序）
        
        Args:
            field: 要获取的字段名，默认为 'itemName'
            
        Returns:
            字段值或 None（查询失败时）
        """
        try:
            # 按时间戳降序排序获取最新文档
            doc = self.collection.find_one(
                filter={},
                sort=[('t', DESCENDING),
                      ('_id', DESCENDING)],

                projection={field: True, '_id': False, 't': True}  # 包含时间戳用于验证
            )

            # 调试日志
            self.logger.debug(f"最新文档时间戳: {doc.get('t') if doc else '无记录'}")

            return doc.get(field) if doc else None
        except PyMongoError as e:
            self.logger.error(f"查询失败: {str(e)}", exc_info=True)
            return None

    def find(self,
             filter: Optional[Dict] = None,
             projection: Optional[Dict] = None,
             limit: int = 0) -> Generator[Dict, None, None]:
        """
        查询多个文档
        :param filter: 查询条件字典，例如 {"age": {"$gt": 18}}
        :param projection: 指定返回字段，例如 {"_id": 0, "name": 1}
        :param limit: 返回结果数量限制，0表示不限制
        :return: 生成器，迭代返回查询结果
        """
        try:
            # 执行查询，获取游标对象
            cursor = self.collection.find(
                filter=filter or {},  # 默认空查询条件
                projection=projection,
                limit=limit
            )

            # 使用生成器逐条返回结果，节省内存
            for doc in cursor:
                # 将ObjectId转换为字符串以便于处理
                doc['_id'] = str(doc['_id'])
                yield doc

        except PyMongoError as e:
            self.logger.error(f"查询失败: {str(e)}")
            yield from ()  # 返回空生成器

    def find_one(self, filter: Optional[Dict] = None) -> Optional[Dict]:
        """
        查询单个文档
        :param filter: 查询条件字典
        :return: 匹配的第一个文档，无匹配时返回None
        """
        try:
            doc = self.collection.find_one(filter or {})
            if doc:
                doc['_id'] = str(doc['_id'])  # 转换ObjectId为字符串
            return doc
        except PyMongoError as e:
            self.logger.error(f"查询失败: {str(e)}")
            return None

    def close(self) -> None:
        """关闭数据库连接"""
        try:
            self.client.close()
            # self.logger.info("数据库连接已关闭")
        except Exception as e:
            self.logger.error(f"关闭连接失败: {str(e)}")

    def __enter__(self):
        """实现上下文管理器协议，支持with语句"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时自动关闭连接"""
        self.close()


# 使用示例
if __name__ == "__main__":
    # 初始化日志配置
    logging.basicConfig(level=logging.INFO)

    # 示例：插入和查询数据
    with MongoDB(db_name="test_db", collection_name="510923657157") as db:
        # 插入单条文档
        # user_id = db.insert_one({
        #     "name": "张三",
        #     "age": 25,
        #     "email": "zhangsan@example.com"
        # })

        # # 批量插入文档
        # db.insert_many([
        #     {"name": "李四", "age": 30},
        #     {"name": "王五", "age": 28}
        # ])
        # db.insert_one({
        #     "78888": "黄某人",
        #     "666": 22,
        #     "33333": "666666@example.com"
        # })

        # # 查询年龄大于25岁的用户
        # print("年龄大于25岁的用户：")
        # for user in db.find(filter={"age": {"$gt": 25}}):
        #     print(user)

        # # 查询单个文档
        # print("\n查询单个用户：")
        # user = db.find_one({"name": "张三"})
        # print(user)

        # # 查询最后一个插入的一个用户
        # print("\n查询最后一个插入的一个用户：")
        # print(db.get_last_inserted())
        ms = db.get_last_field()
        print(ms)
