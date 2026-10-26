"""图形界面：选择入口文件、解释器，触发编译并实时展示日志。"""

import os
import queue
import subprocess
import sys
import threading
import tkinter
import tkinter.filedialog
from pathlib import Path
from tkinter import messagebox

import customtkinter

from pyd_build.compiler import CompileCore
from pyd_build.config import Config


class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        # --- 窗口基础设置 --- 使用黑夜主题
        self.title("Cython (.pyd) 编译器")
        self.geometry("1200x750")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 设置UI主题
        if Config.theme.value:
            customtkinter.set_appearance_mode("Dark")  # 模式: System, Dark, Light
            customtkinter.set_default_color_theme("dark-blue")  # 主题: blue, dark-blue, green
        else:
            customtkinter.set_appearance_mode("Light")  # 模式: System, Dark, Light
            customtkinter.set_default_color_theme("blue")  # 主题: blue, dark-blue, green

        # --- UI 控件创建 ---
        # 1. 路径选择框架
        self.path_frame = customtkinter.CTkFrame(self, corner_radius=10)
        self.path_frame.grid(row=0, column=0, padx=20, pady=20, sticky="ew")
        self.path_frame.grid_columnconfigure(1, weight=1)

        # 入口文件：源目录与输出目录都由它推导
        self.entry_label = customtkinter.CTkLabel(
            self.path_frame, text="入口文件:", width=60
        )
        self.entry_label.grid(row=0, column=0, padx=10, pady=10)

        self.entry_entry = customtkinter.CTkEntry(
            self.path_frame,
            placeholder_text="选择入口 .py（如 main.py），源目录与输出目录自动推导",
        )
        self.entry_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        self.entry_entry.insert(0, Config.entry_file.value)
        self.entry_entry.bind("<KeyRelease>", self.on_entry_file_change)
        self.entry_entry.bind("<FocusOut>", self._normalize_entry_display)
        self.entry_entry.bind("<Return>", self._normalize_entry_display)
        self.entry_button = customtkinter.CTkButton(
            self.path_frame, text="浏览...", width=80, command=self.select_entry_file
        )
        self.entry_button.grid(row=0, column=2, padx=10, pady=10, sticky="w")

        # 只读提示：显示由入口推导出的项目目录与输出目录
        self.path_hint = customtkinter.CTkLabel(
            self.path_frame,
            text="",
            justify="left",
            anchor="w",
            font=("", 12),
            text_color="gray",
        )
        self.path_hint.grid(
            row=1, column=1, columnspan=2, padx=10, pady=(0, 8), sticky="w"
        )

        # 选择解释器
        self.interpreter_label = customtkinter.CTkLabel(
            self.path_frame, text="编译解释器:", width=50
        )
        self.interpreter_label.grid(row=2, column=0, padx=10, pady=10)

        interpreter_list = Config.interpreter_path.value
        interpreter_index = Config.interpreter_index.value
        self.interpreter_entry = customtkinter.CTkComboBox(
            self.path_frame, values=interpreter_list
        )
        if interpreter_list:
            self.interpreter_entry.set(interpreter_list[interpreter_index])
        self.interpreter_entry.grid(row=2, column=1, padx=10, pady=10, sticky="ew")
        self.interpreter_entry.configure(command=self.on_select)  # 绑定选择事件

        # 功能选项
        self.functional_options = customtkinter.CTkLabel(
            self.path_frame, text="功能选项:", width=50
        )
        self.functional_options.grid(row=3, column=0, padx=10, pady=10)

        # 添加一个开关
        self.switch_var = tkinter.BooleanVar()
        if Config.default_is_compress.value:
            self.switch_var.set(True)
        else:
            self.switch_var.set(False)
        self.switch = customtkinter.CTkSwitch(
            self.path_frame, text="压缩成zip压缩包", variable=self.switch_var
        )
        self.switch.grid(row=3, column=1, padx=10, pady=10, sticky="w")

        # 添加主题切换开关
        self.theme_var = tkinter.BooleanVar()
        self.theme_var.set(Config.theme.value)
        theme_text = "黑夜" if Config.theme.value else "白天"
        self.theme_var_check = customtkinter.CTkSwitch(
            self.path_frame, text=theme_text, variable=self.theme_var, command=self.change_theme
        )
        self.theme_var_check.grid(row=3, column=1, padx=10, pady=10, sticky="e")

        # 2. 编译按钮 按钮放最右边 背景颜色修改
        self.action_frame = customtkinter.CTkFrame(
            self.path_frame, fg_color="transparent"
        )
        self.action_frame.grid(row=3, column=2, padx=10, pady=10)

        self.compile_button = customtkinter.CTkButton(
            self.action_frame,
            text="开始编译",
            height=40,
            font=("", 16),
            command=self.start_compilation,
        )
        self.compile_button.grid(row=0, column=0, padx=(0, 8))

        # 测试按钮：在输出目录里用目标解释器跑一遍入口文件
        self.test_button = customtkinter.CTkButton(
            self.action_frame,
            text="运行测试",
            width=100,
            height=40,
            font=("", 16),
            fg_color="#2E7D5B",
            hover_color="#25664A",
            command=self.run_test,
        )
        self.test_button.grid(row=0, column=1)

        # 3. 日志输出文本框
        self.log_textbox = customtkinter.CTkTextbox(
            self, corner_radius=10, state="disabled", font=("", 13)
        )
        self.log_textbox.grid(row=2, column=0, padx=20, pady=(0, 10), sticky="nsew")

        # --- 线程通信设置 ---
        self.log_queue = queue.Queue()

        # 测试子进程句柄（运行测试时非 None）
        self._test_proc: subprocess.Popen | None = None

        # 按已保存的入口文件推导一次目录（相对路径补全为绝对路径）
        self._normalize_entry_display()

        # 启动日志队列消费循环（全程只需一个）
        self.process_log_queue()

    def change_theme(self):
        """切换主题（白天 / 黑夜）"""
        dark = bool(self.theme_var.get())
        # 模式: System / Dark / Light；主题: blue / dark-blue / green
        customtkinter.set_appearance_mode("Dark" if dark else "Light")
        customtkinter.set_default_color_theme("dark-blue" if dark else "blue")
        self.theme_var_check.configure(text="黑夜" if dark else "白天")
        Config.theme.value = dark

    def _resolve_entry_path(self, raw: str) -> Path:
        """把入口输入解析为绝对路径（相对路径优先相对已保存的源目录）"""
        entry = Path(raw).expanduser()
        if entry.is_absolute():
            return entry
        base = Config.project_path.value
        return (Path(base) / entry) if base else (Path.cwd() / entry)

    def _apply_entry_path(self, path: str) -> None:
        """根据入口文件推导源目录与输出目录

        源目录 = 入口文件所在目录；输出目录 = 同级目录 + "_pyd"。
        """
        raw = path.strip()
        if not raw:
            self._update_path_hint()
            return
        entry = self._resolve_entry_path(raw)
        if not entry.is_file():
            self._update_path_hint()
            return
        source_dir = entry.resolve().parent
        Config.project_path.value = str(source_dir)
        Config.output_path.value = str(source_dir.parent / f"{source_dir.name}_pyd")
        self._update_path_hint()

    def _update_path_hint(self) -> None:
        """刷新只读提示：显示项目目录与输出目录的完整路径"""
        source_dir = Config.project_path.value
        output_dir = Config.output_path.value
        if not self.entry_entry.get().strip():
            self.path_hint.configure(text="未选择入口文件")
            return
        if not source_dir or not output_dir:
            self.path_hint.configure(text="入口文件无效，无法确定项目路径")
            return
        self.path_hint.configure(
            text=f"项目：{source_dir}\n输出：{output_dir}"
        )

    def _normalize_entry_display(self, _event=None) -> None:
        """失焦或回车时把入口输入补全成绝对路径，并刷新提示"""
        raw = self.entry_entry.get().strip()
        if not raw:
            Config.entry_file.value = ""
            self._update_path_hint()
            return
        entry = self._resolve_entry_path(raw)
        if not entry.is_file():
            self._update_path_hint()
            return
        resolved = str(entry.resolve())
        self.entry_entry.delete(0, "end")
        self.entry_entry.insert(0, resolved)
        Config.entry_file.value = resolved
        self._apply_entry_path(resolved)

    def on_entry_file_change(self, _event=None):
        """入口文件输入框变化时实时保存并推导目录"""
        path = self.entry_entry.get().strip()
        Config.entry_file.value = path
        self._apply_entry_path(path)

    def select_entry_file(self):
        """打开对话框选择入口 .py 文件，保存绝对路径并推导目录"""
        initial = Config.project_path.value or str(Path.home())
        path = tkinter.filedialog.askopenfilename(
            title="选择入口文件",
            initialdir=initial,
            filetypes=(("Python 文件", "*.py"), ("所有文件", "*.*")),
        )
        if not path:
            return
        path = str(Path(path).resolve())
        self.entry_entry.delete(0, "end")
        self.entry_entry.insert(0, path)
        Config.entry_file.value = path
        self._apply_entry_path(path)

    # 3. 获取选中项的索引（下拉选择或手动输入路径）
    def on_select(self, _event=None):
        selected_value = self.interpreter_entry.get().strip()
        if not selected_value:
            return
        paths = list(Config.interpreter_path.value)
        if selected_value not in paths:
            # 允许手动输入列表外的解释器（比如虚拟环境里的 python.exe）
            paths.append(selected_value)
            Config.interpreter_path.value = paths
        Config.interpreter_index.value = paths.index(selected_value)

    # def select_interpreter_dir(self):
    #     """打开对话框选择解释器，选择python.exe文件"""
    #     path = tkinter.filedialog.askopenfilename(title="选择解释器文件", filetypes=((".exe", "*.exe"),))
    #     if path:
    #         self.interpreter_entry.delete(0, "end")
    #         self.interpreter_entry.insert(0, path)
    #         Config.interpreter_path.value = path

    # --- 事件处理函数 ---
    def log_message(self, message):
        """线程安全地将日志消息放入队列"""
        self.log_queue.put(message)

    def process_log_queue(self):
        """从队列中获取日志并更新到 UI 文本框（同一轮的多条合并插入）"""
        try:
            pending: list[str] = []
            while not self.log_queue.empty():
                message = self.log_queue.get_nowait()

                if message == "COMPILATION_DONE":
                    self._append_log(pending)
                    pending.clear()
                    # 收到结束信号，重新启用按钮
                    self.compile_button.configure(state="normal", text="开始编译")
                    return

                if isinstance(message, str) and message.startswith("TEST_DONE:"):
                    code = int(message.split(":", 1)[1])
                    pending.append(f"[测试] 退出码 {code}（{'通过' if code == 0 else '失败'}）")
                    self._append_log(pending)
                    pending.clear()
                    self.test_button.configure(
                        text="运行测试", fg_color="#2E7D5B", hover_color="#25664A"
                    )
                    continue

                pending.append(str(message))

            self._append_log(pending)
        finally:
            # 每隔 100 毫秒再次检查队列
            self.after(100, self.process_log_queue)

    def _append_log(self, lines: list[str]) -> None:
        """把若干行日志一次性写进日志框，避免逐行刷新界面"""
        if not lines:
            return
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", "".join(f"{line}\n" for line in lines))
        self.log_textbox.configure(state="disabled")
        self.log_textbox.see("end")  # 自动滚动到最后

    # --- 测试运行：在输出目录里跑一遍入口文件 ---
    def run_test(self):
        """用目标解释器在输出目录中运行入口文件，快速验证编译结果"""
        # 正在运行时再点一次 = 停止
        if self._test_proc is not None and self._test_proc.poll() is None:
            self._test_proc.kill()
            self.log_message("[测试] 已手动停止")
            return

        self._normalize_entry_display()
        entry_path = self.entry_entry.get().strip()
        if not entry_path or not self._resolve_entry_path(entry_path).is_file():
            messagebox.showerror("错误", "请先选择有效的入口文件！")
            return

        output_dir = Path(Config.output_path.value or "")
        if not output_dir.is_dir():
            messagebox.showerror("错误", "输出目录不存在，请先编译！")
            return

        interpreter = self.interpreter_entry.get().strip()
        if not interpreter or not Path(interpreter).is_file():
            interpreter = sys.executable

        entry = self._resolve_entry_path(entry_path).resolve()
        source_dir = Path(Config.project_path.value or "")
        try:
            rel_entry = entry.relative_to(source_dir.resolve()).as_posix()
        except (ValueError, OSError):
            rel_entry = entry.name
        if not (output_dir / rel_entry).is_file():
            rel_entry = entry.name

        # 清空日志并开始输出
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")
        self.log_message(f"[测试] 解释器: {interpreter}")
        self.log_message(f"[测试] 工作目录: {output_dir}")
        self.log_message(f"[测试] 运行: {rel_entry}\n")

        try:
            proc = subprocess.Popen(
                [interpreter, rel_entry],
                cwd=str(output_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.log_message(f"[测试] 启动失败: {exc}")
            return

        self._test_proc = proc
        self.test_button.configure(
            text="停止测试", fg_color="#8B3A3A", hover_color="#6E2E2E"
        )
        threading.Thread(
            target=self._pump_test_output, args=(proc,), daemon=True
        ).start()

    def _pump_test_output(self, proc):
        """把测试子进程的输出实时送进日志队列"""
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    self.log_message(f"[测试] {line.rstrip()}")
        finally:
            code = proc.wait()
            self.log_queue.put(f"TEST_DONE:{code}")

    def start_compilation(self):
        """点击“开始编译”按钮后触发"""
        # 入口文件可能是粘贴进来的，没有触发 KeyRelease，这里再同步一次
        entry_path = self.entry_entry.get().strip()
        self._normalize_entry_display()  # 补全为绝对路径并刷新提示
        entry_path = self.entry_entry.get().strip()
        Config.entry_file.value = entry_path
        source_dir = Config.project_path.value

        if not entry_path:
            messagebox.showerror("错误", "请先选择入口文件！")
            return
        if not self._resolve_entry_path(entry_path).is_file():
            messagebox.showerror("错误", "入口文件不存在，请重新选择！")
            return
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror("错误", "入口文件无效，无法推导源目录！")
            return

        # 禁用按钮，清空日志，准备开始
        self.compile_button.configure(state="disabled", text="正在编译中...")
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

        # 在新线程中运行编译任务，防止UI卡死
        compile_thread = threading.Thread(
            target=self.compile_thread_worker,
            daemon=True,
        )
        compile_thread.start()

    def compile_thread_worker(self):
        """这是在新线程中实际执行的函数"""
        try:
            CompileCore(self.log_message).start_compile_project(self.switch_var.get())
        finally:
            # 任务结束后，发送一个完成信号到队列
            self.log_queue.put("COMPILATION_DONE")


def main() -> None:
    """启动图形界面。"""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
