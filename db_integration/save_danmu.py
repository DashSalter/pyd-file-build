import sys
from pathlib import Path

project_root = Path(__file__).parent  # 根据实际层级调整

sys.path.append(str(project_root))

import collections
import threading 
import json
import threading
import time
import os
import shutil 
from typing import Optional, Tuple,Optional,Dict,List,Any
from config import MAX_HISTORY














class DanmuStorageQ:
    """
    基于队列
    一个专用于在内存中存储直播间弹幕数据的类，使用 collections.deque。
    自动管理历史记录长度，新消息加入时，旧消息会自动被移除（如果队列已满）。
    为实时展示优化：消息立即添加到内存队列。
    注意：此实现是纯内存的，程序结束数据会丢失，除非额外实现持久化。
    """

    def __init__(self,
                 room_id: str):
        """
        初始化内存弹幕存储实例.

        Args:
            room_id: 直播间的唯一标识符 (主要用于日志和区分实例).
        """

        self.room_id = str(room_id)
        self.max_history = max(1, MAX_HISTORY) # 弹幕队列的最大长度
        
        # 初始化双端队列，设置最大长度
        self._messages_deque: collections.deque[Dict[str, Any]] = collections.deque(maxlen=self.max_history)
        
        # 锁用于保护对deque的访问
        self._lock = threading.RLock()


    @staticmethod
    def _sanitize_string(text: str) -> str:
        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception:
                return ""
        # 对于纯内存，如果字符串本身是合法的Python str，则无需编解码转换。
        # 但如果数据源可能产生非标准Unicode，保留这个清洗步骤是安全的。
        return text.encode('utf-8', 'replace').decode('utf-8', 'replace')


    def add_message(self, message_data: Dict[str, Any]):
        """
        将一条新的弹幕消息添加到内存队列中。
        如果队列已满，最早的消息会自动被移除。

        Args:
            message_data: 包含弹幕信息的字典对象.
        """
        with self._lock:
            # 清洗字符串值
            # sanitized_data = {k: self._sanitize_string(v) if isinstance(v, str) else v
            #                   for k, v in message_data.items()}
            
            self._messages_deque.append(message_data)
            # deque的maxlen参数会自动处理移除旧元素的操作
            # print(f"[{self.room_id}] 消息已添加，当前队列长度: {len(self._messages_deque)}") # Debug

    def get_messages(self, count: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取存储在内存队列中的弹幕消息。

        Args:
            count: 要获取的最新消息数量。如果为 None 或 0，则返回队列中的所有消息。

        Returns:
            一个包含弹幕数据字典的列表 (按加入顺序，最新的在末尾)。
        """
        with self._lock:
            # 将deque转换为列表以返回。deque本身支持迭代，但返回列表更通用。
            all_messages = list(self._messages_deque)
            
            if count is not None and count > 0:
                # 返回最新的 count 条消息
                return all_messages[-count:] 
            return all_messages # 返回所有消息

    def get_message_count(self) -> int:
        """返回当前队列中的消息数量。"""
        with self._lock:
            return len(self._messages_deque)

    def clear_messages(self):
        """清空队列中的所有消息。"""
        with self._lock:
            self._messages_deque.clear()
            # print(f"信息 [{self.room_id}]: 内存弹幕队列已清空。")

    def close(self):
        """
        关闭存储实例前的操作。对于纯内存deque，主要是释放引用。
        如果未来需要持久化，可以在这里将deque内容写入文件。
        """
        # print(f"信息 [{self.room_id}]: DanmuStorage (内存队列) 正在关闭...")
        # 对于纯内存deque，通常不需要特别的关闭操作，
        # Python的垃圾回收会自动处理。
        # 如果需要在这里做持久化，可以添加相关逻辑。
        # 例如: self._persist_to_file_on_close()
        self.clear_messages() # 或者选择在这里清空，取决于需求
        # print(f"信息 [{self.room_id}]: DanmuStorage (内存队列) 已关闭。")







class DanmuStorage:
    """
    一个专用于存储直播间弹幕数据的类，使用JSON Lines (NDJSON) 格式。
    为实时展示优化：每条消息都会立即追加到文件。
    历史截断是一个独立的操作，以避免阻塞实时写入。
    """

    def __init__(self,
                 room_id: str,
                 storage_base_dir: Path,
                 file_suffix: str = ".jsonl"):
        """
        初始化弹幕存储实例.

        Args:
            room_id: 直播间的唯一标识符.
            storage_base_dir: 存储文件的基础目录.
            max_history: 文件中要保留的最大历史弹幕条数.
            file_suffix: 数据文件的后缀名.
        """
        if not room_id:
            # 在实际应用中，你可能希望这里能从外部（如DouyinLive类）获取有效的room_id
            # 或者有一个更明确的错误处理机制
            print("警告: DanmuStorage 初始化时 room_id 为空或无效。") 
            self.room_id = "unknown_room" # 提供一个默认值以避免后续错误
        else:
            self.room_id = str(room_id)
            
        self.storage_base_dir = Path(storage_base_dir)
        self.max_history = max(1, MAX_HISTORY)
        self._file_suffix = file_suffix

        self.file_path = self.storage_base_dir / f"danmu_{self.room_id}{self._file_suffix}"
        self._io_lock = threading.RLock() # 可重入锁，保护所有文件操作

        try:
            self.storage_base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"错误: 创建存储目录 {self.storage_base_dir} 失败: {e}")
            raise

    @staticmethod
    def _sanitize_string(text: str) -> str:
        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception:
                return ""
        return text.encode('utf-8', 'replace').decode('utf-8')

    def add_message(self, message_data: Dict[str, Any]):
        """
        立即将一条新的弹幕消息追加到存储文件中。

        Args:
            message_data: 包含弹幕信息的字典对象.
        """
        with self._io_lock:
            sanitized_data = {k: self._sanitize_string(v) if isinstance(v, str) else v
                              for k, v in message_data.items()}
            try:
                # 确保目录存在 (虽然__init__做了，但防御性检查无妨)
                self.storage_base_dir.mkdir(parents=True, exist_ok=True)
                
                with open(self.file_path, 'a', encoding='utf-8') as f:
                    json.dump(sanitized_data, f, ensure_ascii=False)
                    f.write('\n')
            except Exception as e:
                print(f"错误 [{self.room_id}]: 追加消息到文件 {self.file_path} 失败: {type(e).__name__} - {e}")

    def _read_all_valid_messages_nolock(self) -> Tuple[List[Dict[str, Any]], bool, int]:
        """
        (在持有锁的情况下调用)
        读取文件中的所有有效JSON Lines消息。
        返回: (消息列表, 是否发现无效行, 读取的总行数)
        """
        valid_messages: List[Dict[str, Any]] = []
        invalid_lines_found = False
        lines_count = 0

        if not self.file_path.exists():
            return valid_messages, invalid_lines_found, lines_count

        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                for line_number, line in enumerate(f):
                    lines_count += 1
                    line = line.strip()
                    if not line:
                        invalid_lines_found = True
                        continue
                    try:
                        msg_data = json.loads(line)
                        valid_messages.append(msg_data)
                    except json.JSONDecodeError:
                        print(f"警告 [{self.room_id}]: 读取时解析文件 {self.file_path} 第 {line_number + 1} 行失败。")
                        invalid_lines_found = True
        except Exception as e:
            print(f"错误 [{self.room_id}]: 读取消息文件 {self.file_path} 时发生错误: {e}")
            # 如果读取都失败，可能文件已严重损坏，返回空列表并标记需要清理
            invalid_lines_found = True 
        return valid_messages, invalid_lines_found, lines_count

    def truncate_history(self, force_rewrite: bool = False):
        """
        执行历史截断操作。
        读取JSON Lines文件，保留最新的max_history条记录，并重写文件。
        仅当消息数量超过max_history或文件中存在解析错误时，或force_rewrite为True时才执行重写。

        Args:
            force_rewrite: 是否强制重写文件，即使消息数量未超限且无无效行。
                           (例如，在程序退出时，可以强制执行一次以确保文件规整)
        """
        with self._io_lock:
            # print(f"信息 [{self.room_id}]: 开始历史截断 (强制: {force_rewrite})...") # Debug
            valid_messages, invalid_lines_found, lines_count = self._read_all_valid_messages_nolock()

            # print(f"[{self.room_id}] 截断检查: 文件行数 {lines_count}, 有效消息 {len(valid_messages)}") # Debug

            if not force_rewrite and len(valid_messages) <= self.max_history and not invalid_lines_found:
                # print(f"信息 [{self.room_id}]: 无需截断或清理。") # Debug
                return

            history_to_keep = valid_messages[-self.max_history:]
            # if force_rewrite or invalid_lines_found or len(valid_messages) > self.max_history:
            #     print(f"信息 [{self.room_id}]: 将保留最新的 {len(history_to_keep)} 条消息进行重写。") # Debug
            # else: # 这行理论上不会执行，因为上面的if会捕获
            #     return


            temp_file_name = f"{self.file_path.stem}.truncating.{os.getpid()}.{time.time_ns()}{self.file_path.suffix}"
            temp_file_path = self.file_path.with_name(temp_file_name)
            
            max_retries = 3
            retry_delay = 0.2
            write_op_done = False

            for attempt in range(max_retries):
                try:
                    with open(temp_file_path, 'w', encoding='utf-8') as f_temp:
                        for msg_item in history_to_keep:
                            json.dump(msg_item, f_temp, ensure_ascii=False)
                            f_temp.write('\n')
                    
                    shutil.move(str(temp_file_path), str(self.file_path))
                    # print(f"信息 [{self.room_id}]: 历史截断并重写文件 {self.file_path} 成功。") # Debug
                    write_op_done = True
                    break
                except (PermissionError, OSError) as e_move:
                    if attempt < max_retries - 1:
                        # print(f"警告 [{self.room_id}]: 截断重写尝试 {attempt+1}/{max_retries} 失败: {type(e_move).__name__}. 等待 {retry_delay}s...") # Debug
                        time.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 1.0)
                    else:
                        print(f"错误 [{self.room_id}]: 截断重写在 {max_retries} 次尝试后仍然失败: {type(e_move).__name__} - {e_move}")
                        break
                except Exception as e_rewrite:
                    print(f"错误 [{self.room_id}]: 截断重写时发生意外错误: {type(e_rewrite).__name__} - {e_rewrite}")
                    break
                finally:
                    if not write_op_done and temp_file_path.exists():
                        try: temp_file_path.unlink()
                        except OSError: pass
            
            if not write_op_done:
                print(f"警告 [{self.room_id}]: 未能成功截断并重写历史文件 {self.file_path}。")


    def get_messages(self, count: Optional[int] = None, perform_truncate_check: bool = True) -> List[Dict[str, Any]]:
        """
        获取存储的弹幕消息。

        Args:
            count: 要获取的最新消息数量。如果为 None，则返回所有有效消息。
            perform_truncate_check: 是否在获取消息前顺便执行一次历史截断检查。
                                     对于频繁读取的实时展示，建议设为 False 或较低频率。

        Returns:
            一个包含弹幕数据字典的列表。
        """
        with self._io_lock:
            if perform_truncate_check:
                # print(f"[{self.room_id}] get_messages 调用了截断检查...") # Debug
                self.truncate_history(force_rewrite=False) # 非强制，仅在需要时截断

            valid_messages, _, _ = self._read_all_valid_messages_nolock()
            
            if count is not None and count > 0:
                return valid_messages[-count:]
            return valid_messages

    def close(self, perform_final_truncate: bool = True):
        """
        关闭存储实例前的清理操作。

        Args:
            perform_final_truncate: 是否在关闭前执行一次最终的历史截断/文件整理。
        """
        # print(f"信息 [{self.room_id}]: 关闭 DanmuStorage...") # Debug
        if perform_final_truncate:
            # print(f"信息 [{self.room_id}]: 执行最后的文件整理...") # Debug
            self.truncate_history(force_rewrite=True) # 强制执行一次，确保文件规整
        # print(f"信息 [{self.room_id}]: DanmuStorage 已关闭。") # Debug

    def cleanup_storage_file(self):
        """删除此直播间的弹幕存储文件。"""
        with self._io_lock:
            try:
                if self.file_path.exists():
                    self.file_path.unlink()
                    print(f"信息 [{self.room_id}]: 已清理存储文件: {self.file_path}")
            except Exception as e:
                print(f"错误 [{self.room_id}]: 清理存储文件 {self.file_path} 失败: {e}")








