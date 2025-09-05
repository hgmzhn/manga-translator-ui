import os
import sys

# Force disable Triton for xformers to avoid PyInstaller issues
os.environ['XFORMERS_FORCE_DISABLE_TRITON'] = '1'

# 设置编码以解决中文显示问题
if sys.platform == "win32":
    import locale
    try:
        # 尝试设置UTF-8编码
        locale.setlocale(locale.LC_ALL, 'zh_CN.UTF-8')
    except:
        try:
            # 备用编码设置
            locale.setlocale(locale.LC_ALL, 'Chinese_China.936')
        except:
            pass  # 忽略编码设置错误

# 快速DPI设置，不输出错误信息以加快启动
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except:
    pass  # 静默忽略DPI设置错误

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 延迟导入，减少启动时的初始加载时间
_app_module = None
_async_service_module = None

def get_app_class():
    """延迟导入App类"""
    global _app_module
    if _app_module is None:
        from app import App
        _app_module = App
    return _app_module

def get_async_services():
    """延迟导入异步服务"""
    global _async_service_module  
    if _async_service_module is None:
        from services.async_service import get_async_service, shutdown_async_service
        _async_service_module = (get_async_service, shutdown_async_service)
    return _async_service_module

if __name__ == "__main__":
    async_service = None
    try:
        # 快速启动：先创建UI，后台初始化服务
        print("Starting Manga Image Translator...")
        
        # 延迟导入和创建应用
        App = get_app_class()
        get_async_service, shutdown_async_service = get_async_services()
        
        # 异步初始化服务（不阻塞UI显示）
        async_service = get_async_service()
        
        # 创建并显示应用
        app = App()
        print("UI loaded, starting application...")
        app.mainloop()
        
    except ImportError as e:
        print(f"Import error: {e}")
        print("Please ensure all dependencies are installed.")
        sys.exit(1)
    except Exception as e:
        print(f"Application error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if async_service:
            try:
                shutdown_async_service()
            except:
                pass  # 静默处理关闭错误