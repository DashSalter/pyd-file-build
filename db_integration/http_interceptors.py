# 核心需求：当前的采集平台无法处理突发的响应结构变化。例如：突发的验证码验证，响应结构发送变化等。

# 计划开发响应结构的拦截器，如果响应的结构是错误的，需要上报给后台，后台会进行记录，方便后续的排查问题。增加平台的稳定性。

# 功能明确：
# 1.智能匹配所有的响应结构
# 2.如果匹配到位置结构，上报飞书系统，并指明是哪个接口，将响应结构也进行上报。
# 3.该系统将可以自由的添加json结构，以适应后续更多的json类型
# 4.该系统将需要接收的是一个response对象，对象中存在json()方法和text属性。===》在请求完成后，启动系统




import json
import datetime
import traceback # 用于获取更详细的堆栈信息，如果需要的话
from jsonschema import validate, ValidationError
# 假设 FS_bot.py 在同一个目录下或已正确安装到PYTHONPATH
# from FS_bot import OptimizedFeishuReporter # 取消注释这一行，当您实际运行时

# jsonschema 是一个第三方库，用于验证 JSON 数据是否符合 JSON Schema。
class ResponseInterceptor:
    def __init__(self, schema_map: dict, feishu_reporter): # 接收 feishu_reporter 实例
        """
        初始化响应拦截器。

        参数:
            schema_map (dict): API 标识符到 JSON Schema 的映射。
                                例如: {"https://api.example.com/v1/users": user_schema_dict}
            feishu_reporter: OptimizedFeishuReporter 的实例。
        """
        self.schema_map = schema_map
        self.feishu_reporter = feishu_reporter
        self.snippet_max_len = 500 # 响应体片段最大长度
        self.instance_value_snippet_max_len = 70 # Schema校验中实例值片段最大长度

    def add_schema(self, api_identifier: str, schema: dict):
        """动态添加或更新 API 的 Schema"""
        self.schema_map[api_identifier] = schema
        print(f"Schema for '{api_identifier}' added/updated.")

    def _get_schema(self, api_url: str):
        """
        根据 API URL 查找对应的 Schema。
        可以根据需要实现更复杂的匹配逻辑 (例如 URL 前缀、正则表达式)。
        """
        if api_url in self.schema_map:
            return self.schema_map[api_url]
        for prefix, schema in self.schema_map.items():
            if api_url.startswith(prefix): # 简单前缀匹配
                return schema
        return None

    def _prepare_report_data_base(self, api_url: str, http_method: str, response_status_code: int, response_content_type: str):
        """准备报告数据的基础结构"""
        return {
            "alert_title": "爬虫响应异常", # 默认标题，后续会具体化
            "alert_level": "ERROR",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "api_info": {
                "url": api_url,
                "method": http_method.upper()
            },
            "error_info": {
                "type": "",
                "message": "",
                "details_stack": ""
            },
            "response_info": {
                "status_code": response_status_code,
                "content_type_header": response_content_type,
                "body_snippet": {
                    "format": "text", # 默认为文本
                    "content": ""
                }
            },
            "schema_validation_failures": [],
            "crawler_context": {} # 可选，您可以从其他地方填充
        }

    def _format_response_body_snippet(self, response_text: str, response_content_type: str, parsed_json=None) -> dict:
        """格式化响应体片段"""
        snippet_content = ""
        snippet_format = "text"

        if 'application/json' in response_content_type.lower() and parsed_json is not None:
            try:
                pretty_json_text = json.dumps(parsed_json, indent=2, ensure_ascii=False)
                if len(pretty_json_text) > self.snippet_max_len:
                    snippet_content = pretty_json_text[:self.snippet_max_len - 20] + "\n... (内容过长已截断)"
                else:
                    snippet_content = pretty_json_text
                snippet_format = "json"
            except TypeError: # 如果JSON对象包含无法序列化的内容
                if len(response_text) > self.snippet_max_len:
                    snippet_content = response_text[:self.snippet_max_len - 20] + "... (内容过长已截断)"
                else:
                    snippet_content = response_text
                snippet_format = "text" # 回退到文本
        else: # 非JSON内容类型或JSON解析失败
            if len(response_text) > self.snippet_max_len:
                snippet_content = response_text[:self.snippet_max_len - 20] + "... (内容过长已截断)"
            else:
                snippet_content = response_text

            if 'text/html' in response_content_type.lower():
                snippet_format = "html"
            else:
                snippet_format = "text"

        return {"format": snippet_format, "content": snippet_content}


    def _format_schema_validation_error(self, validation_error: ValidationError) -> dict:
        """格式化单个 jsonschema ValidationError 为我们定义的结构"""
        path_str = ".".join(map(str, validation_error.path)) if validation_error.path else "root"
        instance_val_snip = str(validation_error.instance)
        if len(instance_val_snip) > self.instance_value_snippet_max_len:
            instance_val_snip = instance_val_snip[:self.instance_value_snippet_max_len - 3] + "..."

        return {
            "path": path_str,
            "message": validation_error.message,
            "validator": validation_error.validator,
            "expected_value": str(validation_error.validator_value),
            "instance_value_snippet": instance_val_snip
        }

    def process(self, api_url: str, http_method: str, response) -> bool:
        """
        处理并验证传入的 response 对象的结构。

        参数:
            api_url (str): 请求的 API URL。
            http_method (str): HTTP 请求方法 (例如 "GET", "POST")。
            response: 响应对象，必须包含 .json() 方法和 .text 属性，以及 .status_code 和 .headers。

        返回:
            bool: True 如果结构有效或无Schema定义（且策略为通过），False 如果结构无效或发生错误。
        """
        response_status_code = getattr(response, 'status_code', 'N/A')
        response_headers = getattr(response, 'headers', {})
        response_content_type = response_headers.get('Content-Type', 'application/octet-stream')

        report_data = self._prepare_report_data_base(api_url, http_method, response_status_code, response_content_type)
        parsed_json = None
        response_text = ""

        try:
            response_text = response.text # 获取原始文本总是一个好主意
        except Exception as e:
            # 极少数情况下，访问 .text 属性也可能失败
            report_data['alert_title'] = "读取响应文本失败"
            report_data['error_info']['type'] = "RESPONSE_TEXT_READ_ERROR"
            report_data['error_info']['message'] = f"无法读取响应对象的 .text 属性: {str(e)}"
            report_data['error_info']['details_stack'] = traceback.format_exc()
            report_data['response_info']['body_snippet']['content'] = "(无法读取响应体)"
            self.feishu_reporter.report_error(
                error=f"{report_data['alert_title']}: {report_data['error_info']['message']}",
                context=report_data
            )
            return False

        # 尝试解析 JSON
        try:
            # 这里假设 response.json() 会在内部处理 .text
            # 如果 response.json() 依赖于预先调用 .text，请确保调用顺序
            parsed_json = response.json()
        except json.JSONDecodeError as e:
            report_data['response_info']['body_snippet'] = self._format_response_body_snippet(response_text, response_content_type, None)
            report_data['alert_title'] = "响应JSON解析失败"
            report_data['error_info']['type'] = "JSON_DECODE_ERROR"
            report_data['error_info']['message'] = "无法将响应内容解析为有效的JSON。"
            report_data['error_info']['details_stack'] = f"错误: {str(e)}\n位置: 行 {e.lineno} 列 {e.colno}"

            # 检查是否是常见的HTML验证码或错误页面
            if "<html" in response_text.lower() or \
               "captcha" in response_text.lower() or \
               "验证码" in response_text.lower() or \
               "anti-spider" in response_text.lower() or \
               "waf" in response_text.lower():
                report_data['alert_title'] = "检测到验证码或HTML防护页面"
                report_data['error_info']['type'] = "UNEXPECTED_HTML_CONTENT"
                report_data['error_info']['message'] = "响应内容似乎是HTML页面，可能触发了反爬/验证码机制。"

            self.feishu_reporter.report_error(
                error=f"{report_data['alert_title']}: {report_data['error_info']['message']}", # 主错误信息
                context=report_data # 详细的结构化信息
            )
            return False # 结构无效
        except Exception as e: # 其他可能的解析错误
            report_data['response_info']['body_snippet'] = self._format_response_body_snippet(response_text, response_content_type, None)
            report_data['alert_title'] = "响应解析时发生未知错误"
            report_data['error_info']['type'] = "RESPONSE_PARSE_UNEXPECTED_ERROR"
            report_data['error_info']['message'] = f"尝试解析响应时发生意外错误: {str(e)}"
            report_data['error_info']['details_stack'] = traceback.format_exc()
            self.feishu_reporter.report_error(
                error=f"{report_data['alert_title']}: {str(e)}",
                context=report_data
            )
            return False

        # JSON 解析成功，更新响应体片段
        report_data['response_info']['body_snippet'] = self._format_response_body_snippet(response_text, response_content_type, parsed_json)

        # 获取对应的 Schema
        schema = self._get_schema(api_url)

        if not schema:
            report_data['alert_title'] = "接口Schema未定义"
            report_data['alert_level'] = "WARNING" # Schema缺失通常是警告
            report_data['error_info']['type'] = "MISSING_SCHEMA"
            report_data['error_info']['message'] = f"API '{api_url}' 未找到定义的Schema，跳过结构校验。"
            # 根据策略决定是否上报 Schema 缺失
            # self.feishu_reporter.report_error(
            #     error=f"Schema缺失: {api_url}", # 可以是一个更简洁的错误消息
            #     context=report_data,
            #     at_users=None # 或者指定负责添加Schema的人员
            # )
            print(f"警告: API '{api_url}' 未找到定义的 Schema，跳过结构校验。")
            return True # 或者 False，取决于是否将 Schema 缺失视为流程中断的错误

        # 使用 JSON Schema 进行校验
        try:
            validate(instance=parsed_json, schema=schema)
            print(f"API '{api_url}' (Method: {http_method}) 响应结构校验通过。")
            return True # 结构有效
        except ValidationError as e:
            report_data['alert_title'] = "响应结构校验失败"
            report_data['error_info']['type'] = "SCHEMA_VALIDATION_FAILED"
            report_data['error_info']['message'] = "响应JSON结构与预定义的Schema不匹配。"
            # 填充 schema_validation_failures
            report_data['schema_validation_failures'].append(self._format_schema_validation_error(e))
            # 如果需要报告所有错误，而不仅仅是第一个，需要迭代 e.context 或使用 validator.iter_errors
            # 例如，如果您想获取所有错误:
            # from jsonschema import Draft7Validator # or your preferred validator
            # validator = Draft7Validator(schema)
            # for error in sorted(validator.iter_errors(parsed_json), key=str):
            # report_data['schema_validation_failures'].append(self._format_schema_validation_error(error))
            # (为了简化，上面只添加了主错误 e)

            self.feishu_reporter.report_error(
                error=f"Schema校验失败: {api_url} - {e.message[:100]}...", # 截取部分错误信息作为主消息
                context=report_data
            )
            return False # 结构无效
        except Exception as e: # 其他 jsonschema 可能的错误
            report_data['alert_title'] = "Schema校验时发生意外错误"
            report_data['error_info']['type'] = "VALIDATION_UNEXPECTED_ERROR"
            report_data['error_info']['message'] = f"Schema校验过程中发生未知错误: {str(e)}"
            report_data['error_info']['details_stack'] = traceback.format_exc()
            self.feishu_reporter.report_error(
                error=f"Schema校验时发生意外错误: {api_url} - {str(e)}",
                context=report_data
            )
            return False # 结构无效