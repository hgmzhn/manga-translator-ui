"""
内存清理工具模块

提供统一的内存清理功能，用于翻译完成后释放模型占用的内存。
特别针对CPU模式进行优化，因为CPU模式下模型权重直接占用RAM。
"""
import logging

logger = logging.getLogger(__name__)


def cleanup_all_model_caches(unload_models: bool = False) -> int:
    """
    清理所有模块的模型缓存，并可选择显式卸载模型实例
    
    Args:
        unload_models: 是否显式卸载模型实例（默认False）
                      - True: 卸载模型并清理缓存（释放内存更彻底，但下次使用需要重新加载）
                      - False: 只清理缓存字典（保留模型实例，下次使用更快）
    
    Returns:
        清理的缓存数量
    """
    cleanup_count = 0
    
    # 清理翻译器缓存
    try:
        from manga_translator.translators import translator_cache
        if translator_cache:
            if unload_models:
                # 先卸载所有模型实例
                for model_instance in list(translator_cache.values()):
                    try:
                        if hasattr(model_instance, '_unload'):
                            model_instance._unload()
                        elif hasattr(model_instance, 'unload'):
                            model_instance.unload()
                        # 删除模型引用
                        del model_instance
                    except Exception as e:
                        logger.debug(f"Error unloading translator model: {e}")
            
            cleanup_count += len(translator_cache)
            translator_cache.clear()
    except Exception as e:
        logger.debug(f"Error clearing translator cache: {e}")
    
    # 清理OCR缓存
    try:
        from manga_translator.ocr import ocr_cache
        if ocr_cache:
            if unload_models:
                # 先卸载所有模型实例
                for model_instance in list(ocr_cache.values()):
                    try:
                        if hasattr(model_instance, '_unload'):
                            model_instance._unload()
                        elif hasattr(model_instance, 'unload'):
                            model_instance.unload()
                        del model_instance
                    except Exception as e:
                        logger.debug(f"Error unloading OCR model: {e}")
            
            cleanup_count += len(ocr_cache)
            ocr_cache.clear()
    except Exception as e:
        logger.debug(f"Error clearing OCR cache: {e}")
    
    # 清理检测器缓存
    try:
        from manga_translator.detection import detector_cache
        if detector_cache:
            if unload_models:
                # 先卸载所有模型实例
                for model_instance in list(detector_cache.values()):
                    try:
                        if hasattr(model_instance, '_unload'):
                            model_instance._unload()
                        elif hasattr(model_instance, 'unload'):
                            model_instance.unload()
                        del model_instance
                    except Exception as e:
                        logger.debug(f"Error unloading detector model: {e}")
            
            cleanup_count += len(detector_cache)
            detector_cache.clear()
    except Exception as e:
        logger.debug(f"Error clearing detector cache: {e}")
    
    # 清理修复器缓存
    try:
        from manga_translator.inpainting import inpainter_cache
        if inpainter_cache:
            if unload_models:
                # 先卸载所有模型实例
                for model_instance in list(inpainter_cache.values()):
                    try:
                        if hasattr(model_instance, '_unload'):
                            model_instance._unload()
                        elif hasattr(model_instance, 'unload'):
                            model_instance.unload()
                        del model_instance
                    except Exception as e:
                        logger.debug(f"Error unloading inpainter model: {e}")
            
            cleanup_count += len(inpainter_cache)
            inpainter_cache.clear()
    except Exception as e:
        logger.debug(f"Error clearing inpainter cache: {e}")
    
    # 清理超分缓存
    try:
        from manga_translator.upscaling import upscaler_cache
        if upscaler_cache:
            if unload_models:
                # 先卸载所有模型实例
                for model_instance in list(upscaler_cache.values()):
                    try:
                        if hasattr(model_instance, '_unload'):
                            model_instance._unload()
                        elif hasattr(model_instance, 'unload'):
                            model_instance.unload()
                        del model_instance
                    except Exception as e:
                        logger.debug(f"Error unloading upscaler model: {e}")
            
            cleanup_count += len(upscaler_cache)
            upscaler_cache.clear()
    except Exception as e:
        logger.debug(f"Error clearing upscaler cache: {e}")
    
    # 清理着色器缓存
    try:
        from manga_translator.colorization import colorizer_cache
        if colorizer_cache:
            if unload_models:
                # 先卸载所有模型实例
                for model_instance in list(colorizer_cache.values()):
                    try:
                        if hasattr(model_instance, '_unload'):
                            model_instance._unload()
                        elif hasattr(model_instance, 'unload'):
                            model_instance.unload()
                        del model_instance
                    except Exception as e:
                        logger.debug(f"Error unloading colorizer model: {e}")
            
            cleanup_count += len(colorizer_cache)
            colorizer_cache.clear()
    except Exception as e:
        logger.debug(f"Error clearing colorizer cache: {e}")
    
    return cleanup_count


def cleanup_gpu_memory():
    """
    清理GPU显存和PyTorch内部缓存
    """
    try:
        import torch
        if torch.cuda.is_available():
            # 清空CUDA缓存
            pass
            pass
            # 清理CUDA内存池
            try:
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.reset_accumulated_memory_stats()
            except Exception:
                pass
            
            return True
    except Exception:
        pass
    return False


def cleanup_physical_memory():
    """
    释放物理内存（Windows特定）
    
    在Windows上优先调用 EmptyWorkingSet 强制释放物理内存，
    再回退到 SetProcessWorkingSetSize。
    对CPU模式特别重要。
    """
    try:
        import ctypes
        import os

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        process_handle = kernel32.OpenProcess(0x0400 | 0x0100, False, os.getpid())
        if not process_handle:
            return False

        try:
            if hasattr(psapi, "EmptyWorkingSet") and psapi.EmptyWorkingSet(process_handle):
                return True
            if kernel32.SetProcessWorkingSetSize(process_handle, -1, -1):
                return True
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        pass  # 非Windows系统忽略
    return False


def full_memory_cleanup(log_callback=None, unload_models: bool = False):
    """
    执行完整的内存清理
    
    Args:
        log_callback: 日志回调函数，接收字符串消息（已弃用，保留参数兼容性）
        unload_models: 是否显式卸载模型实例（默认False）
                      - True: 卸载模型并清理缓存（释放内存更彻底，但下次使用需要重新加载）
                      - False: 只清理缓存字典（保留模型实例，下次使用更快）
    
    Returns:
        dict: 包含清理结果的字典
    """
    result = {
        'caches_cleared': 0,
        'gpu_cleared': False,
        'physical_memory_released': False,
        'models_unloaded': unload_models
    }
    
    # 1. 清理模型缓存
    result['caches_cleared'] = cleanup_all_model_caches(unload_models=unload_models)
    
    # 2. 强制垃圾回收（多次执行确保彻底清理）
    pass
    pass
    pass
    # 3. 清理GPU显存
    result['gpu_cleared'] = cleanup_gpu_memory()
    
    # 4. 释放物理内存
    result['physical_memory_released'] = cleanup_physical_memory()
    
    return result


