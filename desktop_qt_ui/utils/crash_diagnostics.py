"""Windows 原生崩溃（如 0xC0000005）退出后自动诊断工具。

仅使用 Python 标准库，确保在 PyTorch / PyQt6 / CUDA 等底层依赖异常时依然可以稳定运行。
由 Win-Start.bat 在主程序异常退出时调用：
1. 识别 Windows NTSTATUS 崩溃退出码（如 0xC0000005 / 0xC0000409 / 0xC0000135 等）。
2. 查询 Windows 应用程序事件日志（Event ID 1000 / Application Error）提取故障模块名与路径。
3. 检测本机正在运行的已知冲突注入服务/覆盖层进程（如 NahimicService、RTSS、Sonic Studio 等）。
4. 输出结构化诊断信息与分场景处理建议；检测到注入冲突且未启用扩展点缓解时返回退出码 10，
   供 Win-Start.bat 提示用户可临时带 MT_ENABLE_EXTENSION_POINT_MITIGATION=1 重试一次。
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import locale
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


EXIT_NORMAL = 0
EXIT_INJECTION_SUSPECTED = 10

_XML_INVALID_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_NTSTATUS_DESCRIPTIONS: dict[int, tuple[str, str, str]] = {
    0xC0000005: (
        "STATUS_ACCESS_VIOLATION",
        "内存访问违例（常见于第三方覆盖层/Hook 注入、显卡驱动或 PyTorch/Qt 冲突）",
        "Memory access violation (often caused by third-party overlay/hook injection, GPU driver, or PyTorch/Qt conflict)",
    ),
    0xC0000409: (
        "STATUS_STACK_BUFFER_OVERRUN",
        "栈缓冲区溢出 / 运行时致命中止（常见于第三方 Hook 注入或底层 C/C++ 异常）",
        "Stack buffer overrun / fast-fail abort (often caused by third-party hook injection or native C/C++ abort)",
    ),
    0xC0000374: (
        "STATUS_HEAP_CORRUPTION",
        "堆内存损坏（常见于第三方 DLL 注入或底层扩展冲突）",
        "Heap corruption (often caused by third-party DLL injection or native extension conflict)",
    ),
    0xC000001D: (
        "STATUS_ILLEGAL_INSTRUCTION",
        "非法 CPU 指令（常见于较旧 CPU 不支持 AVX2 等指令集）",
        "Illegal CPU instruction (often caused by an older CPU lacking AVX2 support)",
    ),
    0xC00000FD: (
        "STATUS_STACK_OVERFLOW",
        "栈溢出",
        "Stack overflow",
    ),
    0xC0000135: (
        "STATUS_DLL_NOT_FOUND",
        "缺少必需的系统 DLL（通常缺少 Microsoft Visual C++ 2015-2022 运行库）",
        "Required DLL not found (commonly missing Microsoft Visual C++ 2015-2022 Redistributable)",
    ),
    0xC0000139: (
        "STATUS_ENTRYPOINT_NOT_FOUND",
        "DLL 入口点不匹配（常见于运行库或依赖版本混装冲突）",
        "DLL entry point mismatch (commonly caused by mismatched runtime or mixed package versions)",
    ),
    0xC000007B: (
        "STATUS_INVALID_IMAGE_FORMAT",
        "无效镜像格式（32 位 / 64 位 DLL 混装）",
        "Invalid image format (32-bit / 64-bit DLL mismatch)",
    ),
}

_HOOK_SUSPECT_CODES = frozenset({0xC0000005, 0xC0000409, 0xC0000374})

_INJECTION_MODULE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("nahimicosd", "Nahimic / A-Volute Audio OSD"),
    ("nahimic", "Nahimic Audio Suite"),
    ("a-volute", "A-Volute / Nahimic Audio Suite"),
    ("audiodevprops", "Nahimic / A-Volute Audio Suite"),
    ("ss3devprops", "ASUS Sonic Studio / A-Volute"),
    ("ss2devprops", "ASUS Sonic Studio / A-Volute"),
    ("ss3osd", "ASUS Sonic Studio OSD"),
    ("sonicstudio", "ASUS Sonic Studio"),
    ("sonicradar", "ASUS Sonic Radar"),
    ("rtsshooks", "RivaTuner Statistics Server (RTSS)"),
    ("gameoverlayrenderer", "Steam Game Overlay"),
    ("discordhook", "Discord Overlay"),
    ("overwolf", "Overwolf Overlay"),
    ("owclient", "Overwolf Overlay"),
    ("ow-graphics", "Overwolf Overlay"),
    ("graphics-hook", "OBS / Screen Capture Hook"),
    ("obs-graphics-hook", "OBS Studio Capture Hook"),
    ("bdcam", "Bandicam Capture Hook"),
    ("fraps", "Fraps Overlay"),
    ("specialk", "Special K Overlay"),
    ("reshade", "ReShade Hook"),
    ("d3dgear", "D3DGear Overlay"),
    ("fpsmon", "FPS Monitor Overlay"),
    ("nvspcap", "NVIDIA GeForce Experience Overlay"),
    ("mactype", "MacType Font Renderer"),
    ("gdipp", "gdipp Font Renderer"),
)

_INJECTION_PATH_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("a-volute", "Nahimic / A-Volute Audio OSD"),
    ("nahimic", "Nahimic / A-Volute Audio OSD"),
    ("sonicstudio", "ASUS Sonic Studio"),
    ("sonicradar", "ASUS Sonic Radar"),
    ("rivatuner", "RivaTuner Statistics Server (RTSS)"),
    ("overwolf", "Overwolf Overlay"),
    ("mactype", "MacType Font Renderer"),
)

_TORCH_GPU_MODULE_PREFIXES: tuple[str, ...] = (
    "c10",
    "torch_cpu",
    "torch_cuda",
    "torch_python",
    "fbgemm",
    "asmjit",
    "cudnn",
    "cublas",
    "curand",
    "cusolver",
    "cusparse",
    "cufft",
    "nvrtc",
    "nvcuda",
    "nvoglv",
    "nvwgf2um",
    "nvgpucomp",
    "nvldumdx",
    "aticfx",
    "atio6axx",
    "amdxc",
    "amdvlk",
    "igxelpicd",
    "igc64",
    "igd10",
    "igd12",
    "onnxruntime",
    "directml",
)

_QT_MODULE_PREFIXES: tuple[str, ...] = (
    "qt6",
    "qt5",
    "pyqt6",
    "qwindows",
)

_KNOWN_CONFLICT_SERVICES: tuple[tuple[str, str], ...] = (
    ("NahimicService", "Nahimic / A-Volute Audio OSD (NahimicService)"),
    ("AVoluteSS3Vad", "ASUS Sonic Studio / A-Volute (AVoluteSS3Vad)"),
    ("SS3Svc32", "ASUS Sonic Studio (SS3Svc32)"),
    ("SS3Svc64", "ASUS Sonic Studio (SS3Svc64)"),
    ("SonicStudioService", "ASUS Sonic Studio (SonicStudioService)"),
)

_KNOWN_CONFLICT_PROCESSES: tuple[tuple[str, str], ...] = (
    ("nahimicsvc64.exe", "Nahimic / A-Volute Audio OSD"),
    ("nahimicsvc32.exe", "Nahimic / A-Volute Audio OSD"),
    ("nahimic3.exe", "Nahimic / A-Volute Audio OSD"),
    ("ss3svc64.exe", "ASUS Sonic Studio / Sonic Radar"),
    ("ss3svc32.exe", "ASUS Sonic Studio / Sonic Radar"),
    ("sonicradar3.exe", "ASUS Sonic Studio / Sonic Radar"),
    ("rtss.exe", "RivaTuner Statistics Server (RTSS)"),
    ("rtsshooksloader64.exe", "RivaTuner Statistics Server (RTSS)"),
    ("rtsshooksloader.exe", "RivaTuner Statistics Server (RTSS)"),
    ("mactype.exe", "MacType Font Renderer"),
    ("mactray.exe", "MacType Font Renderer"),
    ("fpsmonitor.exe", "FPS Monitor"),
)


@dataclass(frozen=True)
class CrashEvent:
    app_name: str
    app_path: str
    module_name: str
    module_path: str
    module_version: str
    exception_code: str
    fault_offset: str
    time_created: str


@dataclass(frozen=True)
class DiagnosisResult:
    exit_code: int
    ntstatus: int
    is_native_crash: bool
    event: CrashEvent | None
    category: str
    matched_vendor: str | None
    running_conflicts: tuple[str, ...]
    mitigation_already_enabled: bool

    @property
    def injection_suspected(self) -> bool:
        if not self.is_native_crash:
            return False
        if self.category == "injection":
            return True
        if (
            self.ntstatus in _HOOK_SUSPECT_CODES
            and self.category != "torch_gpu"
            and bool(self.running_conflicts)
        ):
            return True
        return False


def to_ntstatus(exit_code: int) -> int:
    return exit_code & 0xFFFFFFFF


def is_native_crash_exit_code(exit_code: int) -> bool:
    code = to_ntstatus(exit_code)
    return 0xC0000000 <= code <= 0xCFFFFFFF


def _normalize_exception_code(raw: str) -> str:
    cleaned = (raw or "").strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    return cleaned.zfill(8) if cleaned else ""


def _decode_command_output(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("mbcs", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def parse_application_error_events(xml_text: str) -> list[CrashEvent]:
    """解析 wevtutil 输出的 Event ID 1000 XML（兼容 Win10 位置字段与 Win11 命名字段）。"""
    if not xml_text or not xml_text.strip():
        return []

    sanitized = _XML_INVALID_CHARS_RE.sub("", xml_text)
    wrapped = f"<Events>{sanitized}</Events>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        return []

    events: list[CrashEvent] = []
    for elem in root:
        if not elem.tag.endswith("Event"):
            continue
        time_created = ""
        named: dict[str, str] = {}
        positional: list[str] = []

        for child in elem:
            if child.tag.endswith("System"):
                for sys_child in child:
                    if sys_child.tag.endswith("TimeCreated"):
                        time_created = sys_child.attrib.get("SystemTime", "")
            elif child.tag.endswith("EventData"):
                for data_elem in child:
                    if not data_elem.tag.endswith("Data"):
                        continue
                    text = (data_elem.text or "").strip()
                    positional.append(text)
                    name = data_elem.attrib.get("Name")
                    if name:
                        named[name] = text

        def _get(name: str, idx: int) -> str:
            if name in named:
                return named[name]
            if 0 <= idx < len(positional):
                return positional[idx]
            return ""

        app_name = _get("AppName", 0)
        module_name = _get("ModuleName", 3)
        exception_code = _normalize_exception_code(_get("ExceptionCode", 6))
        if not app_name and not module_name:
            continue

        events.append(
            CrashEvent(
                app_name=app_name,
                app_path=_get("AppPath", 10),
                module_name=module_name,
                module_path=_get("ModulePath", 11),
                module_version=_get("ModuleVersion", 4),
                exception_code=exception_code,
                fault_offset=_get("FaultingOffset", 7),
                time_created=time_created,
            )
        )
    return events


def select_matching_event(
    events: list[CrashEvent],
    exe_path: str | None,
    exit_code: int,
) -> CrashEvent | None:
    """从最近的应用崩溃事件中选择最匹配本次退出进程与异常码的记录。"""
    if not events:
        return None

    expected_exc = f"{to_ntstatus(exit_code):08x}"
    expected_app_names: set[str] = set()
    preferred_paths: set[str] = set()

    if exe_path:
        base = os.path.basename(exe_path).lower()
        if base:
            expected_app_names.add(base)
        if base in ("python.exe", "pythonw.exe"):
            expected_app_names.update(("python.exe", "pythonw.exe"))
        try:
            preferred_paths.add(os.path.normcase(os.path.abspath(exe_path)))
            preferred_paths.add(os.path.normcase(os.path.realpath(exe_path)))
        except OSError:
            pass
    else:
        expected_app_names.update(("python.exe", "pythonw.exe", "app.exe"))

    candidates: list[CrashEvent] = []
    for ev in events:
        if expected_app_names and ev.app_name.lower() not in expected_app_names:
            continue
        if ev.exception_code and ev.exception_code != expected_exc:
            continue
        candidates.append(ev)

    if not candidates:
        return None

    if preferred_paths:
        for ev in candidates:
            if ev.app_path and os.path.normcase(ev.app_path) in preferred_paths:
                return ev

    return candidates[0]


def query_recent_crash_event(
    exe_path: str | None,
    exit_code: int,
    window_seconds: int = 120,
    retry_delay: float = 0.5,
) -> CrashEvent | None:
    if sys.platform != "win32":
        return None

    window_ms = max(1000, int(window_seconds * 1000))
    query = (
        f"*[System[(EventID=1000) and Provider[@Name='Application Error'] "
        f"and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    )
    cmd = ["wevtutil", "qe", "Application", f"/q:{query}", "/c:10", "/rd:true", "/f:xml"]

    attempts = 2 if retry_delay > 0 else 1
    for attempt in range(attempts):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=3.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                xml_text = _decode_command_output(proc.stdout)
                events = parse_application_error_events(xml_text)
                matched = select_matching_event(events, exe_path, exit_code)
                if matched is not None:
                    return matched
        except (OSError, subprocess.TimeoutExpired):
            return None

        if attempt + 1 < attempts:
            time.sleep(retry_delay)

    return None


def classify_fault_module(
    module_name: str,
    module_path: str = "",
) -> tuple[str, str | None]:
    """将故障模块归类为 injection / torch_gpu / qt / system / unknown。"""
    name = (module_name or "").strip().lower()
    path = (module_path or "").strip().lower()

    if not name and not path:
        return "unknown", None

    for pattern, vendor in _INJECTION_MODULE_PATTERNS:
        if pattern in name:
            return "injection", vendor

    for keyword, vendor in _INJECTION_PATH_KEYWORDS:
        if keyword in path:
            return "injection", vendor

    for prefix in _TORCH_GPU_MODULE_PREFIXES:
        if name.startswith(prefix):
            return "torch_gpu", None

    if any(seg in path for seg in ("\\torch\\lib\\", "\\onnxruntime\\", "\\nvidia\\", "\\amd\\")):
        return "torch_gpu", None

    for prefix in _QT_MODULE_PREFIXES:
        if name.startswith(prefix):
            return "qt", None

    if name in (
        "ntdll.dll",
        "kernelbase.dll",
        "kernel32.dll",
        "ucrtbase.dll",
        "msvcrt.dll",
        "opengl32.dll",
        "d3d11.dll",
        "dxgi.dll",
        "user32.dll",
        "gdi32.dll",
        "combase.dll",
        "ole32.dll",
        "python312.dll",
        "python3.dll",
    ) or name.startswith(("vcruntime", "msvcp")):
        return "system", None

    return "other", None


def _query_running_services() -> list[str]:
    """通过 Win32 SCM 只读检测已知冲突服务是否处于运行中。"""
    if sys.platform != "win32":
        return []

    found: list[str] = []
    try:
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        class SERVICE_STATUS(ctypes.Structure):
            _fields_ = [
                ("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD),
            ]

        advapi32.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        advapi32.OpenSCManagerW.restype = wintypes.HANDLE
        advapi32.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
        advapi32.OpenServiceW.restype = wintypes.HANDLE
        advapi32.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
        advapi32.QueryServiceStatus.restype = wintypes.BOOL
        advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]
        advapi32.CloseServiceHandle.restype = wintypes.BOOL

        SC_MANAGER_CONNECT = 0x0001
        SERVICE_QUERY_STATUS = 0x0004
        SERVICE_RUNNING = 0x0004

        scm = advapi32.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
        if not scm:
            return []
        try:
            status = SERVICE_STATUS()
            for svc_name, label in _KNOWN_CONFLICT_SERVICES:
                svc = advapi32.OpenServiceW(scm, svc_name, SERVICE_QUERY_STATUS)
                if not svc:
                    continue
                try:
                    if advapi32.QueryServiceStatus(svc, ctypes.byref(status)):
                        if status.dwCurrentState == SERVICE_RUNNING:
                            found.append(label)
                finally:
                    advapi32.CloseServiceHandle(svc)
        finally:
            advapi32.CloseServiceHandle(scm)
    except Exception:
        return []

    return found


def _query_running_processes() -> list[str]:
    """通过 Win32 Toolhelp 快照只读检测已知覆盖层/注入进程。"""
    if sys.platform != "win32":
        return []

    found: list[str] = []
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        proc_map = dict(_KNOWN_CONFLICT_PROCESSES)
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                exe_name = (entry.szExeFile or "").lower()
                if exe_name in proc_map:
                    found.append(proc_map[exe_name])
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return []

    return found


def detect_running_overlay_conflicts() -> tuple[str, ...]:
    """检测本机正在运行的已知冲突服务/覆盖层进程并按名称去重。"""
    results: list[str] = []
    seen_groups: set[str] = set()

    for item in (*_query_running_services(), *_query_running_processes()):
        group_key = item.split("(")[0].strip().lower()
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        results.append(item)

    return tuple(results)


def is_extension_point_mitigation_enabled(project_root: str | None = None) -> bool:
    """检查当前环境或 .env 中是否已启用 MT_ENABLE_EXTENSION_POINT_MITIGATION。"""
    val = os.environ.get("MT_ENABLE_EXTENSION_POINT_MITIGATION")
    if val is not None:
        return val.strip().lower() in ("1", "true", "yes", "on")

    root = project_root or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return False

    try:
        from dotenv import dotenv_values

        file_val = dotenv_values(env_path, encoding="utf-8").get(
            "MT_ENABLE_EXTENSION_POINT_MITIGATION"
        )
        if file_val is not None:
            return str(file_val).strip().lower() in ("1", "true", "yes", "on")
        return False
    except Exception:
        pass

    # 回退到纯标准库简易 .env 解析，避免 python-dotenv 不可用时出错（同名键以后者覆盖前者）
    try:
        last_val: str | None = None
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() != "MT_ENABLE_EXTENSION_POINT_MITIGATION":
                    continue
                last_val = v.split("#", 1)[0].strip().strip("\"'")
        if last_val is not None:
            return last_val.lower() in ("1", "true", "yes", "on")
    except OSError:
        pass
    return False


def _prefer_chinese() -> bool:
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetUserDefaultUILanguage.restype = ctypes.c_ushort
            lang_id = kernel32.GetUserDefaultUILanguage()
            # Primary language ID 0x04 = LANG_CHINESE
            return (lang_id & 0xFF) == 0x04
        except Exception:
            pass
    try:
        lang, _ = locale.getlocale()
        if lang and lang.lower().startswith(("zh", "chinese")):
            return True
    except Exception:
        pass
    return False


def diagnose_crash(
    exit_code: int,
    exe_path: str | None = None,
    window_seconds: int = 120,
    retry_delay: float = 0.5,
    project_root: str | None = None,
) -> DiagnosisResult:
    ntstatus = to_ntstatus(exit_code)
    native = is_native_crash_exit_code(exit_code)
    mitigation_on = is_extension_point_mitigation_enabled(project_root=project_root)

    if not native:
        return DiagnosisResult(
            exit_code=exit_code,
            ntstatus=ntstatus,
            is_native_crash=False,
            event=None,
            category="non_native",
            matched_vendor=None,
            running_conflicts=(),
            mitigation_already_enabled=mitigation_on,
        )

    event = query_recent_crash_event(
        exe_path=exe_path,
        exit_code=exit_code,
        window_seconds=window_seconds,
        retry_delay=retry_delay,
    )
    if event is not None:
        category, vendor = classify_fault_module(event.module_name, event.module_path)
    else:
        category, vendor = "unknown", None

    conflicts: tuple[str, ...] = ()
    if ntstatus in _HOOK_SUSPECT_CODES and category != "torch_gpu":
        conflicts = detect_running_overlay_conflicts()

    return DiagnosisResult(
        exit_code=exit_code,
        ntstatus=ntstatus,
        is_native_crash=True,
        event=event,
        category=category,
        matched_vendor=vendor,
        running_conflicts=conflicts,
        mitigation_already_enabled=mitigation_on,
    )


def format_diagnosis_report(result: DiagnosisResult, zh: bool | None = None) -> str:
    if not result.is_native_crash:
        return ""

    use_zh = _prefer_chinese() if zh is None else zh
    nt_hex = f"0x{result.ntstatus:08X}"
    status_info = _NTSTATUS_DESCRIPTIONS.get(result.ntstatus)

    lines: list[str] = []
    sep = "=" * 60
    lines.append("")
    lines.append(sep)
    if use_zh:
        lines.append("[崩溃自动诊断] 检测到 Windows 原生异常退出")
    else:
        lines.append("[CRASH DIAGNOSIS] Windows native crash detected")
    lines.append(sep)

    if status_info:
        sym, desc_zh, desc_en = status_info
        desc = desc_zh if use_zh else desc_en
        label = "异常代码" if use_zh else "Exception"
        lines.append(f"  {label}: {result.exit_code} ({nt_hex} / {sym})")
        lines.append(f"  {'说明' if use_zh else 'Meaning'}: {desc}")
    else:
        label = "异常代码" if use_zh else "Exception"
        lines.append(f"  {label}: {result.exit_code} ({nt_hex})")

    if result.event is not None:
        ev = result.event
        if use_zh:
            lines.append(f"  故障模块: {ev.module_name or '(未知)'}")
            if ev.module_path:
                lines.append(f"  模块路径: {ev.module_path}")
        else:
            lines.append(f"  Fault Module: {ev.module_name or '(unknown)'}")
            if ev.module_path:
                lines.append(f"  Module Path:  {ev.module_path}")
    else:
        if use_zh:
            lines.append("  故障模块: 未在最近 Windows 应用程序事件日志中找到匹配记录")
        else:
            lines.append("  Fault Module: No matching Windows Application Error event found")

    if result.running_conflicts:
        conflict_text = ", ".join(result.running_conflicts)
        if use_zh:
            lines.append(f"  检测到运行中的常见冲突组件: {conflict_text}")
        else:
            lines.append(f"  Running known overlay/hook software: {conflict_text}")

    lines.append("")
    if use_zh:
        lines.append("[建议处理方案]")
    else:
        lines.append("[Recommended Actions]")

    if result.category == "injection":
        vendor = result.matched_vendor or (result.event.module_name if result.event else "Third-party overlay")
        vendor_lower = vendor.lower()
        is_nahimic = any(k in vendor_lower for k in ("nahimic", "a-volute", "sonic studio", "sonic radar"))
        if use_zh:
            lines.append(f"  * 已定位到第三方覆盖层/注入模块崩溃：{vendor}")
            if is_nahimic:
                lines.append("  1. [推荐，不影响中文输入法] 关闭 Nahimic / Sonic Studio 的“声音追踪器”或 OSD，或在 services.msc 中停止/禁用 NahimicService，")
                lines.append("     或将 python.exe 加入 C:\\ProgramData\\A-Volute\\...\\BlackApps.dat 排除列表。")
            else:
                lines.append(f"  1. [推荐，不影响中文输入法] 暂时关闭 {vendor} 的悬浮窗/注入功能，或将 python.exe 加入其排除列表后重启程序。")
            if not result.mitigation_already_enabled:
                lines.append("  2. [临时应急方案] 在 .env 中添加 MT_ENABLE_EXTENSION_POINT_MITIGATION=1（注意：可能导致部分第三方中文输入法无法输入）。")
            else:
                lines.append("  2. 当前已启用 MT_ENABLE_EXTENSION_POINT_MITIGATION=1 仍发生崩溃，请直接关闭上述外部覆盖层服务。")
        else:
            lines.append(f"  * Confirmed third-party overlay/injection crash: {vendor}")
            if is_nahimic:
                lines.append("  1. [Recommended, preserves IME] Disable Sound Tracker / OSD in Nahimic or Sonic Studio, stop/disable NahimicService in services.msc,")
                lines.append("     or add python.exe to C:\\ProgramData\\A-Volute\\...\\BlackApps.dat.")
            else:
                lines.append(f"  1. [Recommended, preserves IME] Disable the overlay/hook feature in {vendor} or add python.exe to its exclusion list, then retry.")
            if not result.mitigation_already_enabled:
                lines.append("  2. [Temporary fallback] Add MT_ENABLE_EXTENSION_POINT_MITIGATION=1 to .env (note: may break third-party CJK IMEs).")
            else:
                lines.append("  2. MT_ENABLE_EXTENSION_POINT_MITIGATION=1 is already enabled; please disable the overlay software/service directly.")

    elif result.category == "torch_gpu":
        if use_zh:
            lines.append("  * 故障模块指向 PyTorch / CUDA / ONNX 或显卡驱动，而非外部输入法/覆盖层注入。")
            lines.append("  1. 更新显卡驱动，确认当前 PyTorch 后端（CUDA/CPU/ROCm）与硬件匹配。")
            lines.append("  2. 运行 Win-Install-or-Update.bat 选择 [1] Install 修复依赖，或检查 result\\log_*.txt。")
        else:
            lines.append("  * Fault points to PyTorch / CUDA / ONNX or GPU driver.")
            lines.append("  1. Update your GPU driver and confirm the installed PyTorch backend matches your hardware.")
            lines.append("  2. Run Win-Install-or-Update.bat -> [1] Install to repair dependencies, and inspect result\\log_*.txt.")

    elif result.ntstatus in (0xC0000135, 0xC0000139, 0xC000007B):
        if use_zh:
            lines.append("  1. 安装或修复最新版 Microsoft Visual C++ 2015-2022 Redistributable (x64)。")
            lines.append("  2. 运行 Win-Install-or-Update.bat 选择 [1] Install 检查并修复运行环境。")
        else:
            lines.append("  1. Install or repair Microsoft Visual C++ 2015-2022 Redistributable (x64).")
            lines.append("  2. Run Win-Install-or-Update.bat -> [1] Install to repair the runtime environment.")

    elif result.ntstatus == 0xC000001D:
        if use_zh:
            lines.append("  1. 当前 CPU 可能不支持部分二进制依赖所需的指令集（如 AVX2）。")
            lines.append("  2. 请查看 result\\log_*.txt 并在 Issue 中附带 CPU 型号与故障模块信息。")
        else:
            lines.append("  1. Your CPU may lack required instruction set extensions (such as AVX2).")
            lines.append("  2. Check result\\log_*.txt and include your CPU model and fault module when reporting.")

    elif result.injection_suspected:
        if use_zh:
            if result.event is not None and result.event.module_name:
                lines.append(f"  * 虽然崩溃点落在 {result.event.module_name} 中，但本机检测到常见外部注入覆盖层正在运行，极可能为间接注入干扰。")
            else:
                lines.append("  * 虽然未在事件日志中定位到具体故障模块，但本机检测到常见外部注入覆盖层正在运行，极可能为外部注入干扰。")
            lines.append("  1. [推荐，不影响中文输入法] 先暂时退出/禁用上述覆盖层软件或服务（如 NahimicService / RTSS）后再启动。")
            if not result.mitigation_already_enabled:
                lines.append("  2. [临时验证] 可在下方选择临时启用 MT_ENABLE_EXTENSION_POINT_MITIGATION=1 重试一次（注意：可能影响第三方中文输入法）。")
                lines.append("  3. 若关闭后仍崩溃，请运行 Win-Install-or-Update.bat 选择 [1] Install 修复依赖。")
            else:
                lines.append("  2. 当前已启用 MT_ENABLE_EXTENSION_POINT_MITIGATION=1 仍崩溃；若关闭上述覆盖层后依然复现，请运行 Win-Install-or-Update.bat 选择 [1] Install 修复依赖。")
        else:
            if result.event is not None and result.event.module_name:
                lines.append(f"  * Although the fault occurred in {result.event.module_name}, known overlay/hook software is running and may have triggered it.")
            else:
                lines.append("  * Although no specific fault module was found in Event Viewer, known overlay/hook software is running and may have triggered this crash.")
            lines.append("  1. [Recommended, preserves IME] Temporarily close/disable the overlay software or service (e.g. NahimicService / RTSS) and retry.")
            if not result.mitigation_already_enabled:
                lines.append("  2. [Temporary test] You can retry once with MT_ENABLE_EXTENSION_POINT_MITIGATION=1 below (note: may affect third-party CJK IMEs).")
                lines.append("  3. If it still crashes, run Win-Install-or-Update.bat -> [1] Install to repair dependencies.")
            else:
                lines.append("  2. MT_ENABLE_EXTENSION_POINT_MITIGATION=1 is already enabled; if closing the overlay still crashes, run Win-Install-or-Update.bat -> [1] Install.")

    else:
        if use_zh:
            lines.append("  1. 查看最新日志 result\\log_*.txt，或按 Win+R 运行 eventvwr.msc -> Windows 日志 -> 应用程序 查看 Application Error 详情。")
            lines.append("  2. 可先运行 Win-Install-or-Update.bat 选择 [1] Install 修复环境依赖。")
        else:
            lines.append("  1. Inspect the latest result\\log_*.txt or open eventvwr.msc -> Windows Logs -> Application for Application Error details.")
            lines.append("  2. Try running Win-Install-or-Update.bat -> [1] Install to repair dependencies.")

    lines.append(sep)
    return "\n".join(lines)


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post-crash auto-diagnostics for Windows native crashes."
    )
    parser.add_argument("--exit-code", type=int, required=True, help="Process exit code")
    parser.add_argument("--exe", default=None, help="Path to the crashed executable")
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=120,
        help="Lookback window for Windows Application Error events",
    )
    parser.add_argument(
        "--lang",
        choices=("auto", "zh", "en"),
        default="auto",
        help="Output language",
    )
    args = parser.parse_args(argv)

    result = diagnose_crash(
        exit_code=args.exit_code,
        exe_path=args.exe,
        window_seconds=args.window_seconds,
    )
    if not result.is_native_crash:
        return EXIT_NORMAL

    zh = None if args.lang == "auto" else (args.lang == "zh")
    report = format_diagnosis_report(result, zh=zh)
    if report:
        _safe_print(report)

    if result.injection_suspected and not result.mitigation_already_enabled:
        return EXIT_INJECTION_SUSPECTED
    return EXIT_NORMAL


if __name__ == "__main__":
    raise SystemExit(main())
