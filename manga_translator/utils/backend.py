import os
import sys
import logging
from typing import List, Tuple, Optional


def _get_logger(name: str = "Backend") -> logging.Logger:
    try:
        # Lazy import project logger utility if available
        from .log import get_logger  # type: ignore
        return get_logger(name)
    except Exception:
        logger = logging.getLogger(name)
        if not logger.handlers:
            logging.basicConfig(level=logging.INFO)
        return logger


def is_windows() -> bool:
    return sys.platform.startswith("win")


def import_onnxruntime():
    try:
        import onnxruntime as ort  # type: ignore
        return ort
    except Exception as e:
        return None


def ort_available_providers() -> List[str]:
    ort = import_onnxruntime()
    if not ort:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:
        # Some old versions expose it on InferenceSession
        try:
            return list(ort.InferenceSession.get_available_providers())  # type: ignore[attr-defined]
        except Exception:
            return []


def log_ort_environment(logger: Optional[logging.Logger] = None) -> None:
    logger = logger or _get_logger("Backend")
    ort = import_onnxruntime()
    if not ort:
        logger.warning("onnxruntime is not installed. For Windows 11 + AMD RDNA GPUs, install onnxruntime-directml: pip install onnxruntime-directml")
        return
    try:
        version = getattr(ort, "__version__", "unknown")
    except Exception:
        version = "unknown"
    providers = ort_available_providers()
    logger.info(f"ONNX Runtime version: {version}")
    logger.info(f"ONNX Runtime available providers: {providers}")


class OnnxProviderChoice(Tuple[List[str], Optional[List[dict]]]):
    pass


def _env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    return v if (v is not None and v != "") else default


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    try:
        v = os.environ.get(name)
        if v is None or v == "":
            return default
        return int(v)
    except Exception:
        return default


def select_onnx_providers(
    prefer: str = "auto",
    device_hint: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Tuple[List[str], Optional[List[dict]], str]:
    """
    Decide ONNX Runtime providers with fallback.

    - prefer: 'auto' | 'dml' | 'cuda' | 'cpu'
    - device_hint: hint from outer pipeline ('cuda'|'cpu'|'mps' etc.)

    Returns (providers, provider_options, selected_backend)
    """
    logger = logger or _get_logger("Backend")

    # Env overrides
    env_force = (_env_str("MT_ONNX_BACKEND") or _env_str("MANGA_ONNX_BACKEND") or _env_str("MT_FORCE_ONNX_BACKEND"))
    env_device_id = _env_int("MT_DML_DEVICE_ID", _env_int("MANGA_DML_DEVICE_ID"))

    if env_force:
        prefer = env_force.lower()

    # Normalize prefer
    prefer = (prefer or "auto").lower()

    _providers = ort_available_providers()

    # Desired order based on hint and platform
    order: List[str] = []
    if prefer == "auto":
        # If hint says CUDA, prefer CUDA; else on Windows prefer DML; then CUDA; then CPU
        if (device_hint or "").startswith("cuda") and "CUDAExecutionProvider" in _providers:
            order = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            if is_windows():
                order = ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
            else:
                order = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    elif prefer == "dml":
        order = ["DmlExecutionProvider", "CPUExecutionProvider"]
    elif prefer == "cuda":
        order = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    else:
        order = ["CPUExecutionProvider"]

    selected = None
    providers: List[str] = []
    provider_options: Optional[List[dict]] = None

    for p in order:
        if p in _providers:
            selected = p
            providers = [p, "CPUExecutionProvider"] if p != "CPUExecutionProvider" else [p]
            # Configure provider options when possible (device id for DML)
            if p == "DmlExecutionProvider" and env_device_id is not None:
                provider_options = [{"device_id": int(env_device_id)}, {}]
            elif p == "CUDAExecutionProvider":
                # Could add device_id for CUDA in the future
                provider_options = None
            else:
                provider_options = None
            break

    if not selected:
        logger.warning("No preferred GPU provider available. Using CPUExecutionProvider.")
        providers = ["CPUExecutionProvider"]
        selected = "CPUExecutionProvider"
        provider_options = None

    # Log decision and reason
    reason = f"prefer={prefer}, hint={device_hint}, env_force={env_force}, device_id={env_device_id}"
    logger.info(f"ONNX Runtime backend selected: {selected} ({reason})")
    if selected == "DmlExecutionProvider" and not is_windows():
        logger.info("DML selected on non-Windows platform (unexpected). Ensure this is intended.")

    return providers, provider_options, selected


def create_onnx_session(model_path: str, logger: Optional[logging.Logger] = None, prefer: str = "auto", device_hint: Optional[str] = None):
    """
    Create an ONNX Runtime session with DML/CUDA/CPU fallback and helpful logging.
    """
    logger = logger or _get_logger("Backend")
    ort = import_onnxruntime()
    if not ort:
        logger.error("onnxruntime is not installed. Install 'onnxruntime-directml' on Windows 11 for AMD GPUs or 'onnxruntime-gpu' for NVIDIA.")
        raise ImportError("onnxruntime not installed")

    log_ort_environment(logger)
    providers, provider_options, selected = select_onnx_providers(prefer=prefer, device_hint=device_hint, logger=logger)

    # Try to construct session with provider options when supported
    try:
        if provider_options is not None:
            sess = ort.InferenceSession(model_path, providers=providers, provider_options=provider_options)
        else:
            sess = ort.InferenceSession(model_path, providers=providers)
        return sess, selected
    except TypeError:
        # Older ORT versions may not accept provider_options in constructor
        try:
            sess = ort.InferenceSession(model_path, providers=providers)
            if provider_options is not None and hasattr(sess, "set_providers"):
                try:
                    sess.set_providers(providers, provider_options)  # type: ignore[attr-defined]
                except Exception:
                    pass
            return sess, selected
        except Exception as e:
            logger.error(f"Failed to create ONNX Runtime session with providers {providers}: {e}")
            # Final fallback: let ORT decide default providers
            sess = ort.InferenceSession(model_path)
            return sess, "auto"


def recommend_installation_message() -> str:
    msg = (
        "Windows 11 + AMD RDNA4 detected or GPU acceleration requested.\n"
        "Install ONNX Runtime (DirectML) for GPU acceleration: pip install onnxruntime-directml\n"
        "Optional (PyTorch path): pip install torch-directml  # only for compatible torch modules\n"
        "If you encounter provider issues, set MT_ONNX_BACKEND=cpu to force CPU.\n"
        "You can also set MT_DML_DEVICE_ID to choose a specific GPU."
    )
    return msg


def startup_backend_selfcheck(logger: Optional[logging.Logger] = None) -> None:
    """Print startup self-check and guidance messages."""
    logger = logger or _get_logger("Backend")
    try:
        log_ort_environment(logger)
        providers, _, selected = select_onnx_providers(logger=logger)
        logger.info(f"Backend auto-selection result: {selected} -> providers={providers}")
    except Exception as e:
        logger.debug(f"ONNX Runtime backend self-check skipped: {e}")

    if is_windows():
        # Provide helpful hints for Windows users
        logger.info("If DirectML is not available, install onnxruntime-directml. To force CPU: set MT_ONNX_BACKEND=cpu")
