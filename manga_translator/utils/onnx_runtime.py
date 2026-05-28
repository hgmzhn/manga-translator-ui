from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

ModelPath = Union[str, Path]
_TRUE_VALUES = {"1", "true", "yes", "on"}
_DISABLE_ONNX_GPU = False


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def set_onnx_gpu_disabled(disabled: bool) -> None:
    """Set process-wide ONNX GPU disable switch."""
    global _DISABLE_ONNX_GPU
    _DISABLE_ONNX_GPU = bool(disabled)


def is_onnx_gpu_disabled() -> bool:
    """Return True when ONNX GPU acceleration is disabled by config or env."""
    return _DISABLE_ONNX_GPU or _is_truthy(os.getenv("MT_DISABLE_ONNX_GPU", ""))


def import_onnxruntime(import_error_message: str = ""):
    """Import onnxruntime with a caller-provided error message."""
    try:
        # Crucial for Windows: add OpenVINO libs directory to search path before importing ORT
        if os.name == 'nt':
            try:
                import openvino
                import ctypes
                ov_path = Path(openvino.__file__).parent
                libs_dir = ov_path / "libs"
                if libs_dir.exists():
                    # 1. Add to Python's DLL directory list
                    try:
                        os.add_dll_directory(str(libs_dir))
                    except AttributeError:
                        pass
                    # 2. Set Win32 LoadLibrary search path
                    try:
                        ctypes.windll.kernel32.SetDllDirectoryW(str(libs_dir))
                    except Exception:
                        pass
                    # 3. Prepend to environment PATH
                    os.environ["PATH"] = str(libs_dir) + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass

        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        if import_error_message:
            raise ImportError(import_error_message) from exc
        raise
    return ort



def create_session_options(
    ort: Any,
    *,
    log_severity_level: int = 3,
    enable_mem_pattern: Optional[bool] = None,
    enable_cpu_mem_arena: Optional[bool] = None,
    intra_op_num_threads: Optional[int] = None,
    inter_op_num_threads: Optional[int] = None,
) -> Any:
    """Create SessionOptions using ONNX Runtime official defaults plus overrides."""
    sess_options = ort.SessionOptions()
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_options.log_severity_level = log_severity_level

    if enable_mem_pattern is not None:
        sess_options.enable_mem_pattern = bool(enable_mem_pattern)
    if enable_cpu_mem_arena is not None:
        sess_options.enable_cpu_mem_arena = bool(enable_cpu_mem_arena)
    if intra_op_num_threads is not None:
        sess_options.intra_op_num_threads = int(intra_op_num_threads)
    if inter_op_num_threads is not None:
        sess_options.inter_op_num_threads = int(inter_op_num_threads)

    return sess_options


def _provider_has_gpu(provider: Any) -> bool:
    if isinstance(provider, str):
        return provider in ("CUDAExecutionProvider", "DmlExecutionProvider")
    if isinstance(provider, (tuple, list)) and provider:
        return provider[0] in ("CUDAExecutionProvider", "DmlExecutionProvider")
    return False


def build_execution_providers(
    ort: Any,
    *,
    device: str = "cpu",
    cuda_options: Optional[Mapping[str, Any]] = None,
    disable_onnx_gpu: Optional[bool] = None,
    include_cpu: bool = True,
    preload_cuda_dlls: bool = True,
    logger: Any = None,
) -> list[Any]:
    """
    Build providers in ONNX Runtime official format:
    [("DmlExecutionProvider", {}), ("CUDAExecutionProvider", {...}), "CPUExecutionProvider"].
    """
    normalized = (device or "cpu").lower()
    wants_gpu = any(normalized.startswith(prefix) for prefix in ("cuda", "gpu", "hip", "dml", "directml"))
    onnx_gpu_disabled = is_onnx_gpu_disabled()
    if disable_onnx_gpu is not None:
        onnx_gpu_disabled = bool(disable_onnx_gpu) or onnx_gpu_disabled
    providers: list[Any] = []

    if wants_gpu and onnx_gpu_disabled:
        if logger is not None:
            logger.info("ONNX GPU acceleration disabled by switch; forcing CPUExecutionProvider.")
        wants_gpu = False

    if wants_gpu:
        if preload_cuda_dlls and hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls()
            except Exception as exc:
                if logger is not None:
                    logger.warning(f"onnxruntime.preload_dlls() failed: {exc}")

        available_providers: Sequence[str] = []
        try:
            available_providers = ort.get_available_providers()
        except Exception as exc:
            if logger is not None:
                logger.warning(f"Failed to query ONNX Runtime providers: {exc}")

        # 优先检测并使用微软 DirectML 加速
        if "DmlExecutionProvider" in available_providers:
            providers.append(("DmlExecutionProvider", {"device_id": 0}))
        elif "CUDAExecutionProvider" in available_providers:
            options = {"device_id": 0}
            if cuda_options:
                options.update(dict(cuda_options))
            providers.append(("CUDAExecutionProvider", options))
        elif logger is not None:
            logger.warning(f"GPU execution provider not available, fallback to CPU: {available_providers}")

    if include_cpu or not providers:
        available_providers = []
        try:
            available_providers = ort.get_available_providers()
        except Exception:
            pass
        if "OpenVINOExecutionProvider" in available_providers:
            providers.append(("OpenVINOExecutionProvider", {
                "device_type": "CPU",
                "num_of_threads": 8
            }))
        providers.append("CPUExecutionProvider")

    return providers


def create_inference_session(
    ort: Any,
    model_path: ModelPath,
    *,
    device: str = "cpu",
    sess_options: Any,
    cuda_options: Optional[Mapping[str, Any]] = None,
    disable_onnx_gpu: Optional[bool] = None,
    logger: Any = None,
    fallback_to_cpu: bool = True,
    preload_cuda_dlls: bool = True,
) -> tuple[Any, str]:
    """
    Create InferenceSession with standard provider order and optional GPU->CPU fallback.
    Returns (session, active_device), where active_device is 'cuda', 'dml', or 'cpu'.
    """
    providers = build_execution_providers(
        ort,
        device=device,
        cuda_options=cuda_options,
        disable_onnx_gpu=disable_onnx_gpu,
        include_cpu=True,
        preload_cuda_dlls=preload_cuda_dlls,
        logger=logger,
    )

    model_path_str = str(model_path)
    tried_gpu = any(_provider_has_gpu(provider) for provider in providers)

    try:
        session = ort.InferenceSession(model_path_str, sess_options=sess_options, providers=providers)
    except Exception as exc:
        if fallback_to_cpu and tried_gpu:
            if logger is not None:
                logger.warning(f"GPU session creation failed, fallback to CPU: {exc}")
            session = ort.InferenceSession(
                model_path_str,
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            )
        else:
            raise

    active_providers = session.get_providers()
    if "CUDAExecutionProvider" in active_providers:
        active_device = "cuda"
    elif "DmlExecutionProvider" in active_providers:
        active_device = "dml"
    elif "OpenVINOExecutionProvider" in active_providers:
        active_device = "openvino"
    else:
        active_device = "cpu"
    return session, active_device
