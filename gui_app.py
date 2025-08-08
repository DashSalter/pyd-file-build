import tkinter
import tkinter.filedialog
import customtkinter
import threading
import queue
from tkinter import messagebox
# 导入我们的核心编译逻辑
from Utils.compiler_core import CompileCore
from Utils.config import Config

r"""
[无命令窗口]
.venv\Scripts\pyinstaller.exe -w -y --clean .\gui_app.py -i fm.ico --upx-dir upx-3.96-win64
"""
interpreter_path = 'python_interpreter'


class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        # --- 窗口基础设置 ---
        self.title("Cython (.pyd) 编译器")
        self.geometry("1200x750")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 设置UI主题
        customtkinter.set_appearance_mode("System")  # 模式: System, Dark, Light
        customtkinter.set_default_color_theme("blue")  # 主题: blue, dark-blue, green

        # --- UI 控件创建 ---

        # 1. 路径选择框架
        self.path_frame = customtkinter.CTkFrame(self, corner_radius=10)
        self.path_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        self.path_frame.grid_columnconfigure(1, weight=1)

        # 源目录
        self.source_label = customtkinter.CTkLabel(
            self.path_frame, text="源目录:", width=100
        )
        self.source_label.grid(row=0, column=0, padx=10, pady=10)
        self.source_entry = customtkinter.CTkEntry(
            self.path_frame, placeholder_text="请选择包含 .py 文件的项目目录"
        )
        self.source_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        self.source_entry.insert(0, Config.project_path.value)
        self.source_button = customtkinter.CTkButton(
            self.path_frame, text="浏览...", width=80, command=self.select_source_dir
        )
        self.source_button.grid(row=0, column=2, padx=10, pady=10)

        # 选择解释器
        self.interpreter_label = customtkinter.CTkLabel(
            self.path_frame, text="编译解释器:", width=100
        )
        self.interpreter_label.grid(row=1, column=0, padx=10, pady=10)

        self.interpreter_entry = customtkinter.CTkEntry(
            self.path_frame, placeholder_text="请选择解释器python.exe文件"
        )
        self.interpreter_entry.grid(row=1, column=1, padx=10, pady=10, sticky="ew")
        self.interpreter_entry.insert(0, Config.interpreter_path.value)
        self.interpreter_button = customtkinter.CTkButton(
            self.path_frame, text="浏览...", width=80, command=self.select_interpreter_dir
        )
        self.interpreter_button.grid(row=1, column=2, padx=10, pady=10)

        # 2. 编译按钮
        self.compile_button = customtkinter.CTkButton(
            self,
            text="开始编译",
            height=40,
            font=("", 16),
            command=self.start_compilation,
        )
        self.compile_button.grid(row=1, column=0, padx=20, pady=10, sticky="ew")

        # 3. 日志输出文本框
        self.log_textbox = customtkinter.CTkTextbox(
            self, corner_radius=10, state="disabled", font=("", 13)
        )
        self.log_textbox.grid(row=2, column=0, padx=20, pady=20, sticky="nsew")

        # --- 线程通信设置 ---
        self.log_queue = queue.Queue()

    def select_interpreter_dir(self):
        """打开对话框选择解释器，选择python.exe文件"""
        path = tkinter.filedialog.askopenfilename(title="选择解释器文件", filetypes=((".exe", "*.exe"),))
        if path:
            self.interpreter_entry.delete(0, "end")
            self.interpreter_entry.insert(0, path)
            Config.interpreter_path.value = path

    # --- 事件处理函数 ---
    def select_source_dir(self):
        """打开对话框选择源目录"""
        path = tkinter.filedialog.askdirectory(title="选择源项目目录")
        if path:
            self.source_entry.delete(0, "end")
            self.source_entry.insert(0, path)
            Config.project_path.value = path

    def log_message(self, message):
        """线程安全地将日志消息放入队列"""
        self.log_queue.put(message)

    def process_log_queue(self):
        """从队列中获取日志并更新到UI文本框"""
        try:
            while not self.log_queue.empty():
                message = self.log_queue.get_nowait()
                if message == "COMPILATION_DONE":
                    # 收到结束信号，重新启用按钮
                    self.compile_button.configure(state="normal", text="开始编译")
                    return

                # 更新UI
                self.log_textbox.configure(state="normal")
                self.log_textbox.insert("end", message + "\n")
                self.log_textbox.configure(state="disabled")
                self.log_textbox.see("end")  # 自动滚动到最后
        finally:
            # 每隔100毫秒再次检查队列
            self.after(100, self.process_log_queue)

    def start_compilation(self):
        """点击“开始编译”按钮后触发"""
        source_dir = self.source_entry.get()

        interpreter_file = self.interpreter_entry.get()

        if not source_dir:
            messagebox.showerror("错误", "请选择源目录！")
            return

        # 禁用按钮，清空日志，准备开始
        self.compile_button.configure(state="disabled", text="正在编译中...")
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

        # 启动日志队列处理循环
        self.process_log_queue()

        # 在新线程中运行编译任务，防止UI卡死
        compile_thread = threading.Thread(
            target=self.compile_thread_worker,
            args=(source_dir, interpreter_file),
            daemon=True,
        )
        compile_thread.start()

    def compile_thread_worker(self, source_dir, interpreter_file):
        """这是在新线程中实际执行的函数"""
        try:
            CompileCore(source_dir, self.log_message, interpreter_file).start_compile_project()
        finally:
            # 任务结束后，发送一个完成信号到队列
            self.log_queue.put("COMPILATION_DONE")


if __name__ == "__main__":
    # --- 运行程序 ---
    app = App()
    app.mainloop()
