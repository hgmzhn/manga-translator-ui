import colorama

from .utils.dotenv_utils import load_app_dotenv

colorama.init(autoreset=True)
load_app_dotenv(override=False)

# 延迟导入，避免在 --help 时加载大型库
def __getattr__(name):
    """延迟导入 MangaTranslator 等类"""
    if name in ['MangaTranslator', 'Config', 'Context']:
        from .manga_translator import Config, Context, MangaTranslator
        globals()[name] = locals()[name]
        return locals()[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
