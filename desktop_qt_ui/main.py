import sys
import os
import logging
import warnings

# 设置 Hugging Face 镜像站（国内用户加速下载）
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

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

# 抑制 xformers/triton 警告
warnings.filterwarnings('ignore', message='.*Triton.*')
warnings.filterwarnings('ignore', module='xformers')

# 抑制 xformers 导入时的 Triton 警告（这些是直接打印到 stdout 的）
import io
import contextlib

# 临时重定向 stdout 和 stderr 来抑制 xformers 的 Triton 警告
_original_stdout = sys.stdout
_original_stderr = sys.stderr
sys.stdout = io.StringIO()
sys.stderr = io.StringIO()

from PyQt6.QtWidgets import QApplication
from main_window import MainWindow
from services import init_services

# 恢复 stdout 和 stderr（xformers 已经导入完成）
sys.stdout = _original_stdout
sys.stderr = _original_stderr

def print_memory_snapshot():
    """打印内存快照（前100行）"""
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')
    print("\n" + "="*80)
    print("📊 内存占用 TOP 100:")
    print("="*80)
    for i, stat in enumerate(top_stats[:100], 1):
        print(f"{i}. {stat}")
    print("="*80 + "\n")

def main():
    """
    应用主入口
    """
    # --- 日志配置 ---
    # 初始设置为INFO级别，稍后根据配置文件调整
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
        stream=sys.stdout,
    )

    # --- 环境设置 ---
    # Windows特殊处理：必须在创建QApplication之前设置AppUserModelID
    if sys.platform == 'win32':
        try:
            import ctypes
            # 设置AppUserModelID，让Windows识别这是独立应用
            myappid = 'manga.translator.ui.1.0'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception as e:
            pass
    
    # 1. 创建 QApplication 实例
    app = QApplication(sys.argv)
    app.setApplicationName("Manga Translator")
    app.setOrganizationName("Manga Translator")
    
    # 设置应用程序图标（用于任务栏）
    from PyQt6.QtGui import QIcon
    
    # 确定图标路径
    if getattr(sys, 'frozen', False):
        # 打包环境：图标在 _internal 目录下
        # sys.executable 是 app.exe 的路径，_internal 在同级目录
        exe_dir = os.path.dirname(sys.executable)
        icon_path = os.path.join(exe_dir, '_internal', 'doc', 'images', 'icon.ico')
    else:
        # 开发环境
        icon_path = os.path.join(os.path.dirname(__file__), '..', 'doc', 'images', 'icon.ico')
    
    icon_path = os.path.abspath(icon_path)
    app_icon = None
    
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)

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
    
    # Windows特殊处理：强制窗口显示在最前面
    if sys.platform == 'win32':
        # 设置窗口标志，使其显示在最前面
        from PyQt6.QtCore import Qt
        main_window.setWindowFlags(main_window.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        main_window.show()  # 重新显示以应用标志
        # 立即取消置顶，避免一直在最前面
        main_window.setWindowFlags(main_window.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
        main_window.show()  # 再次显示以应用标志
        
        # 设置图标并刷新
        if app_icon and not app_icon.isNull():
            main_window.setWindowIcon(app_icon)
            app.processEvents()  # 强制处理事件，刷新任务栏图标
    
    main_window.raise_()  # 将窗口提升到最前面
    main_window.activateWindow()  # 激活窗口
    app.processEvents()  # 处理所有待处理事件

    # 4. 启动事件循环
    sys.exit(app.exec())

if __name__ == '__main__':
    # 在创建QApplication之前设置DPI策略，这是解决DPI问题的另一种稳妥方式
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    main()
