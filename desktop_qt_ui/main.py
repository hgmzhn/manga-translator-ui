import logging
import os
import sys
import warnings

# 抑制第三方库的警告（必须在导入其他库之前设置）
warnings.filterwarnings('ignore', message='.*Triton.*')
warnings.filterwarnings('ignore', message='.*triton.*')
warnings.filterwarnings('ignore', message='.*pkg_resources.*')
warnings.filterwarnings('ignore', category=DeprecationWarning, module='ctranslate2')
warnings.filterwarnings('ignore', module='xformers')

# 在 PyTorch 初始化前设置显存优化，允许使用共享显存
# expandable_segments 可以减少显存碎片，避免 OOM 错误
os.environ.setdefault('PYTORCH_ALLOC_CONF', 'expandable_segments:True')

# 修复便携版Python的路径问题：将脚本所在目录添加到sys.path开头
# 便携版Python使用._pth文件会禁用自动添加脚本目录的默认行为
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 将项目根目录添加到 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 修复PyInstaller打包后onnxruntime的DLL加载问题
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # 运行在PyInstaller打包环境中
    if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
        # 只设置DLL搜索路径，不预加载
        # 让Python的导入机制自然处理DLL加载
        os.add_dll_directory(sys._MEIPASS)
        onnx_capi_dir = os.path.join(sys._MEIPASS, 'onnxruntime', 'capi')
        if os.path.exists(onnx_capi_dir):
            os.add_dll_directory(onnx_capi_dir)

# 在 PyQt6 之前加载 PyTorch，避免 PyQt6 的 Qt DLL 路径干扰 c10.dll 的加载
# 参考: https://github.com/pytorch/pytorch/issues/166628
try:
    import torch  # noqa: F401
except ImportError:
    pass

from main_window import MainWindow
from PyQt6.QtWidgets import QApplication
from services import init_services
from utils.app_version import get_app_version
from utils.resource_helper import iter_existing_resource_paths, load_icon_from_resources
from widgets.themed_message_box import install_themed_message_boxes


# 全局异常处理器，捕获未处理的异常并记录到日志
def global_exception_handler(exc_type, exc_value, exc_traceback):
    """全局异常处理器，防止程序静默崩溃"""
    import traceback
    
    # 忽略 KeyboardInterrupt
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    # 格式化异常信息
    error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    
    # 记录到日志（会写入 result/log_*.txt）
    logging.critical(f"未捕获的异常导致程序崩溃:\n{error_msg}")
    
    # 同时输出到控制台（确保能看到）
    print(f"\n{'='*60}", file=sys.stderr)
    print("❌ 程序发生未捕获的异常:", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(error_msg, file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

# 设置全局异常处理器
sys.excepthook = global_exception_handler


def _set_windows_app_user_model_id():
    """确保 Windows 将直接脚本启动识别为独立应用，而不是 python.exe。"""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'manga.translator.ui.1.0'
        )
    except Exception:
        logging.exception("设置 Windows AppUserModelID 失败")


def _apply_windows_native_window_icon(window, icon_path: str):
    """为 Windows 原生窗口句柄设置大小图标，覆盖 python.exe 默认图标。"""
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(window.winId()))
        user32 = ctypes.windll.user32
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t

        image_icon = 1
        wm_seticon = 0x0080
        icon_small = 0
        icon_big = 1
        lr_loadfromfile = 0x0010

        sm_cxicon = 11
        sm_cyicon = 12
        sm_cxsmicon = 49
        sm_cysmicon = 50

        big_icon_handle = user32.LoadImageW(
            None,
            icon_path,
            image_icon,
            user32.GetSystemMetrics(sm_cxicon),
            user32.GetSystemMetrics(sm_cyicon),
            lr_loadfromfile,
        )
        small_icon_handle = user32.LoadImageW(
            None,
            icon_path,
            image_icon,
            user32.GetSystemMetrics(sm_cxsmicon),
            user32.GetSystemMetrics(sm_cysmicon),
            lr_loadfromfile,
        )

        if big_icon_handle:
            user32.SendMessageW(hwnd, wm_seticon, icon_big, big_icon_handle)
        if small_icon_handle:
            user32.SendMessageW(hwnd, wm_seticon, icon_small, small_icon_handle)

        if big_icon_handle or small_icon_handle:
            window._native_icon_handles = (big_icon_handle, small_icon_handle)
            logging.info(f"Windows 原生窗口图标已设置: {icon_path}")
            return True

        logging.warning(f"Windows 原生窗口图标加载失败: {icon_path}")
    except Exception:
        logging.exception("设置 Windows 原生窗口图标失败")
    return False

def main():
    """
    应用主入口
    """
    # --- 日志配置（异步优化）---
    import atexit
    import queue
    import threading
    
    # 创建异步日志处理器
    class AsyncStreamHandler(logging.Handler):
        """异步日志处理器，避免阻塞主线程"""
        def __init__(self, stream=sys.stdout):
            super().__init__()
            self.stream = stream
            # 限制队列大小为1000，避免日志过多导致内存占用
            self.log_queue = queue.Queue(maxsize=1000)
            self.running = True
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()
        
        def _worker(self):
            while self.running:
                try:
                    # ✅ 减少超时时间，更快处理日志
                    record = self.log_queue.get(timeout=0.01)
                    if record is None:
                        break
                    msg = self.format(record)
                    self.stream.write(msg + '\n')
                    # ✅ 每条日志立即刷新
                    self.stream.flush()
                except queue.Empty:
                    # ✅ 即使队列为空也刷新一次，确保之前的输出显示
                    try:
                        self.stream.flush()
                    except Exception:
                        pass
                    continue
                except Exception:
                    pass
        
        def emit(self, record):
            try:
                self.log_queue.put_nowait(record)
            except queue.Full:
                pass  # 队列满时丢弃日志，避免阻塞
        
        def close(self):
            self.running = False
            self.log_queue.put(None)
            self.thread.join(timeout=1)
            super().close()
    
    # 配置异步日志（控制台）
    async_handler = AsyncStreamHandler(sys.stdout)
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')
    async_handler.setFormatter(log_formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根日志器设为 DEBUG 以允许所有日志通过
    root_logger.addHandler(async_handler)
    
    # 确保程序退出时正确关闭日志处理器
    atexit.register(async_handler.close)
    
    # --- 日志文件配置 ---
    from datetime import datetime
    
    # 创建强制刷新的文件处理器类（确保日志立即写入磁盘，防止丢失）
    class FlushingFileHandler(logging.FileHandler):
        """每次写入后立即刷新到磁盘的文件处理器"""
        def emit(self, record):
            super().emit(record)
            self.flush()  # 强制刷新缓冲区
    
    # 日志目录放在 result/ 下
    if getattr(sys, 'frozen', False):
        log_dir = os.path.join(os.path.dirname(sys.executable), '_internal', 'result')
    else:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'result')
    os.makedirs(log_dir, exist_ok=True)
    
    # 生成带时间戳的日志文件名
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    log_file_path = os.path.join(log_dir, f'log_{timestamp}.txt')
    
    # 使用强制刷新的文件处理器
    file_handler = FlushingFileHandler(log_file_path, encoding='utf-8', delay=False)
    file_handler.setLevel(logging.DEBUG)  # 始终为 DEBUG 级别
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
    
    # 确保程序退出时关闭文件处理器
    atexit.register(file_handler.close)
    
    logging.info(f"UI日志文件: {log_file_path}")
    
    # --- 确保配置文件存在 ---
    try:
        from manga_translator.utils.text_filter import ensure_filter_list_exists
        ensure_filter_list_exists()
        from manga_translator.rendering.text_replacements import ensure_text_replacements_exists
        ensure_text_replacements_exists()
        from manga_translator.utils.translation_template import ensure_translation_template_exists
        ensure_translation_template_exists()
    except Exception as e:
        logging.warning(f"创建配置文件失败: {e}")
    
    # --- 崩溃捕获 (faulthandler) ---
    # 启用 faulthandler 以捕获 C++ 级别的崩溃 (Segmentation Fault 等)
    # 将崩溃信息直接写入同一个日志文件
    import faulthandler
    # 使用 file_handler 的流对象
    faulthandler.enable(file=file_handler.stream, all_threads=True)
    logging.info("已启用崩溃捕获 (faulthandler)，崩溃信息将记录在此文件中")

    # --- 环境设置 ---
    # Windows特殊处理：必须在创建QApplication之前设置AppUserModelID
    if sys.platform == 'win32':
        _set_windows_app_user_model_id()
    
    # 1. 创建 QApplication 实例
    app = QApplication(sys.argv)
    app.setApplicationName("Manga Translator")
    app.setOrganizationName("Manga Translator")
    app_version = get_app_version()
    if app_version != "unknown":
        app.setApplicationVersion(app_version)
        logging.info(f"UI version: {app_version}")
    install_themed_message_boxes()
    
    # 设置 Qt 异常处理钩子（捕获信号槽中的异常）
    def qt_message_handler(mode, context, message):
        """Qt 消息处理器，捕获 Qt 内部错误"""
        from PyQt6.QtCore import QtMsgType
        if mode == QtMsgType.QtFatalMsg:
            logging.critical(f"Qt Fatal: {message} (file: {context.file}, line: {context.line})")
        elif mode == QtMsgType.QtCriticalMsg:
            logging.error(f"Qt Critical: {message}")
        elif mode == QtMsgType.QtWarningMsg:
            # 过滤一些常见的无害警告
            if "QWindowsWindow::setGeometry" not in message:
                logging.warning(f"Qt Warning: {message}")
        # Debug 和 Info 级别不记录，避免日志过多
    
    from PyQt6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(qt_message_handler)
    
    app_icon = None
    native_windows_icon_path = None

    icon_candidates = []
    if sys.platform == 'darwin':
        icon_candidates.extend([
            os.path.join('doc', 'images', 'icon.icns'),
            os.path.join('doc', 'images', 'icon.png'),
            os.path.join('doc', 'images', 'icon.ico'),
        ])
    else:
        icon_candidates.extend([
            os.path.join('doc', 'images', 'icon.ico'),
            os.path.join('doc', 'images', 'icon.png'),
        ])

    app_icon, icon_source = load_icon_from_resources(icon_candidates)
    if app_icon and not app_icon.isNull():
        app.setWindowIcon(app_icon)
        logging.info(f"UI 图标加载成功: {icon_source}")
    else:
        logging.warning("UI 图标加载失败：未找到可用的 icon.ico/icon.png/icon.icns")

    if sys.platform == 'win32':
        native_windows_icon_path = next(
            iter_existing_resource_paths([os.path.join('doc', 'images', 'icon.ico')]),
            None,
        )
        if not native_windows_icon_path:
            logging.warning("Windows 原生窗口图标未找到：doc/images/icon.ico")

    # 2. 初始化所有服务
    # 设置正确的根目录：打包后指向_internal，开发时指向项目根目录
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller打包环境：所有资源在_internal目录
        root_dir = sys._MEIPASS
    else:
        # 开发环境：资源在项目根目录
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    if not init_services(root_dir):
        logging.fatal("Fatal: Service initialization failed.")
        sys.exit(1)

    # 3. 创建并显示主窗口
    main_window = MainWindow()
    
    # 确保主窗口也设置了图标
    if app_icon and not app_icon.isNull():
        main_window.setWindowIcon(app_icon)
    
    main_window.show()

    if sys.platform == 'win32' and native_windows_icon_path:
        _apply_windows_native_window_icon(main_window, native_windows_icon_path)

    # 避免在 Windows 初始 show 流程内同步处理事件。
    # 这会触发 Qt/Windows 的重入消息处理，可能导致 RPC_E_CANTCALLOUT_ININPUTSYNCCALL。
    from PyQt6.QtCore import QTimer

    def finalize_window_activation():
        try:
            if main_window.isMinimized():
                main_window.showNormal()

            main_window.raise_()
            main_window.activateWindow()

            if sys.platform == 'win32':
                try:
                    import ctypes
                    from ctypes import wintypes

                    user32 = ctypes.windll.user32
                    kernel32 = ctypes.windll.kernel32

                    user32.GetForegroundWindow.restype = wintypes.HWND
                    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
                    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
                    user32.AttachThreadInput.restype = wintypes.BOOL
                    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.ShowWindow.restype = wintypes.BOOL
                    user32.SetWindowPos.argtypes = [
                        wintypes.HWND,
                        wintypes.HWND,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_int,
                        ctypes.c_uint,
                    ]
                    user32.SetWindowPos.restype = wintypes.BOOL
                    user32.BringWindowToTop.argtypes = [wintypes.HWND]
                    user32.BringWindowToTop.restype = wintypes.BOOL
                    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                    user32.SetForegroundWindow.restype = wintypes.BOOL
                    user32.SetActiveWindow.argtypes = [wintypes.HWND]
                    user32.SetActiveWindow.restype = wintypes.HWND
                    user32.SetFocus.argtypes = [wintypes.HWND]
                    user32.SetFocus.restype = wintypes.HWND
                    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

                    hwnd = int(main_window.winId())
                    if hwnd:
                        SW_RESTORE = 9
                        SW_SHOW = 5
                        SWP_NOMOVE = 0x0002
                        SWP_NOSIZE = 0x0001
                        SWP_SHOWWINDOW = 0x0040
                        HWND_TOPMOST = -1
                        HWND_NOTOPMOST = -2

                        foreground_hwnd = user32.GetForegroundWindow()
                        current_thread_id = kernel32.GetCurrentThreadId()
                        foreground_thread_id = 0
                        if foreground_hwnd:
                            foreground_thread_id = user32.GetWindowThreadProcessId(
                                wintypes.HWND(foreground_hwnd),
                                None,
                            )

                        attached = False
                        if foreground_thread_id and foreground_thread_id != current_thread_id:
                            attached = bool(
                                user32.AttachThreadInput(
                                    wintypes.DWORD(foreground_thread_id),
                                    wintypes.DWORD(current_thread_id),
                                    True,
                                )
                            )

                        try:
                            user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
                            user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOW)
                            user32.SetWindowPos(
                                wintypes.HWND(hwnd),
                                wintypes.HWND(HWND_TOPMOST),
                                0,
                                0,
                                0,
                                0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
                            )
                            user32.SetWindowPos(
                                wintypes.HWND(hwnd),
                                wintypes.HWND(HWND_NOTOPMOST),
                                0,
                                0,
                                0,
                                0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
                            )
                            user32.BringWindowToTop(wintypes.HWND(hwnd))
                            user32.SetForegroundWindow(wintypes.HWND(hwnd))
                            user32.SetActiveWindow(wintypes.HWND(hwnd))
                            user32.SetFocus(wintypes.HWND(hwnd))
                        finally:
                            if attached:
                                user32.AttachThreadInput(
                                    wintypes.DWORD(foreground_thread_id),
                                    wintypes.DWORD(current_thread_id),
                                    False,
                                )
                except Exception as exc:
                    logging.debug(f"Windows 前台激活失败: {exc}")
        except Exception as exc:
            logging.debug(f"激活主窗口失败: {exc}")

    if sys.platform == 'win32':
        QTimer.singleShot(0, finalize_window_activation)
        QTimer.singleShot(250, finalize_window_activation)
    else:
        QTimer.singleShot(0, finalize_window_activation)

    # 4. 启动事件循环
    ret = app.exec()
    logging.info("Exiting application...")

    try:
        from services import shutdown_services
        shutdown_services()
    except Exception as e:
        logging.error(f"关闭服务时出错: {e}", exc_info=True)
    
    # 确保所有日志都写入文件
    try:
        # 刷新所有日志处理器
        for handler in logging.root.handlers:
            handler.flush()
        
        # 关闭异步日志处理器
        if 'async_handler' in locals():
            async_handler.close()
        
        # 关闭文件日志处理器
        if 'file_handler' in locals():
            file_handler.flush()
            file_handler.close()
    except Exception as e:
        print(f"关闭日志处理器时出错: {e}", file=sys.stderr)
    
    # 使用 os._exit 强制退出，防止守护线程阻塞
    os._exit(ret)

if __name__ == '__main__':
    # 在创建QApplication之前设置DPI策略，这是解决DPI问题的另一种稳妥方式
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    main()
