#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
漫画翻译器启动脚本
Manga Translator UI Launcher
"""

import os
import sys
import argparse
import subprocess
import importlib.util
from pathlib import Path

# 项目配置
BRANCH = 'main'
VERSION = '1.7.6'
PYTHON_VERSION_MIN = (3, 12)
PYTHON_VERSION_MAX = (3, 12)  # 仅支持Python 3.12,不支持3.13+

# AMD APU HSA 架构伪装版本 (针对不支持的 gfx1103/gfx1150 核显)
HSA_APU_JAILBREAK_VERSION = '11.0.0'

# 路径配置
PATH_ROOT = Path(__file__).parent.parent
stored_commit_hash = None

# 获取环境变量
python = sys.executable

# Git路径配置 (优先使用便携版)
portable_git = PATH_ROOT / "PortableGit" / "cmd" / "git.exe"
if portable_git.exists():
    git = str(portable_git)
else:
    git = os.environ.get('GIT', "git")

skip_install = False
index_url = os.environ.get('INDEX_URL', "")

# 备用镜像源列表（按优先级排序）
MIRROR_URLS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple/",  # 清华源
    "https://mirrors.aliyun.com/pypi/simple/",     # 阿里云
    "https://pypi.douban.com/simple/",             # 豆瓣
    "https://pypi.org/simple/",                    # 官方源（作为最后备选）
]

# PyTorch 专用源回退列表。
# 说明：
# - primary source 来自 pyproject.toml 中 [[tool.uv.index]] 定义的 PyTorch 源
# - 这里补充官方源之外的镜像，安装 torch 相关包时按顺序回退
PYTORCH_INDEX_FALLBACKS = {
    "https://download.pytorch.org/whl/cpu": [
        "https://mirror.sjtu.edu.cn/pytorch-wheels/cpu/",
        "https://mirrors.aliyun.com/pytorch-wheels/cpu/",
    ],
    "https://download.pytorch.org/whl/cu128": [
        "https://mirrors.aliyun.com/pytorch-wheels/cu128/",
    ],
}

# 对部分 PyTorch 源使用自定义尝试顺序。
# 例如 cu128 优先走国内镜像，失败后再回退官方源。
PYTORCH_INDEX_PRIORITY = {
    "https://download.pytorch.org/whl/cu128": [
        "https://mirrors.aliyun.com/pytorch-wheels/cu128/",
        "https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/",
        "https://download.pytorch.org/whl/cu128",
    ],
}


def normalize_index_url(url):
    """统一 index-url 格式，便于做去重和映射。"""
    return (url or "").strip().rstrip("/")


def build_trusted_host_args(urls):
    """根据 URL 列表构建 pip 的 trusted-host 参数。"""
    import urllib.parse

    hosts = []
    for url in urls:
        parsed = urllib.parse.urlparse(url or "")
        if parsed.hostname and parsed.hostname not in hosts:
            hosts.append(parsed.hostname)
    return "".join(f" --trusted-host {host}" for host in hosts)


def get_pytorch_index_candidates(primary_index_url):
    """返回 PyTorch 包安装时应尝试的专用源列表。"""
    normalized_primary = normalize_index_url(primary_index_url)
    candidates = []
    seen = set()

    def add(url):
        normalized = normalize_index_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    preferred_urls = PYTORCH_INDEX_PRIORITY.get(normalized_primary)
    if preferred_urls:
        for preferred_url in preferred_urls:
            add(preferred_url)
        return candidates

    add(normalized_primary)
    for fallback_url in PYTORCH_INDEX_FALLBACKS.get(normalized_primary, []):
        add(fallback_url)
    return candidates


# ============================================================
# pyproject.toml 依赖读取（替代原 requirements_*.txt 文件）
# 依赖声明在 pyproject.toml 中：公共依赖 + cpu/gpu/amd/metal 四个 extra
# ============================================================
PYPROJECT_FILE = PATH_ROOT / 'pyproject.toml'
DEP_VARIANTS = ('cpu', 'gpu', 'amd', 'metal')

_pyproject_cache = None


def normalize_variant(name):
    """校验并规范化变体名 (cpu/gpu/amd/metal)，无效返回 None"""
    if not name:
        return None
    name = str(name).strip().lower()
    return name if name in DEP_VARIANTS else None


def _load_pyproject():
    global _pyproject_cache
    if _pyproject_cache is None:
        import tomllib
        with open(PYPROJECT_FILE, 'rb') as f:
            _pyproject_cache = tomllib.load(f)
    return _pyproject_cache


def _dep_base_name(dep):
    """从依赖串中取出包名（去掉版本约束/extras/@url）"""
    import re
    m = re.match(r'^\s*([A-Za-z0-9._-]+)', dep or '')
    return m.group(1) if m else (dep or '').strip()


def _resolve_platform_source(name, sources):
    """把 tool.uv.sources 中按平台区分的 url/git 来源转成 pip 可用的依赖串。

    仅处理 url/git 类型来源（如 pydensecrf）；index 类型来源（torch 等）返回 None，
    交给 PyTorch 专用源逻辑处理。
    """
    entries = sources.get(name)
    if not entries:
        return None
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        marker = entry.get('marker')
        if marker:
            try:
                from packaging.markers import Marker
                if not Marker(marker).evaluate():
                    continue
            except Exception:
                # packaging 不可用时退化为简单平台名匹配
                if f"'{sys.platform}'" not in marker:
                    continue
        if 'url' in entry:
            return f"{name} @ {entry['url']}"
        if 'git' in entry:
            return f"{name} @ git+{entry['git']}"
    return None


def get_variant_packages(variant):
    """从 pyproject.toml 取出 公共依赖 + 指定 extra 的依赖列表。

    返回内容等价于原 requirements_<variant>.txt 中的包列表。
    """
    variant = normalize_variant(variant)
    if variant is None:
        raise RuntimeError(f'未知的依赖方案: {variant}，可选: {", ".join(DEP_VARIANTS)}')
    data = _load_pyproject()
    project = data.get('project', {})
    sources = data.get('tool', {}).get('uv', {}).get('sources', {})
    source_names = {k.lower() for k in sources}

    deps = list(project.get('dependencies', []))
    deps += list(project.get('optional-dependencies', {}).get(variant, []))

    packages = []
    for dep in deps:
        base_name = _dep_base_name(dep)
        if base_name.lower() in source_names:
            resolved = _resolve_platform_source(base_name, sources)
            if resolved:
                packages.append(resolved)
                continue
        packages.append(dep)
    return packages


def get_variant_index_url(variant):
    """获取变体对应的 PyTorch 主源（等价于原 requirements 文件中的 --index-url）"""
    variant = normalize_variant(variant)
    if variant is None:
        return None
    data = _load_pyproject()
    tool_uv = data.get('tool', {}).get('uv', {})
    indexes = {}
    for idx in tool_uv.get('index', []):
        if idx.get('name') and idx.get('url'):
            indexes[idx['name']] = idx['url']
    torch_sources = tool_uv.get('sources', {}).get('torch', [])
    if isinstance(torch_sources, dict):
        torch_sources = [torch_sources]
    for entry in torch_sources:
        if entry.get('extra') == variant and entry.get('index') in indexes:
            return indexes[entry['index']]
    return None


def is_python_version_valid():
    """检查Python版本是否符合要求"""
    if sys.version_info < PYTHON_VERSION_MIN:
        print(f'错误: 需要 Python {PYTHON_VERSION_MIN[0]}.{PYTHON_VERSION_MIN[1]}+ ')
        print(f'当前版本: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
        return False
    if sys.version_info[:2] > PYTHON_VERSION_MAX:
        print(f'错误: 仅支持 Python {PYTHON_VERSION_MAX[0]}.{PYTHON_VERSION_MAX[1]},不支持更高版本')
        print(f'当前版本: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
        print(f'请使用 Python {PYTHON_VERSION_MAX[0]}.{PYTHON_VERSION_MAX[1]} 版本')
        return False
    return True


def run(command, desc=None, errdesc=None, custom_env=None, live=False, timeout=None, capture_output=True):
    """执行系统命令
    
    Args:
        command: 要执行的命令
        desc: 描述信息
        errdesc: 错误描述
        custom_env: 自定义环境变量
        live: 是否实时显示输出（不捕获）
        timeout: 超时时间（秒），None 表示无超时
        capture_output: 是否捕获输出，False 时丢弃输出避免死锁
    """
    if desc is not None:
        print(desc)

    env = os.environ if custom_env is None else custom_env

    if live:
        # 实时模式：直接显示输出
        result = subprocess.run(command, shell=True, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"""{errdesc or '命令执行错误'}.
命令: {command}
错误代码: {result.returncode}""")
        return ""

    if not capture_output:
        # 不捕获输出模式：丢弃输出避免死锁
        result = subprocess.run(
            command, 
            shell=True, 
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout
        )
        if result.returncode != 0:
            raise RuntimeError(f"""{errdesc or '命令执行错误'}.
命令: {command}
错误代码: {result.returncode}""")
        return ""

    # 捕获输出模式：使用 Popen + communicate 避免死锁
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # communicate() 会同时读取 stdout 和 stderr，避免死锁
        stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
        
        # 解码输出
        try:
            stdout = stdout_bytes.decode('utf-8', errors='ignore')
        except:
            try:
                stdout = stdout_bytes.decode('gbk', errors='ignore')
            except:
                stdout = str(stdout_bytes)
        
        try:
            stderr = stderr_bytes.decode('utf-8', errors='ignore')
        except:
            try:
                stderr = stderr_bytes.decode('gbk', errors='ignore')
            except:
                stderr = str(stderr_bytes)
        
        if process.returncode != 0:
            message = f"""{errdesc or '命令执行错误'}.
命令: {command}
错误代码: {process.returncode}
stdout: {stdout if stdout else '<empty>'}
stderr: {stderr if stderr else '<empty>'}
"""
            raise RuntimeError(message)
        
        return stdout
        
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()  # 清理剩余输出
        raise RuntimeError(f"""{errdesc or '命令超时'}.
命令: {command}
超时: {timeout}秒""")


def run_pip(args, desc=None):
    """使用pip安装包，支持多镜像源自动回退"""
    if skip_install:
        return
    
    import urllib.parse
    
    def build_pip_command(pip_args, mirror_url=None):
        """构建pip命令"""
        index_url_line = f' --index-url {mirror_url}' if mirror_url else ''
        trusted_host_line = ''
        
        if mirror_url:
            parsed = urllib.parse.urlparse(mirror_url)
            if parsed.hostname:
                trusted_host_line += f' --trusted-host {parsed.hostname}'
            trusted_host_line += ' --trusted-host download.pytorch.org'
        
        return f'"{python}" -m pip {pip_args} --prefer-binary{index_url_line}{trusted_host_line} --disable-pip-version-check --no-warn-script-location'
    
    # 如果用户指定了 INDEX_URL，优先使用
    if index_url:
        mirrors_to_try = [index_url] + [m for m in MIRROR_URLS if m != index_url]
    else:
        mirrors_to_try = MIRROR_URLS.copy()
    
    last_error = None
    for i, mirror in enumerate(mirrors_to_try):
        try:
            mirror_name = urllib.parse.urlparse(mirror).hostname or mirror
            if i == 0:
                print(f"正在安装 {desc}...")
            else:
                print(f"尝试备用镜像源: {mirror_name}")
            
            cmd = build_pip_command(args, mirror)
            result = subprocess.run(cmd, shell=True, env=os.environ)
            
            if result.returncode == 0:
                return ""
            else:
                last_error = f"返回码: {result.returncode}"
                print(f"镜像源 {mirror_name} 安装失败，{last_error}")
                
        except Exception as e:
            last_error = str(e)
            print(f"镜像源 {mirror_name} 出错: {last_error}")
    
    # 所有镜像源都失败
    raise RuntimeError(f"无法安装 {desc}，所有镜像源均失败。最后错误: {last_error}")


# 需要从 PyTorch 源下载的包列表（包括 PyTorch 及其依赖）
PYTORCH_SOURCE_PACKAGES = [
    'torch', 'torchvision', 'torchaudio', 'xformers',
    # PyTorch 核心依赖
    'pytorch-triton', 'pytorch-triton-rocm', 'pytorch-triton-xpu',
    'torch-cuda80', 'torch-model-archiver', 'torch-tb-profiler',
    'torch-tensorrt', 'torchao', 'torchaudio', 'torchcodec',
    'torchcsprng', 'torchdata', 'torchmetrics', 'torchrec',
    'torchrec-cpu', 'torchserve', 'torchtext', 'torchvision',
    # NVIDIA CUDA 相关
    'nvidia-cublas-cu12', 'nvidia-cuda-cupti-cu12', 'nvidia-cuda-nvrtc-cu12',
    'nvidia-cuda-runtime-cu12', 'nvidia-cudnn-cu11', 'nvidia-cudnn-cu12',
    'nvidia-cudnn-cu13', 'nvidia-cufft-cu12', 'nvidia-cufile-cu12',
    'nvidia-curand-cu12', 'nvidia-cusolver-cu12', 'nvidia-cusparse-cu12',
    'nvidia-cusparselt-cu12', 'nvidia-nccl-cu12', 'nvidia-nvjitlink-cu12',
    'nvidia-nvshmem-cu12', 'nvidia-nvtx-cu12',
    # Intel oneAPI 相关
    'intel-cmplr-lib-rt', 'intel-cmplr-lib-ur', 'intel-cmplr-lic-rt',
    'intel-opencl-rt', 'intel-openmp', 'intel-pti', 'intel-sycl-rt',
    'oneccl', 'oneccl-devel', 'onemkl-sycl-blas', 'onemkl-sycl-dft',
    'onemkl-sycl-lapack', 'onemkl-sycl-rng', 'onemkl-sycl-sparse',
    # 其他 PyTorch 生态依赖
    'triton', 'fbgemm-gpu', 'fbgemm-gpu-genai', 'flashinfer',
    'flashinfer-python', 'vllm', 'cuda-bindings', 'dpcpp-cpp-rt',
    'mpi-rt', 'tcmlib'
]

# 不应从 PyTorch 源下载的包（即使名字以 torch 开头）
PYTORCH_SOURCE_EXCLUDED = ['torchsummary', 'torchmetrics']


def is_pytorch_source_package(pkg_name):
    """检查是否是需要从 PyTorch 源下载的包"""
    pkg_lower = (pkg_name or '').lower()
    if pkg_lower in PYTORCH_SOURCE_EXCLUDED:
        return False
    for prefix in PYTORCH_SOURCE_PACKAGES:
        if pkg_lower.startswith(prefix):
            return True
    return False


def find_uv():
    """查找 uv 命令（返回可直接拼进命令行的字符串），找不到返回 None

    查找顺序: 打包目录自带 uv.exe -> 当前环境已安装的 uv 模块（不检测系统 PATH）
    """
    for candidate in (PATH_ROOT / 'packaging' / 'uv.exe', PATH_ROOT / 'uv.exe'):
        if candidate.exists():
            return f'"{candidate}"'
    # 环境内 pip 安装过 uv 的情况（conda 旧环境兼容）
    try:
        import importlib.util
        if importlib.util.find_spec('uv') is not None:
            return f'"{python}" -m uv'
    except Exception:
        pass
    return None


def run_pip_packages_fallback(packages, primary_index_url, desc=None):
    """pip 逐包安装（未检测到 uv 时的回退路径），失败时从失败的包开始切换镜像重试"""
    import urllib.parse

    def build_pip_command(pip_args, index_source=None):
        index_url_line = f' --index-url {index_source}' if index_source else ''
        trusted_host_line = build_trusted_host_args([index_source, "https://download.pytorch.org"])
        return f'"{python}" -m pip {pip_args} --prefer-binary{index_url_line}{trusted_host_line} --disable-pip-version-check --no-warn-script-location'

    # 如果用户指定了 INDEX_URL，优先使用
    if index_url:
        mirrors_to_try = [index_url] + [m for m in MIRROR_URLS if m != index_url]
    else:
        mirrors_to_try = MIRROR_URLS.copy()

    total = len(packages)
    print(f"正在安装 {desc or '依赖'}... (共 {total} 个包)")

    pkg_idx = 0
    while pkg_idx < total:
        pkg = packages[pkg_idx]

        # 获取包名用于显示（去除版本约束）
        pkg_display = pkg.split('==')[0].split('>=')[0].split('<=')[0].split('[')[0].split('@')[0].strip()
        print(f"[{pkg_idx + 1}/{total}] 安装 {pkg_display}...")

        # 检查是否是 PyTorch 相关包，需要使用主源
        use_primary = is_pytorch_source_package(pkg_display) and primary_index_url
        index_candidates = get_pytorch_index_candidates(primary_index_url) if use_primary else mirrors_to_try

        installed = False
        last_error = None
        for source_idx, current_index in enumerate(index_candidates):
            source_name = urllib.parse.urlparse(current_index).hostname or current_index
            if use_primary:
                print(f"    (使用 PyTorch 源: {current_index})")

            cmd = build_pip_command(f'install "{pkg}"', current_index)

            try:
                result = subprocess.run(cmd, shell=True, env=os.environ)
                if result.returncode == 0:
                    installed = True
                    break

                last_error = f"返回码: {result.returncode}"
                print(f"[失败] {pkg_display} 在 {source_name} 安装失败，{last_error}")
            except Exception as e:
                last_error = str(e)
                print(f"[错误] 安装 {pkg_display} 时出错: {e}")

            if source_idx + 1 < len(index_candidates):
                next_index = index_candidates[source_idx + 1]
                next_name = urllib.parse.urlparse(next_index).hostname or next_index
                print(f"[重试] 切换到镜像 {next_name}，从 {pkg_display} 重新开始...")

        if not installed:
            raise RuntimeError(f"无法安装 {pkg_display}，所有镜像源均失败。最后错误: {last_error}")

        pkg_idx += 1

    print(f"[完成] {desc or '依赖'} 安装完成")


def run_uv_packages(uv, packages, primary_index_url, desc=None):
    """使用 uv 批量安装包（快速路径）。

    PyTorch 相关包走 PyTorch 专用源（含镜像回退），其余包走 PyPI 镜像（含回退）。
    任一批次所有源都失败时抛异常，由调用方回退到 pip 逐包安装。
    """
    import urllib.parse

    # 缓存放到包目录所在磁盘：跨盘无法硬链接会退化成整份复制（慢且占双倍空间）
    os.environ.setdefault('UV_CACHE_DIR', str(PATH_ROOT / 'packaging' / 'uv_cache'))

    if primary_index_url:
        pytorch_pkgs = [p for p in packages if is_pytorch_source_package(_dep_base_name(p))]
    else:
        pytorch_pkgs = []
    normal_pkgs = [p for p in packages if p not in pytorch_pkgs]

    def uv_install(pkgs, install_index_url, find_links=None):
        quoted = ' '.join(f'"{p}"' for p in pkgs)
        cmd = f'{uv} pip install --python "{python}"'
        if find_links:
            cmd += f' --find-links {find_links}'
        if install_index_url:
            cmd += f' --index-url {install_index_url}'
        cmd += f' {quoted}'
        result = subprocess.run(cmd, shell=True, env=os.environ)
        return result.returncode == 0

    # 先装 PyTorch 相关包（按优先级回退：国内镜像优先，官方源兜底）
    # 官方源是标准 PEP 503 索引，直接当 --index-url 用；
    # 国内镜像是静态 wheel 目录，用 --find-links 解析，其余依赖走 PyPI 镜像
    if pytorch_pkgs:
        pypi_mirror = index_url or MIRROR_URLS[0]
        installed = False
        for candidate in get_pytorch_index_candidates(primary_index_url):
            if 'download.pytorch.org' in candidate:
                print(f'[uv] 安装 PyTorch 相关包 ({len(pytorch_pkgs)} 个)，源: {candidate}')
                ok = uv_install(pytorch_pkgs, candidate)
            else:
                print(f'[uv] 安装 PyTorch 相关包 ({len(pytorch_pkgs)} 个)，镜像: {candidate} (find-links)')
                ok = uv_install(pytorch_pkgs, pypi_mirror, find_links=candidate)
            if ok:
                installed = True
                break
            print(f'[uv][失败] PyTorch 源 {candidate} 安装失败，尝试下一个源...')
        if not installed:
            raise RuntimeError('uv 安装 PyTorch 相关包失败（所有 PyTorch 源均失败）')

    # 再批量装其余包（走 PyPI 镜像，按顺序回退）
    if normal_pkgs:
        if index_url:
            mirrors_to_try = [index_url] + [m for m in MIRROR_URLS if m != index_url]
        else:
            mirrors_to_try = MIRROR_URLS.copy()
        installed = False
        for mirror in mirrors_to_try:
            mirror_name = urllib.parse.urlparse(mirror).hostname or mirror
            print(f'[uv] 批量安装 {len(normal_pkgs)} 个包，镜像: {mirror_name}')
            if uv_install(normal_pkgs, mirror):
                installed = True
                break
            print(f'[uv][失败] 镜像 {mirror_name} 安装失败，尝试下一个镜像...')
        if not installed:
            raise RuntimeError('uv 批量安装失败（所有镜像源均失败）')

    print(f'[完成] {desc or "依赖"} 安装完成 (uv)')


def run_pip_requirements(variant, desc=None, exclude_packages=None):
    """逐个安装指定依赖方案（pyproject.toml extra）中的包，失败时从失败的包开始切换镜像重试

    Args:
        variant: 依赖方案 (cpu/gpu/amd/metal)
        desc: 描述信息
        exclude_packages: 需要排除的包名列表（小写），如 AMD 模式下跳过 PyTorch 生态包
    """
    if skip_install:
        return

    # 从 pyproject.toml 读取包列表和 PyTorch 主源
    packages = get_variant_packages(variant)
    primary_index_url = get_variant_index_url(variant)
    if exclude_packages:
        excluded = {p.lower() for p in exclude_packages}
        packages = [p for p in packages if _dep_base_name(p).lower() not in excluded]

    run_pip_packages(packages, primary_index_url, desc or f'依赖方案 {variant}')


def run_pip_packages(packages, primary_index_url, desc=None):
    """安装包列表：检测到 uv 用 uv 批量安装（快），否则用 pip 逐包安装（兼容）"""
    if skip_install:
        return
    if not packages:
        print(f"[警告] {desc or '依赖列表'} 中没有找到有效的依赖包")
        return
    uv = find_uv()
    if uv:
        run_uv_packages(uv, packages, primary_index_url, desc)
    else:
        print('[INFO] 未检测到 uv，使用 pip 逐包安装')
        run_pip_packages_fallback(packages, primary_index_url, desc)


def ensure_git_safe_directory():
    """确保当前目录在 Git safe.directory 列表中，解决所有权问题"""
    try:
        # 将项目根目录添加到 Git safe.directory
        subprocess.run(
            [git, 'config', '--global', '--add', 'safe.directory', str(PATH_ROOT)],
            capture_output=True,
            check=False
        )
    except Exception:
        pass  # 忽略错误，不影响后续操作


def commit_hash():
    """获取当前Git commit hash"""
    global stored_commit_hash
    if stored_commit_hash is not None:
        return stored_commit_hash

    ensure_git_safe_directory()  # 确保 safe.directory 已配置
    try:
        stored_commit_hash = run(f"{git} rev-parse HEAD").strip()
    except Exception:
        stored_commit_hash = "<none>"

    return stored_commit_hash


def restart():
    """重启应用"""
    print('正在重启应用...\n')
    os.execv(sys.executable, ['python'] + sys.argv)


def detect_gpu():
    """检测GPU类型 - 使用多种方法以提高兼容性
    
    支持双显卡笔记本（如 NVIDIA 独显 + AMD 核显）：
    - 先列出所有检测到的显卡
    - 如果检测到多张显卡，让用户选择使用哪张
    - 每张显卡的类型和名称严格对应，不会张冠李戴
    """
    
    def classify_gpu_line(line):
        """对单行显卡名称进行分类，返回 GPU 类型或 None"""
        if not line:
            return None
        upper = line.upper()
        if any(kw in upper for kw in ["NVIDIA", "GEFORCE", "GTX", "RTX", "QUADRO", "TESLA"]):
            return "NVIDIA"
        if any(kw in upper for kw in ["AMD", "RADEON", "ATI"]):
            return "AMD"
        if any(kw in upper for kw in ["INTEL", "HD GRAPHICS", "UHD GRAPHICS", "IRIS", "ARC"]):
            return "Intel"
        return None
    
    def parse_all_gpus(output):
        """从多行输出中解析所有显卡，返回 [(type, name), ...] 列表"""
        if not output:
            return []
        results = []
        seen_names = set()
        for line in output.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('NAME') or line.startswith('---'):
                continue
            if 'REG_SZ' in line:
                line = line.split('REG_SZ', 1)[1].strip()
            gpu_type = classify_gpu_line(line)
            if gpu_type and line not in seen_names:
                results.append((gpu_type, line))
                seen_names.add(line)
        return results

    def normalize_gpu_name(gpu_name):
        """标准化显卡名称，用于跨检测方式去重。"""
        return ' '.join((gpu_name or '').strip().split()).upper()

    def add_gpu_results(all_gpus, gpu_results):
        """合并多种检测方式的结果，避免第一个 API 只返回核显。"""
        seen_names = {normalize_gpu_name(name) for _, name in all_gpus}
        for gpu_type, gpu_name in gpu_results:
            key = normalize_gpu_name(gpu_name)
            if key and key not in seen_names:
                all_gpus.append((gpu_type, gpu_name))
                seen_names.add(key)

    def is_integrated_gpu(gpu_type, gpu_name):
        """判断是否为核显/低性能显示适配器。"""
        upper = (gpu_name or '').upper()

        # 先排除常见独显标记，避免把 "Radeon RX ... Graphics" 误判为核显。
        discrete_markers = [
            ' RX ', 'RX ', 'RADEON PRO', 'PRO W', 'MI300', 'MI350',
            'GEFORCE', 'RTX', 'GTX', 'QUADRO', 'TESLA', 'ARC A', 'ARC B',
        ]
        if any(marker in upper for marker in discrete_markers):
            return False

        if gpu_type == 'AMD':
            return any(kw in upper for kw in [
                'RADEON(TM) GRAPHICS',
                'AMD RADEON(TM) GRAPHICS',
                'AMD RADEON GRAPHICS',
                'RADEON GRAPHICS',
            ])
        if gpu_type == 'Intel':
            return 'ARC' not in upper
        return 'BASIC DISPLAY ADAPTER' in upper
    
    def check_nvidia_cuda_version():
        """检查 NVIDIA CUDA 驱动版本"""
        try:
            # 尝试运行 nvidia-smi 获取驱动版本
            cmd = 'nvidia-smi --query-gpu=driver_version --format=csv,noheader'
            output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='gbk', errors='ignore')
            driver_version = output.strip().split('\n')[0].strip()
            
            # 尝试从nvidia-smi直接输出获取CUDA版本
            # nvidia-smi输出的第一行通常包含CUDA版本信息
            try:
                cmd_full = 'nvidia-smi'
                full_output = subprocess.check_output(cmd_full, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='gbk', errors='ignore')
                # 解析 "CUDA Version: X.Y" 格式
                import re
                # 兼容新旧 nvidia-smi 输出格式: "CUDA Version: 12.8" / "CUDA UMD Version: 13.3"
                cuda_match = re.search(r'CUDA (?:UMD )?Version:\s*(\d+\.\d+)', full_output)
                if cuda_match:
                    cuda_version = cuda_match.group(1)
                    cuda_major = int(cuda_version.split('.')[0])
                    return cuda_major, cuda_version, driver_version
            except:
                pass
            
            # 如果无法获取CUDA版本，返回驱动版本
            return None, None, driver_version
        except Exception:
            return None, None, None
    
    def prompt_user_choose_gpu(all_gpus):
        """当检测到多张显卡时，让用户选择使用哪张
        
        Args:
            all_gpus: [(type, name), ...] 列表
        Returns:
            (gpu_type, gpu_name) 用户选择的显卡
        """
        # 只有一张显卡，直接返回
        if len(all_gpus) <= 1:
            return all_gpus[0]
        
        options = []
        for gpu_type, gpu_name in all_gpus:
            options.append((gpu_type, gpu_name))

        def get_gpu_priority(gpu_info):
            gpu_type, gpu_name = gpu_info
            if gpu_type == 'NVIDIA':
                return 400
            if gpu_type == 'AMD':
                detected_gfx, _, has_torch = detect_amd_gfx_version(gpu_name)
                if has_torch:
                    return 350
                if not is_integrated_gpu(gpu_type, gpu_name):
                    return 250
                return 100
            if gpu_type == 'Intel':
                return 180 if not is_integrated_gpu(gpu_type, gpu_name) else 80
            return 0

        default_idx = 1
        max_priority = -1
        for idx, gpu_info in enumerate(options, 1):
            priority = get_gpu_priority(gpu_info)
            if priority > max_priority:
                max_priority = priority
                default_idx = idx
            
        # 检查是否配置了环境变量来跳过手动选择
        env_choice = os.environ.get('MANGAT_SELECTED_GPU')
        if env_choice:
            env_choice = env_choice.strip().upper()
            # 1. 尝试匹配序号 (如 "1", "2")
            if env_choice.isdigit():
                choice_idx = int(env_choice)
                if 1 <= choice_idx <= len(options):
                    selected = options[choice_idx - 1]
                    print(f"检测到环境变量 MANGAT_SELECTED_GPU={env_choice}，已自动选择显卡: {selected[1]}")
                    return selected
            # 2. 尝试匹配显卡类型 (如 "NVIDIA", "AMD")。同类型多卡时选优先级最高的。
            type_matches = [
                (gpu_type, gpu_name)
                for gpu_type, gpu_name in options
                if gpu_type.upper() == env_choice
            ]
            if type_matches:
                selected = max(type_matches, key=get_gpu_priority)
                print(f"检测到环境变量 MANGAT_SELECTED_GPU={env_choice}，已自动选择显卡: {selected[1]}")
                return selected
            # 3. 尝试模糊匹配显卡名称 (如 "4070", "780M")
            for gpu_type, gpu_name in options:
                if env_choice in gpu_name.upper():
                    print(f"检测到环境变量 MANGAT_SELECTED_GPU={env_choice}，已自动选择显卡: {gpu_name}")
                    return gpu_type, gpu_name
        
        # 多张显卡，提示用户选择
        print('')
        print('=' * 55)
        print('检测到多张显卡')
        print('=' * 55)
        print('')
        
        for idx, (gpu_type, gpu_name) in enumerate(options, 1):
            hint_parts = []
            if gpu_type == 'NVIDIA':
                hint_parts.append('CUDA')
            elif gpu_type == 'AMD':
                detected_gfx, _, has_torch = detect_amd_gfx_version(gpu_name)
                if has_torch:
                    hint_parts.append('ROCm 支持')
                else:
                    hint_parts.append('ROCm 未确认')
            elif gpu_type == 'Intel':
                hint_parts.append('支持有限')

            if is_integrated_gpu(gpu_type, gpu_name):
                hint_parts.append('核显/低性能')
            if idx == default_idx:
                hint_parts.append('推荐')

            hint = f" ({', '.join(hint_parts)})" if hint_parts else ''
            print(f'  [{idx}] {gpu_name}{hint}')
        
        print('')
        print(f'  默认选择: [{default_idx}]')
        print('')
        
        while True:
            choice = input(f'请选择要使用的显卡 (1-{len(options)}, 默认{default_idx}): ').strip()
            if choice == '':
                return options[default_idx - 1]
            try:
                choice_int = int(choice)
                if 1 <= choice_int <= len(options):
                    return options[choice_int - 1]
            except ValueError:
                pass
            print(f'无效输入，请输入 1 到 {len(options)} 之间的数字')
    
    try:
        if sys.platform == 'win32':
            # Windows 系统：尝试多种检测方式（优先使用无需安装的方法）
            all_gpus = []
            
            # 方法1: 尝试 PowerShell Get-CimInstance（Windows 8+，无需额外工具）
            try:
                cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"'
                output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='gbk', errors='ignore')
                add_gpu_results(all_gpus, parse_all_gpus(output))
            except Exception:
                pass
            
            # 方法2: 尝试 wmic（经典方法，兼容老系统）
            try:
                cmd = 'wmic path win32_VideoController get name'
                output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='gbk', errors='ignore')
                add_gpu_results(all_gpus, parse_all_gpus(output))
            except Exception:
                pass
            
            # 方法3: 尝试 PowerShell Get-WmiObject（更老的 PowerShell）
            try:
                cmd = 'powershell -NoProfile -Command "Get-WmiObject Win32_VideoController | Select-Object -ExpandProperty Name"'
                output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='gbk', errors='ignore')
                add_gpu_results(all_gpus, parse_all_gpus(output))
            except Exception:
                pass
            
            # 方法4: 尝试读取注册表（最底层的方法）
            try:
                cmd = 'reg query "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}" /s /v DriverDesc'
                output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='gbk', errors='ignore')
                add_gpu_results(all_gpus, parse_all_gpus(output))
            except Exception:
                pass
            
            # 方法5: 尝试使用 wmi Python 库（需要额外安装，作为最后备选）
            if not all_gpus:
                try:
                    try:
                        import wmi
                    except ImportError:
                        try:
                            import subprocess as sp
                            print('正在安装 wmi 库以进行显卡检测...')
                            sp.run([python, '-m', 'pip', 'install', 'wmi', '--quiet'], check=True, timeout=30)
                            import wmi
                            print('wmi 库安装成功')
                        except Exception:
                            raise ImportError('wmi 库安装失败')
                    
                    c = wmi.WMI()
                    seen_names = set()
                    for gpu in c.Win32_VideoController():
                        gpu_type = classify_gpu_line(gpu.Name)
                        if gpu_type and gpu.Name not in seen_names:
                            all_gpus.append((gpu_type, gpu.Name))
                            seen_names.add(gpu.Name)
                except (ImportError, Exception):
                    pass
            
            # 处理检测结果
            if all_gpus:
                if len(all_gpus) > 1:
                    # 多张显卡：让用户选择
                    gpu_type, gpu_name = prompt_user_choose_gpu(all_gpus)
                else:
                    # 单张显卡：直接使用第一个
                    gpu_type, gpu_name = all_gpus[0]
                
                # 如果选择了 NVIDIA，补充 CUDA 信息
                if gpu_type == "NVIDIA":
                    cuda_major, cuda_version, driver_version = check_nvidia_cuda_version()
                    return gpu_type, gpu_name, cuda_major, cuda_version, driver_version
                return gpu_type, gpu_name, None, None, None
                
        else:
            # macOS: 特殊处理 Apple Silicon
            if sys.platform == 'darwin':
                try:
                    # 检测是否是 Apple Silicon (M1/M2/M3/M4 等)
                    import platform
                    machine = platform.machine()
                    
                    if machine == 'arm64':
                        # Apple Silicon Mac，使用 system_profiler 获取芯片信息
                        try:
                            output = subprocess.check_output(
                                "system_profiler SPHardwareDataType | grep 'Chip'",
                                shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5
                            )
                            # 解析芯片名称，例如 "Chip: Apple M4 Pro"
                            chip_name = ""
                            for line in output.strip().split('\n'):
                                if 'Chip' in line:
                                    parts = line.split(':')
                                    if len(parts) >= 2:
                                        chip_name = parts[1].strip()
                                        break
                            
                            if chip_name and ('M1' in chip_name or 'M2' in chip_name or 
                                              'M3' in chip_name or 'M4' in chip_name or
                                              'Apple' in chip_name):
                                # Apple Silicon，支持 Metal
                                return "AppleSilicon", chip_name, None, None, None
                        except Exception:
                            pass
                        
                        # 如果无法获取具体芯片名称，但确定是 arm64，仍然返回 Apple Silicon
                        return "AppleSilicon", "Apple Silicon", None, None, None
                    
                    # Intel Mac，继续使用下面的通用检测逻辑
                except Exception:
                    pass
            
            # Linux 或 Intel Mac: 使用lspci或其他工具
            all_gpus = []
            try:
                output = subprocess.check_output("lspci | grep -i vga", shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='utf-8', errors='ignore')
                all_gpus = parse_all_gpus(output)
            except:
                pass
            
            # 尝试使用 lshw (Linux only) 作为补充
            if not all_gpus:
                try:
                    output = subprocess.check_output("lshw -C display 2>/dev/null | grep 'product:'", shell=True, text=True, stderr=subprocess.DEVNULL, timeout=5, encoding='utf-8', errors='ignore')
                    all_gpus = parse_all_gpus(output)
                except:
                    pass
            
            if all_gpus:
                if len(all_gpus) > 1:
                    gpu_type, gpu_name = prompt_user_choose_gpu(all_gpus)
                else:
                    gpu_type, gpu_name = all_gpus[0]
                
                if gpu_type == "NVIDIA":
                    cuda_major, cuda_version, driver_version = check_nvidia_cuda_version()
                    return gpu_type, gpu_name, cuda_major, cuda_version, driver_version
                return gpu_type, gpu_name, None, None, None
                
    except Exception:
        pass
    
    return "CPU", "", None, None, None


def detect_amd_gfx_version(gpu_name):
    """根据 AMD 显卡名称检测对应的 gfx 版本
    
    返回: (gfx_version, architecture_name, has_torch_support) 或 (None, None, False)
    """
    if not gpu_name:
        return None, None, False
    
    gpu_name_upper = gpu_name.upper()
    
    # AMD 显卡型号到 gfx 版本的映射（Windows ROCm 7.2 + PyTorch 支持列表）
    amd_gpu_mapping = {
        # CDNA 数据中心系列 - 支持
        'gfx94X-dcgpu': {
            'keywords': ['MI300A', 'MI300X'],
            'name': 'CDNA (MI300A/MI300X)',
            'has_torch': True
        },
        'gfx950-dcgpu': {
            'keywords': ['MI350X', 'MI355X'],
            'name': 'CDNA (MI350X/MI355X)',
            'has_torch': True
        },

        # RDNA 3 架构 - 支持的具体型号
        'gfx110X-dgpu': {
            'keywords': ['RX 7900 XTX', '7900 XTX', 'RX 7800 XT', '7800 XT', 'RX 7700S', '7700S', 'FRAMEWORK LAPTOP 16'],
            'name': 'RDNA 3 (RX 7900 XTX / RX 7800 XT / RX 7700S)',
            'has_torch': True
        },

        # Strix Halo iGPU - 支持
        'gfx1151': {
            'keywords': ['STRIX HALO'],
            'name': 'Strix Halo iGPU',
            'has_torch': True
        },

        # RDNA 4 架构 - 支持的具体型号
        'gfx120X-all': {
            'keywords': ['RX 9060 XT', 'RX 9060', 'RX 9070 XT', 'RX 9070'],
            'name': 'RDNA 4 (RX 9060/9070 系列)',
            'has_torch': True
        },

        # RDNA 3 APU 核显 - 官方不支持但可越狱加速
        'gfx1103': {
            'keywords': ['780M', '760M', '740M'],
            'name': 'RDNA 3 APU (Radeon 780M / 760M / 740M) - 越狱加速',
            'has_torch': False
        },

        # RDNA 3.5 APU 核显 - 官方不支持但可越狱加速
        'gfx1150': {
            'keywords': ['890M', '880M', '860M'],
            'name': 'RDNA 3.5 APU (Radeon 890M / 880M / 860M) - 越狱加速',
            'has_torch': False
        },
        
        # 以下架构不支持 torch（已验证）
        # RDNA 2 架构 (RX 6000 系列) - 不支持
        'gfx103X-dgpu': {
            'keywords': ['RX 6', '6900', '6800', '6700', '6600', '6500', '6400'],
            'name': 'RDNA 2 (RX 6000 系列) - 不支持 PyTorch',
            'has_torch': False
        },
        
        # RDNA 1 架构 (RX 5000 系列) - 不支持
        'gfx101X-dgpu': {
            'keywords': ['RX 5', '5700', '5600', '5500'],
            'name': 'RDNA 1 (RX 5000 系列) - 不支持 PyTorch',
            'has_torch': False
        },
        
        # Vega 架构 - 不支持
        'gfx90X-dcgpu': {
            'keywords': ['VEGA', 'RADEON VII', 'MI25', 'MI50', 'MI60'],
            'name': 'Vega (Radeon VII / MI50/60) - 不支持 PyTorch',
            'has_torch': False
        },
    }
    
    # 尝试匹配
    for gfx_version, info in amd_gpu_mapping.items():
        for keyword in info['keywords']:
            if keyword in gpu_name_upper:
                return gfx_version, info['name'], info.get('has_torch', False)
    
    return None, None, False


def print_supported_amd_gpu_types():
    """打印当前支持的 AMD 显卡类型"""
    print('当前支持的 AMD 显卡类型:')
    print('  - MI300A / MI300X (gfx94X-dcgpu)')
    print('  - MI350X / MI355X (gfx950-dcgpu)')
    print('  - RX 7900 XTX / RX 7800 XT / RX 7700S (Framework Laptop 16) (gfx110X-dgpu)')
    print('  - AMD Strix Halo iGPU (gfx1151)')
    print('  - RX 9060 / RX 9060 XT / RX 9070 / RX 9070 XT (gfx120X-all)')
    print('  ⚠️  Windows 版 ROCm 7.2 PyTorch 需要 AMD 显卡驱动 26.1.1')


def choose_when_amd_unsupported():
    """AMD 不支持时给用户选择，默认 CPU"""
    print('')
    print('⚠️  当前显卡不在 AMD ROCm PyTorch 支持列表中。')
    print_supported_amd_gpu_types()
    print('')
    print('请选择:')
    print('  [1] 使用 CPU 版本 (默认, 推荐)')
    print('  [2] 强制安装 AMD 版本 (实验性, 可能失败)')
    print('  [3] 退出安装')
    print('')

    while True:
        choice = input('请选择 (1/2/3, 默认1): ').strip()
        if choice in ['', '1']:
            return 'cpu'
        elif choice == '2':
            return 'force_amd'
        elif choice == '3':
            return 'exit'
        else:
            print('无效输入,请输入 1, 2 或 3')


def detect_installed_pytorch_version():
    """检测当前安装的PyTorch版本类型(CPU/GPU/Metal)"""
    try:
        # 在子进程中检测，避免在主进程中加载 torch DLL
        # 这样可以在需要时卸载 torch
        code = """
import sys
try:
    import torch
    # 检查 AMD ROCm
    if hasattr(torch.version, 'hip') and torch.version.hip:
        print(f"AMD|ROCm {torch.version.hip}")
    # 检查 NVIDIA CUDA
    elif torch.cuda.is_available():
        cuda_version = torch.version.cuda
        if cuda_version:
            print(f"GPU|CUDA {cuda_version}")
        else:
            print("GPU|Unknown CUDA")
    # 检查 Apple Silicon Metal (MPS)
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        print("Metal|MPS")
    else:
        print("CPU|CPU-only")
except (ImportError, AttributeError):
    print("None|未安装")
except OSError as e:
    print(f"None|安装损坏: {e}")
"""
        result = subprocess.run(
            [python, '-c', code],
            capture_output=True,
            text=True,
            timeout=10,
            encoding='utf-8',
            errors='ignore'
        )
        
        if result.returncode == 0:
            output = result.stdout.strip()
            if '|' in output:
                pytorch_type, detail = output.split('|', 1)
                # 子进程输出 "None|未安装" 是字符串，转成真正的 None（未安装不算版本不匹配）
                if pytorch_type == 'None':
                    return None, detail
                return pytorch_type, detail

        return None, "检测失败"
    except Exception:
        return None, "检测失败"


def get_requirements_file_from_env():
    """从当前虚拟环境检测应该使用哪个依赖方案 (cpu/gpu/amd/metal)"""
    pytorch_type, detail = detect_installed_pytorch_version()
    
    if pytorch_type == "GPU":
        return 'gpu', pytorch_type, detail
    elif pytorch_type == "Metal":
        return 'metal', pytorch_type, detail
    elif pytorch_type == "AMD":
        return 'amd', pytorch_type, detail
    elif pytorch_type == "CPU":
        return 'cpu', pytorch_type, detail
    else:
        # 未安装PyTorch,返回None让后续逻辑自动检测
        return None, None, detail


def prepare_environment(args):
    """准备运行环境
    
    返回: (use_amd_pytorch, amd_gfx_version) - 是否使用AMD PyTorch及其gfx版本
    """
    
    if args.frozen:
        print('frozen模式: 跳过依赖安装')
        return False, None

    # 确保 packaging 已安装 (需要 < 25.0 版本)
    try:
        import packaging
        import packaging.version
        import packaging.utils
        # 检查是否有 packaging.requirements (在 25.0 中已移除)
        try:
            from packaging.requirements import Requirement
        except (ImportError, AttributeError):
            # packaging 版本过高,需要降级
            print('检测到 packaging 版本不兼容,正在安装兼容版本...')
            run_pip("install 'packaging<25.0'", "packaging")
            import packaging
            import packaging.version
            import packaging.utils
            print('✓ packaging 安装成功')
    except (ModuleNotFoundError, ImportError):
        print('正在安装 packaging 模块...')
        run_pip("install 'packaging<25.0'", "packaging")
        try:
            import packaging
            import packaging.version
            import packaging.utils
            print('✓ packaging 安装成功')
        except (ModuleNotFoundError, ImportError):
            print('✗ 警告: packaging 安装失败')

    print('\n正在检查依赖...\n')
    
    # 将 packaging 目录添加到 Python 路径，以便导入 build_utils
    packaging_dir = PATH_ROOT / 'packaging'
    if str(packaging_dir) not in sys.path:
        sys.path.insert(0, str(packaging_dir))
    
    # 导入依赖检查工具
    try:
        from build_utils.package_checker import check_reqs
        check_variant_deps = lambda v: check_reqs(get_variant_packages(v))
        print('✓ 依赖检查工具加载成功')
    except ImportError as e:
        print(f'✗ 警告: 无法导入依赖检查工具')
        print(f'   原因: {e}')
        print('   将跳过增量检查,强制重新安装所有依赖')
        check_variant_deps = lambda v: False

    # 检测GPU并选择对应的依赖文件
    gpu_type, gpu_name, cuda_major, cuda_version, driver_version = detect_gpu()
    print(f'\n检测到的计算设备: {gpu_type}')
    if gpu_name:
        print(f'显卡型号: {gpu_name}')
    if cuda_version:
        print(f'CUDA 版本: {cuda_version}')
        if driver_version:
            print(f'驱动版本: {driver_version}')
    print()
    
    # 根据GPU类型选择requirements文件
    use_amd_pytorch = False  # 初始化AMD PyTorch标志
    amd_gfx_version = None    # 初始化gfx版本
    
    if args.requirements != 'auto':
        # 用户手动指定,尊重用户选择
        requirements_file = normalize_variant(args.requirements)
        if requirements_file is None:
            print(f'错误: 无效的依赖方案 "{args.requirements}"，可选: {", ".join(DEP_VARIANTS)}')
            return False, None
        # 如果手动指定了 amd 方案，需要检测 gfx 版本并安装 AMD PyTorch
        if requirements_file == 'amd':
            use_amd_pytorch = True
            detected_installed_amd = False
            # 尝试从环境中检测已安装的 AMD PyTorch 版本（在子进程中检测）
            try:
                code = """
import sys
try:
    import torch
    if hasattr(torch.version, 'hip') and torch.version.hip:
        print(f"installed|{torch.version.hip}")
    else:
        print("not_amd|")
except:
    print("not_installed|")
"""
                result = subprocess.run(
                    [python, '-c', code],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    encoding='utf-8',
                    errors='ignore'
                )
                
                if result.returncode == 0:
                    output = result.stdout.strip()
                    if output.startswith('installed|'):
                        detected_installed_amd = True
                        rocm_version = output.split('|')[1]
                        # 已安装 AMD ROCm PyTorch，获取版本信息
                        print(f'\n检测到已安装 AMD ROCm PyTorch')
                        print(f'ROCm 版本: {rocm_version}')
                        print('')
                        
                        # 询问是否更新
                        update_choice = input('是否更新 AMD ROCm PyTorch? (y/n, 默认n): ').strip().lower()
                        if update_choice in ['y', 'yes']:
                            # 自动检测 gfx 版本
                            detected_gfx, arch_name, has_torch = detect_amd_gfx_version(gpu_name) if gpu_name else (None, None, False)
                            
                            if detected_gfx and has_torch:
                                print(f'\n自动识别架构: {arch_name}')
                                print(f'对应 gfx 版本: {detected_gfx}')
                                print('✓ 使用自动检测到的 gfx 版本')
                                amd_gfx_version = detected_gfx
                            elif detected_gfx and not has_torch:
                                print(f'\n⚠️  警告: {detected_gfx} 不支持 AMD ROCm PyTorch')
                                user_action = choose_when_amd_unsupported()
                                if user_action == 'exit':
                                    print('已取消安装，请确认显卡型号和驱动版本后重试。')
                                    sys.exit(0)
                                elif user_action == 'force_amd':
                                    print('⚠️  已选择强制安装 AMD 版本，兼容性无法保证。')
                                    use_amd_pytorch = True
                                    amd_gfx_version = detected_gfx
                                else:
                                    requirements_file = 'cpu'
                                    use_amd_pytorch = False
                            else:
                                print('\n⚠️  无法自动检测到受支持的 AMD gfx 版本')
                                user_action = choose_when_amd_unsupported()
                                if user_action == 'exit':
                                    print('已取消安装，请确认显卡型号和驱动版本后重试。')
                                    sys.exit(0)
                                elif user_action == 'force_amd':
                                    print('⚠️  已选择强制安装 AMD 版本，兼容性无法保证。')
                                    use_amd_pytorch = True
                                else:
                                    requirements_file = 'cpu'
                                    use_amd_pytorch = False
                        else:
                            use_amd_pytorch = False
            except Exception:
                # 检测失败，继续
                pass
            
            if not use_amd_pytorch:
                # 未安装或非 AMD PyTorch
                if requirements_file == 'amd':
                    if detected_installed_amd:
                        print('\n检测到已安装 AMD ROCm PyTorch，本次不更新。')
                    else:
                        print('\n未检测到 AMD ROCm PyTorch')
                        print('[INFO] 手动指定了 amd 方案，但未安装 AMD PyTorch')
                        print('[INFO] 如需安装 AMD PyTorch，请运行 步骤1-首次安装.bat')
                else:
                    print(f'\n✓ 使用: {requirements_file} (CPU版本)')
                use_amd_pytorch = False
        else:
            pass  # 不是AMD，use_amd_pytorch已在开头初始化为False
    else:
        # 自动选择
        if gpu_type == "NVIDIA":
            print('=' * 50)
            print('检测到 NVIDIA GPU')
            print('=' * 50)
            print('')
            
            # 检查 CUDA 版本
            if cuda_major is not None:
                if cuda_major < 12:
                    print('⚠️  警告: 检测到 CUDA 版本低于 12')
                    print(f'   当前 CUDA 版本: {cuda_version}')
                    print(f'   GPU 版本需要: CUDA 12.x')
                    print(f'   驱动版本要求: >= 525.60.13')
                    print('')
                    print('您的 CUDA 版本过低，无法使用 GPU 版本。')
                    print('请选择:')
                    print('  [1] 更新 NVIDIA 驱动后重新运行安装')
                    print('  [2] 使用 CPU 版本')
                    print('')
                    
                    while True:
                        choice = input('请选择 (1/2, 默认2): ').strip()
                        if choice == '1':
                            print('\n请访问 NVIDIA 官网下载最新驱动:')
                            print('https://www.nvidia.com/Download/index.aspx')
                            print('\n安装驱动后请重新运行此脚本')
                            sys.exit(0)
                        elif choice in ['', '2']:
                            requirements_file = 'cpu'
                            print(f'✓ 使用: {requirements_file} (CPU版本)')
                            break
                        else:
                            print('无效输入,请输入 1 或 2')
                else:
                    # CUDA 版本符合要求
                    print('GPU 版本需要:')
                    print('  - NVIDIA 显卡支持 CUDA 12.x')
                    print('  - 显卡驱动版本 >= 525.60.13')
                    print('')
                    print(f'✓ 您的 CUDA 版本 {cuda_version} 符合要求')
                    print('')
                    print('如果不确定,可以选择 CPU 版本(速度较慢但兼容性好)')
                    print('')
                    
                    while True:
                        choice = input('使用 GPU 版本? (y/n, 默认y): ').strip().lower()
                        if choice in ['', 'y', 'yes']:
                            requirements_file = 'gpu'
                            print(f'✓ 使用: {requirements_file} (NVIDIA CUDA)')
                            break
                        elif choice in ['n', 'no']:
                            requirements_file = 'cpu'
                            print(f'✓ 使用: {requirements_file} (CPU版本)')
                            break
                        else:
                            print('无效输入,请输入 y 或 n')
            else:
                # 无法检测 CUDA 版本
                print('⚠️  无法检测 CUDA 版本 (可能未安装 nvidia-smi)')
                print('')
                print('GPU 版本需要:')
                print('  - NVIDIA 显卡支持 CUDA 12.x')
                print('  - 显卡驱动版本 >= 525.60.13')
                print('')
                print('如果不确定,可以选择 CPU 版本(速度较慢但兼容性好)')
                print('')
                
                while True:
                    choice = input('使用 GPU 版本? (y/n, 默认y): ').strip().lower()
                    if choice in ['', 'y', 'yes']:
                        requirements_file = 'gpu'
                        print(f'✓ 使用: {requirements_file} (NVIDIA CUDA)')
                        break
                    elif choice in ['n', 'no']:
                        requirements_file = 'cpu'
                        print(f'✓ 使用: {requirements_file} (CPU版本)')
                        break
                    else:
                        print('无效输入,请输入 y 或 n')
                    
        elif gpu_type == "AMD":
            # 检测 AMD GPU 的 gfx 版本
            detected_gfx, arch_name, has_torch = detect_amd_gfx_version(gpu_name)
            
            print('=' * 50)
            print('检测到 AMD GPU')
            print('=' * 50)
            print('')
            
            if detected_gfx:
                print(f'自动识别架构: {arch_name}')
                print(f'对应 gfx 版本: {detected_gfx}')
                if not has_torch:
                    print(f'⚠️  该显卡不支持 AMD ROCm PyTorch')
                    print(f'⚠️  建议使用 CPU 版本')
            else:
                print('⚠️  无法自动识别 AMD GPU 架构')
            
            print('')
            print('AMD GPU 支持选项:')
            print('  [1] AMD ROCm GPU 版本 (实验性,需要兼容的 AMD 显卡)')
            print('  [2] CPU 版本 (推荐,兼容性好)')
            print('  ⚠️ Windows 版 ROCm 7.2 PyTorch 需要 AMD 显卡驱动 26.1.1')
            print('')
            
            if detected_gfx and has_torch:
                print(f'建议: 选择 [1] 并使用检测到的 {detected_gfx}')
            else:
                print('建议: 选择 [2] CPU 版本')
            print('')

            # 检测到不支持时：展示支持型号并给出选择（默认 CPU）
            if not (detected_gfx and has_torch):
                user_action = choose_when_amd_unsupported()
                if user_action == 'exit':
                    print('已取消安装，请确认显卡型号和驱动版本后重试。')
                    sys.exit(0)
                elif user_action == 'force_amd':
                    requirements_file = 'amd'
                    use_amd_pytorch = True
                    amd_gfx_version = detected_gfx
                    print('⚠️  已选择强制安装 AMD 版本，兼容性无法保证。')
                    print(f'✓ 使用: {requirements_file} (AMD 强制安装)')
                else:
                    requirements_file = 'cpu'
                    use_amd_pytorch = False
                    print(f'✓ 使用: {requirements_file} (CPU版本)')
            else:
                while True:
                    choice = input('请选择 (1/2, 默认2): ').strip()
                    if choice == '1':
                        amd_gfx_version = detected_gfx
                        requirements_file = 'amd'  # 使用专用的 AMD 依赖方案
                        use_amd_pytorch = True
                        print(f'✓ 自动识别并使用: {amd_gfx_version}')
                        print(f'✓ 将使用 AMD ROCm PyTorch ({amd_gfx_version})')
                        print(f'✓ 依赖文件: {requirements_file}')
                        break
                    elif choice in ['', '2']:
                        requirements_file = 'cpu'
                        print(f'✓ 使用: {requirements_file} (CPU版本)')
                        break
                    else:
                        print('无效输入,请输入 1 或 2')
                    
        elif gpu_type == "AppleSilicon":
            # Apple Silicon Mac，使用 Metal 加速
            print('=' * 50)
            print('检测到 Apple Silicon')
            print('=' * 50)
            print('')
            if gpu_name:
                print(f'芯片型号: {gpu_name}')
            print('')
            print('✓ Apple Silicon 支持 Metal 加速')
            print('✓ 将使用 Metal 版本以获得最佳性能')
            print('')
            requirements_file = 'metal'
            print(f'✓ 使用: {requirements_file} (Apple Metal)')
                    
        elif gpu_type == "CPU":
            # 自动检测失败,让用户手动选择
            print('=' * 50)
            print('⚠️  无法自动检测显卡类型')
            print('=' * 50)
            print('')
            print('请手动选择安装版本:')
            print('  [1] NVIDIA GPU 版本 (CUDA) - 需要 NVIDIA 显卡')
            print('  [2] AMD GPU 版本 (ROCm) - 需要兼容的 AMD 显卡')
            print('  [3] CPU 版本 - 兼容所有电脑')
            print('')
            
            while True:
                choice = input('请选择 (1/2/3, 默认3): ').strip()
                if choice == '1':
                    requirements_file = 'gpu'
                    print(f'✓ 使用: {requirements_file} (NVIDIA CUDA)')
                    break
                elif choice == '2':
                    # AMD GPU（纯自动检测）
                    print('')
                    print('✓ 支持 PyTorch 的 AMD gfx 版本:')
                    print('  - gfx94X-dcgpu: MI300A / MI300X')
                    print('  - gfx950-dcgpu: MI350X / MI355X')
                    print('  - gfx110X-dgpu: RX 7900 XTX / RX 7800 XT / RX 7700S (Framework Laptop 16)')
                    print('  - gfx1151:      AMD Strix Halo iGPU')
                    print('  - gfx120X-all:  RX 9060 / RX 9060 XT / RX 9070 / RX 9070 XT')
                    print('')
                    print('✗ 不支持 PyTorch 的版本:')
                    print('  - gfx101X-dgpu: RX 5000 系列')
                    print('  - gfx103X-dgpu: RX 6000 系列')
                    print('  - gfx90X-dcgpu: Vega / Radeon VII')
                    print('')

                    detected_gfx, arch_name, has_torch = detect_amd_gfx_version(gpu_name) if gpu_name else (None, None, False)
                    if detected_gfx and has_torch:
                        amd_gfx_version = detected_gfx
                        requirements_file = 'amd'
                        use_amd_pytorch = True
                        print(f'✓ 自动识别架构: {arch_name}')
                        print(f'✓ 将使用 AMD ROCm PyTorch ({amd_gfx_version})')
                        print(f'✓ 依赖文件: {requirements_file}')
                        break
                    else:
                        user_action = choose_when_amd_unsupported()
                        if user_action == 'exit':
                            print('已取消安装，请确认显卡型号和驱动版本后重试。')
                            sys.exit(0)
                        elif user_action == 'force_amd':
                            requirements_file = 'amd'
                            use_amd_pytorch = True
                            amd_gfx_version = detected_gfx
                            print('⚠️  已选择强制安装 AMD 版本，兼容性无法保证。')
                            print(f'✓ 使用: {requirements_file} (AMD 强制安装)')
                        else:
                            requirements_file = 'cpu'
                            use_amd_pytorch = False
                            print(f'✓ 使用: {requirements_file} (CPU版本)')
                        break
                elif choice in ['', '3']:
                    requirements_file = 'cpu'
                    print(f'✓ 使用: {requirements_file} (CPU版本)')
                    break
                else:
                    print('无效输入,请输入 1, 2 或 3')
                    
        else:
            # Intel GPU - 在 Windows 上支持有限,推荐使用 CPU 版本
            print('=' * 50)
            print('检测到 Intel GPU')
            print('=' * 50)
            print('')
            print('⚠️  Intel GPU 在 PyTorch 上的支持有限')
            print('推荐使用 CPU 版本以获得最佳兼容性')
            print('')
            print('请选择:')
            print('  [1] NVIDIA GPU 版本 (如果有独立显卡)')
            print('  [2] CPU 版本 (推荐)')
            print('')
            
            while True:
                choice = input('请选择 (1/2, 默认2): ').strip()
                if choice == '1':
                    requirements_file = 'gpu'
                    print(f'✓ 使用: {requirements_file} (NVIDIA CUDA)')
                    break
                elif choice in ['', '2']:
                    requirements_file = 'cpu'
                    print(f'✓ 使用: {requirements_file} (CPU版本)')
                    break
                else:
                    print('无效输入,请输入 1 或 2')
    
    # 选择对应的PyTorch版本 (根据 pyproject.toml 中 gpu extra 的版本)
    # 注意: 不再单独安装 PyTorch，而是通过 requirements 文件统一安装
    # 这样可以避免版本冲突和 DLL 损坏问题
    
    # 检查是否需要卸载不匹配的 PyTorch 版本
    need_reinstall = args.reinstall_torch
    
    if not need_reinstall:
        # 检测当前安装的 PyTorch 类型
        installed_pytorch_type, installed_detail = detect_installed_pytorch_version()
        requirements_lower = requirements_file.lower()
        if "amd" in requirements_lower:
            target_type = "AMD"
        elif "metal" in requirements_lower:
            target_type = "Metal"
        elif "gpu" in requirements_lower:
            target_type = "GPU"
        else:
            target_type = "CPU"
        
        if installed_pytorch_type is not None and installed_pytorch_type != target_type:
            print('\n' + '=' * 50)
            print('⚠️  警告: 检测到 PyTorch 版本不匹配')
            print('=' * 50)
            print(f'当前安装: {installed_pytorch_type} 版本 ({installed_detail})')
            print(f'目标版本: {target_type} 版本')
            print('')
            print('不同版本的 PyTorch 会导致 DLL 冲突和加载失败')
            print('建议卸载旧版本后重新安装')
            print('')
            need_reinstall = True
    
    # 如果需要重装 PyTorch，先卸载
    if need_reinstall or use_amd_pytorch:
        print('正在卸载现有的 PyTorch...')
        print('[提示] 请确保没有其他 Python 进程正在运行')
        
        # 尝试多次卸载，处理文件占用问题
        max_retries = 3
        for retry in range(max_retries):
            try:
                run(f'"{python}" -m pip uninstall torch torchvision torchaudio -y', "卸载 PyTorch", "无法卸载 PyTorch", live=True)
                break
            except Exception as e:
                if retry < max_retries - 1:
                    print(f'卸载失败（尝试 {retry + 1}/{max_retries}），可能有文件被占用')
                    print('请关闭所有使用 PyTorch 的程序，然后按回车继续...')
                    input()
                else:
                    print(f'警告: PyTorch 卸载失败，将尝试强制覆盖安装')
                    print(f'错误: {e}')
        
        # 强制清理 pip 缓存，避免使用缓存的错误版本
        print('正在清理 pip 缓存...')
        try:
            run(f'"{python}" -m pip cache purge', "清理缓存", "无法清理缓存")
        except:
            pass
    
    # 如果用户选择了 AMD ROCm PyTorch，先安装它
    if use_amd_pytorch:
        print('\n' + '=' * 50)
        print('正在安装 AMD ROCm PyTorch')
        print('=' * 50)
        if amd_gfx_version:
            print(f'gfx 版本: {amd_gfx_version}')
        print('模式: ROCm SDK 7.2 固定 URL 安装')
        print('⚠️  前置要求: Windows 版 ROCm 7.2 PyTorch 必须安装 AMD 显卡驱动 26.1.1')
        print('')

        # 第1步：先安装 ROCm SDK 依赖
        rocm_sdk_urls = [
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm_sdk_core-7.2.0.dev0-py3-none-win_amd64.whl",
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm_sdk_devel-7.2.0.dev0-py3-none-win_amd64.whl",
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm_sdk_libraries_custom-7.2.0.dev0-py3-none-win_amd64.whl",
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/rocm-7.2.0.dev0.tar.gz",
        ]

        # 第2步：再安装 PyTorch 三件套
        rocm_torch_urls = [
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/torch-2.9.1%2Brocmsdk20260116-cp312-cp312-win_amd64.whl",
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/torchaudio-2.9.1%2Brocmsdk20260116-cp312-cp312-win_amd64.whl",
            "https://repo.radeon.com/rocm/windows/rocm-rel-7.2/torchvision-0.24.1%2Brocmsdk20260116-cp312-cp312-win_amd64.whl",
        ]

        sdk_urls_str = " ".join([f'"{u}"' for u in rocm_sdk_urls])
        torch_urls_str = " ".join([f'"{u}"' for u in rocm_torch_urls])
        install_sdk_cmd = f'"{python}" -m pip install --no-cache-dir {sdk_urls_str}'
        install_torch_cmd = f'"{python}" -m pip install --no-cache-dir {torch_urls_str}'

        try:
            run(install_sdk_cmd, "步骤 1/2: 安装 AMD ROCm SDK 依赖", "AMD ROCm SDK 依赖安装失败", live=True)
            run(install_torch_cmd, "步骤 2/2: 安装 AMD ROCm PyTorch", "AMD ROCm PyTorch 安装失败", live=True)
            print('\n✓ AMD ROCm PyTorch 安装完成')
            print('\n⚠️  注意:')
            print('  - AMD ROCm PyTorch 是实验性功能')
            print('  - 首次运行可能需要编译某些操作')
            print('  - 如果遇到问题,请使用 CPU 版本')
        except Exception as e:
            print(f'\n✗ AMD ROCm PyTorch 安装失败: {e}')
            print('\n建议:')
            print('  1. 检查网络连接')
            print('  2. 确认 Python 版本为 3.12')
            print('  3. 如果仍有问题,请使用 CPU 版本重新安装')
            # 安装失败，返回失败状态
            return False, None

    # 检查并安装其他依赖
    if not PYPROJECT_FILE.exists():
        print(f'警告: 未找到 {PYPROJECT_FILE}')
        return False, None

    print(f'\n正在检查依赖方案: {requirements_file}')
    if not check_variant_deps(requirements_file) or need_reinstall:
        if need_reinstall:
            print(f'强制重新安装所有依赖...')
            # 只有 AMD 用户才会在前面单独安装 PyTorch，其他用户需要从 requirements 安装
            if use_amd_pytorch:
                print('跳过依赖方案中的 PyTorch（AMD ROCm 已单独安装）')
                # 排除 PyTorch 及其生态包（这些包依赖 torch，会触发 torch 安装）
                pytorch_related = ['torch', 'torchvision', 'torchaudio', 'xformers', 'torchsummary', 'open_clip_torch']
                run_pip_requirements(requirements_file, f"{requirements_file} 方案依赖（跳过PyTorch）", exclude_packages=pytorch_related)
            else:
                run_pip_requirements(requirements_file, f"{requirements_file} 方案依赖")
        else:
            print(f'发现缺失依赖,正在安装...')
            # 使用逐个包安装，失败时从失败的包开始切换镜像重试
            run_pip_requirements(requirements_file, f"{requirements_file} 方案依赖")
    else:
        print(f'依赖已满足 ✓')
    
    # 自动设置 AMD APU 越狱环境变量 (HSA Override)
    if use_amd_pytorch:
        target_gfx = amd_gfx_version
        if not target_gfx and gpu_name:
            name_upper = gpu_name.upper()
            if any(kw in name_upper for kw in ['780M', '760M', '740M']):
                target_gfx = 'gfx1103'
            elif any(kw in name_upper for kw in ['890M', '880M', '860M']):
                target_gfx = 'gfx1150'
                
        if target_gfx in ['gfx1103', 'gfx1150']:
            print('\n[INFO] 检测到 AMD APU 核显 (780M/890M等)')
            print(f'       正在自动注入架构越狱环境变量: HSA_OVERRIDE_GFX_VERSION={HSA_APU_JAILBREAK_VERSION} 以启用核显加速...')
            print('       [提示] 核显显存优化建议:')
            print('              由于 AMD 核显共享内存机制，若您的 BIOS 预分配显存过低（如 512MB），')
            print('              运行本地模型时极易遭遇 "Out of Memory" 报错或卡顿。')
            print('              💡 建议：进入您的电脑 BIOS 或品牌控制软件，')
            print('              将 "UMA Frame Buffer Size" (预分配显存) 修改为 4G、8G 或更高，')
            print('              以获得最稳定、最流畅的核显加速体验！\n')
            os.environ['HSA_OVERRIDE_GFX_VERSION'] = HSA_APU_JAILBREAK_VERSION

    # 返回 AMD PyTorch 相关信息
    return use_amd_pytorch, amd_gfx_version


def update_repository(args):
    """更新代码库"""
    if getattr(sys, 'frozen', False):
        print('打包版本,跳过更新检查')
        return False

    if not args.update:
        return False

    print('正在检查更新...')
    try:
        current_commit = commit_hash()
        run(f"{git} fetch origin {BRANCH}", desc="正在从远程拉取更新...", errdesc="拉取更新失败")
        latest_commit = run(f"{git} rev-parse origin/{BRANCH}").strip()

        if current_commit != latest_commit:
            print("发现新版本,正在更新...")
            run(f"{git} pull origin {BRANCH}", desc="正在更新代码库...", errdesc="更新失败")
            print("更新完成,正在重启应用...")
            restart()
            return True
        else:
            print("已是最新版本")
    except Exception as e:
        print(f"更新检查失败: {e}")
        print("继续使用当前版本")
    
    return False


# ============================================================
# Git 镜像源 / 分支 / 版本(tag) 管理
# ============================================================
GIT_MIRRORS = [
    ('GitHub 官方', 'GitHub official', 'https://github.com/hgmzhn/manga-translator-ui.git'),
    ('Gitee 镜像 (国内推荐)', 'Gitee mirror (recommended in China)', 'https://gitee.com/hgmzhn/manga-translator-ui.git'),
]

SUPPORTED_BRANCHES = ['main', 'beta']

# 维护菜单语言配置（持久化在 packaging/maintenance_config.json）
MAINT_CONFIG_FILE = PATH_ROOT / 'packaging' / 'maintenance_config.json'
LANG = 'zh'


def load_maint_config():
    try:
        import json
        with open(MAINT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_maint_config(**updates):
    try:
        import json
        cfg = load_maint_config()
        cfg.update(updates)
        with open(MAINT_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def L(zh, en):
    """双语文案：按当前语言返回中文或英文"""
    return zh if LANG == 'zh' else en


def init_language():
    """首次运行询问语言并保存，之后从配置文件读取"""
    global LANG
    lang = load_maint_config().get('language')
    if lang in ('zh', 'en'):
        LANG = lang
        return
    print()
    print('请选择语言 / Select language:')
    print('[1] 中文')
    print('[2] English')
    choice = input('(1/2, 默认1 / default 1): ').strip()
    LANG = 'en' if choice == '2' else 'zh'
    save_maint_config(language=LANG)


def switch_language():
    """中英文互切并持久化"""
    global LANG
    LANG = 'en' if LANG == 'zh' else 'zh'
    save_maint_config(language=LANG)
    print(L('[OK] 已切换为中文', '[OK] Language switched to English'))



def _git_output(cmd_args, timeout=15):
    """执行 git 命令并返回 stdout（失败返回 None）"""
    try:
        result = subprocess.run(
            [git] + cmd_args,
            capture_output=True, text=True, check=False, timeout=timeout,
            encoding='utf-8', errors='ignore'
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_remote_url():
    """获取当前 origin 远程地址"""
    return _git_output(['config', '--get', 'remote.origin.url']) or ''


def get_mirror_display_name(url=None):
    """把远程地址转成可读的镜像源名称"""
    url = (url if url is not None else get_remote_url()).strip()
    normalized = url.removesuffix('.git')
    for zh_name, en_name, mirror_url in GIT_MIRRORS:
        if normalized == mirror_url.removesuffix('.git'):
            return L(zh_name, en_name)
    return url or L('未配置', 'not configured')


def get_current_branch():
    """返回 (分支名或tag名, 是否游离状态)"""
    branch = _git_output(['rev-parse', '--abbrev-ref', 'HEAD'])
    if branch and branch != 'HEAD':
        return branch, False
    tag = _git_output(['describe', '--tags', '--exact-match'])
    if tag:
        return tag, True
    commit = _git_output(['rev-parse', '--short', 'HEAD']) or 'unknown'
    return commit, True


def get_update_branch():
    """获取用于更新比对的分支（游离状态或未知分支时回落到 main）"""
    branch, detached = get_current_branch()
    if not detached and branch in SUPPORTED_BRANCHES:
        return branch
    return 'main'


def switch_mirror():
    """切换 git 镜像源，返回是否切换成功"""
    current = get_remote_url().removesuffix('.git')
    print()
    print("=" * 40)
    print(L("切换镜像源", "Switch Mirror"))
    print("=" * 40)
    for i, (zh_name, en_name, url) in enumerate(GIT_MIRRORS, 1):
        mark = L('  (当前)', '  (current)') if url.removesuffix('.git') == current else ''
        print(f"[{i}] {L(zh_name, en_name)}: {url}{mark}")
    print(L(f"[{len(GIT_MIRRORS) + 1}] 手动输入仓库地址",
            f"[{len(GIT_MIRRORS) + 1}] Enter repository URL manually"))
    print()
    choice = input(L(f"请选择 (1-{len(GIT_MIRRORS) + 1}, 回车取消): ",
                     f"Select (1-{len(GIT_MIRRORS) + 1}, Enter to cancel): ")).strip()
    if not choice:
        print(L("已取消", "Cancelled"))
        return False
    if not choice.isdigit():
        print(L("无效选项", "Invalid option"))
        return False
    idx = int(choice)
    if 1 <= idx <= len(GIT_MIRRORS):
        new_url = GIT_MIRRORS[idx - 1][2]
    elif idx == len(GIT_MIRRORS) + 1:
        new_url = input(L("请输入仓库地址: ", "Repository URL: ")).strip()
        if not new_url:
            print(L("已取消", "Cancelled"))
            return False
    else:
        print(L("无效选项", "Invalid option"))
        return False
    result = subprocess.run([git, 'remote', 'set-url', 'origin', new_url], capture_output=True)
    if result.returncode == 0:
        print(L(f"[OK] 镜像源已切换为: {get_mirror_display_name(new_url)}",
                f"[OK] Mirror switched to: {get_mirror_display_name(new_url)}"))
        return True
    print(L("[错误] 切换镜像源失败", "[ERROR] Failed to switch mirror"))
    return False


def git_fetch_with_mirror_prompt(fetch_args=None, desc=None):
    """git fetch，失败时推荐切换到另一条线路并重试"""
    while True:
        print((desc or L('获取远程更新', 'Fetching remote updates')) + '...')
        try:
            result = subprocess.run([git, 'fetch', 'origin'] + (fetch_args or []), check=False, timeout=300)
            if result.returncode == 0:
                return True
        except Exception:
            pass
        print(L("[错误] 同步失败（网络问题或当前镜像源不可用）",
                "[ERROR] Sync failed (network issue or current mirror unavailable)"))
        print(L(f"当前镜像源: {get_mirror_display_name()}",
                f"Current mirror: {get_mirror_display_name()}"))
        # 直接推荐另一条线路（GitHub 失败推荐 Gitee，反之亦然）
        current = get_remote_url().removesuffix('.git')
        suggestion = None
        for zh_name, en_name, url in GIT_MIRRORS:
            if url.removesuffix('.git') != current:
                suggestion = (L(zh_name, en_name), url)
                break
        if suggestion:
            choice = input(L(f"是否切换到 {suggestion[0]} 并重试? (y/n, 默认y): ",
                             f"Switch to {suggestion[0]} and retry? (y/n, default y): ")).strip().lower()
            if choice in ['', 'y', 'yes']:
                subprocess.run([git, 'remote', 'set-url', 'origin', suggestion[1]], capture_output=True)
                print(L(f"[OK] 已切换到 {suggestion[0]}", f"[OK] Switched to {suggestion[0]}"))
                continue
        else:
            choice = input(L("是否切换镜像源并重试? (y/n, 默认y): ",
                             "Switch mirror and retry? (y/n, default y): ")).strip().lower()
            if choice in ['', 'y', 'yes']:
                if switch_mirror():
                    continue
        return False


def switch_branch():
    """切换分支 (main/beta)，切换后强制同步到远程"""
    branch, detached = get_current_branch()
    print()
    print("=" * 40)
    print(L("切换分支", "Switch Branch"))
    print("=" * 40)
    note = L(" (tag/游离状态)", " (tag/detached)") if detached else ""
    print(L(f"当前: {branch}{note}", f"Current: {branch}{note}"))
    print()
    for i, b in enumerate(SUPPORTED_BRANCHES, 1):
        desc = L('稳定版', 'stable') if b == 'main' else L('测试版', 'beta/testing')
        mark = L('  (当前)', '  (current)') if (not detached and b == branch) else ''
        print(f"[{i}] {b} ({desc}){mark}")
    print()
    choice = input(L(f"请选择 (1-{len(SUPPORTED_BRANCHES)}, 回车取消): ",
                     f"Select (1-{len(SUPPORTED_BRANCHES)}, Enter to cancel): ")).strip()
    if not choice or not choice.isdigit() or not (1 <= int(choice) <= len(SUPPORTED_BRANCHES)):
        print(L("已取消", "Cancelled"))
        return False
    target = SUPPORTED_BRANCHES[int(choice) - 1]
    if not detached and target == branch:
        print(L(f"[信息] 已在 {target} 分支", f"[INFO] Already on branch {target}"))
        return False
    print()
    print(L(f"[警告] 切换到 {target} 分支将强制同步远程代码，本地修改将被覆盖",
            f"[WARNING] Switching to {target} will force-sync remote code; local changes will be overwritten"))
    confirm = input(L("是否继续? (y/n): ", "Continue? (y/n): ")).strip().lower()
    if confirm not in ['y', 'yes']:
        print(L("已取消", "Cancelled"))
        return False
    if not git_fetch_with_mirror_prompt():
        return False
    result = subprocess.run([git, 'checkout', '-f', '-B', target, f'origin/{target}'], check=False)
    if result.returncode == 0:
        print(L(f"[OK] 已切换到 {target} 分支", f"[OK] Switched to branch {target}"))
        print(L("[提示] 建议执行一次 [更新] 以同步依赖", "[HINT] Run [Update] once to sync dependencies"))
        return True
    print(L("[错误] 切换分支失败", "[ERROR] Failed to switch branch"))
    return False


def switch_version_by_tag():
    """按 tag 切换版本（切换后处于游离状态）"""
    print()
    print("=" * 40)
    print(L("切换版本 (按 tag)", "Switch Version (by tag)"))
    print("=" * 40)
    if not git_fetch_with_mirror_prompt(['--tags', '--force'], L('获取版本列表', 'Fetching version list')):
        return False
    tags_out = _git_output(['tag', '--sort=-creatordate'])
    if not tags_out:
        print(L("[信息] 仓库中没有任何版本 tag", "[INFO] No version tags found in the repository"))
        return False
    tags = [t for t in tags_out.split('\n') if t.strip()][:20]
    current_tag = _git_output(['describe', '--tags', '--exact-match'])
    print()
    for i, tag in enumerate(tags, 1):
        mark = L('  (当前)', '  (current)') if tag == current_tag else ''
        print(f"[{i}] {tag}{mark}")
    print()
    choice = input(L("请选择序号或直接输入 tag 名 (回车取消): ",
                     "Select a number or type a tag name (Enter to cancel): ")).strip()
    if not choice:
        print(L("已取消", "Cancelled"))
        return False
    if choice.isdigit() and 1 <= int(choice) <= len(tags):
        target_tag = tags[int(choice) - 1]
    else:
        target_tag = choice
    if target_tag == current_tag:
        print(L(f"[信息] 已在版本 {target_tag}", f"[INFO] Already at version {target_tag}"))
        return False
    print()
    print(L(f"[警告] 切换到版本 {target_tag} 将覆盖本地修改，并进入游离状态",
            f"[WARNING] Switching to {target_tag} will overwrite local changes and enter detached state"))
    confirm = input(L("是否继续? (y/n): ", "Continue? (y/n): ")).strip().lower()
    if confirm not in ['y', 'yes']:
        print(L("已取消", "Cancelled"))
        return False
    result = subprocess.run([git, 'checkout', '-f', target_tag], check=False)
    if result.returncode == 0:
        print(L(f"[OK] 已切换到版本 {target_tag}", f"[OK] Switched to version {target_tag}"))
        print(L("[提示] 建议执行一次 [更新] 以同步该版本的依赖",
                "[HINT] Run [Update] once to sync this version's dependencies"))
        print(L("[提示] 如需回到最新代码，请使用 [切换分支]",
                "[HINT] To return to the latest code, use [Switch branch]"))
        return True
    print(L(f"[错误] 切换版本失败，请确认 tag 名称: {target_tag}",
            f"[ERROR] Failed to switch version, please check the tag name: {target_tag}"))
    return False


def check_version_info():
    """检查版本信息（基于当前分支/镜像源）"""
    ensure_git_safe_directory()  # 确保 safe.directory 已配置
    print()
    print(L("正在检查版本...", "Checking version..."))
    print("=" * 40)

    branch, detached = get_current_branch()
    update_branch = get_update_branch()

    # 获取当前版本
    version_file = PATH_ROOT / "packaging" / "VERSION"
    try:
        if version_file.exists():
            current_version = version_file.read_text(encoding='utf-8').strip()
        else:
            current_version = "unknown"
    except Exception:
        current_version = "unknown"

    note = L(" (tag/游离状态)", " (tag/detached)") if detached else ""
    print(L(f"当前分支 - {branch}{note}", f"Branch  - {branch}{note}"))
    print(L(f"镜像源   - {get_mirror_display_name()}", f"Mirror  - {get_mirror_display_name()}"))

    # fetch远程（静默，失败不中断）
    try:
        subprocess.run([git, 'fetch', 'origin'], capture_output=True, check=False, timeout=30)
    except Exception:
        pass

    # 获取远程版本（按当前分支比对；游离状态按 main 比对）
    remote_version = _git_output(['show', f'origin/{update_branch}:packaging/VERSION']) or "unknown"
    behind = _git_output(['rev-list', '--count', f'HEAD..origin/{update_branch}'])

    print(L(f"当前版本 - {current_version}", f"Local   - {current_version}"))
    print(L(f"远程版本 - {remote_version} (origin/{update_branch})",
            f"Remote  - {remote_version} (origin/{update_branch})"))

    if remote_version == "unknown":
        print()
        print(L("[警告] 无法获取远程版本信息（网络或镜像源问题）",
                "[WARNING] Failed to get remote version (network or mirror issue)"))
        print(L("       可在菜单中使用 [切换镜像源] 后重试",
                "        Try [Switch mirror] in the menu and retry"))
    elif current_version == remote_version and behind in (None, '0'):
        print()
        print(L("[信息] 当前已是最新版本", "[INFO] Already up to date"))
    else:
        print()
        if behind and behind != '0':
            print(L(f"[发现新版本] 当前落后远程 {behind} 个提交",
                    f"[NEW VERSION] {behind} commit(s) behind remote"))
        else:
            print(L("[发现新版本]", "[NEW VERSION available]"))

    print("=" * 40)
    return current_version, remote_version


def update_code_force(skip_confirm=False, target_branch=None):
    """强制更新代码（同步到远程分支），同步失败时提示切换镜像源重试

    Args:
        skip_confirm: 是否跳过确认提示（用于完整更新流程中）
        target_branch: 目标分支（默认当前分支，游离状态回落 main）
    """
    ensure_git_safe_directory()  # 确保 safe.directory 已配置
    branch = target_branch or get_update_branch()
    print()
    print("=" * 40)
    print(L(f"更新代码 (强制同步到 origin/{branch})", f"Updating code (force sync to origin/{branch})"))
    print("=" * 40)
    print()

    if not skip_confirm:
        print(L("[警告] 将强制同步到远程分支,本地修改将被覆盖",
                "[WARNING] This will force-sync to the remote branch; local changes will be overwritten"))
        confirm = input(L("是否继续更新? (y/n): ", "Continue? (y/n): ")).strip().lower()
        if confirm not in ['y', 'yes']:
            print(L("取消更新", "Update cancelled"))
            return False

    print()
    if not git_fetch_with_mirror_prompt():
        return False

    while True:
        print()
        print(L(f"正在强制同步到 origin/{branch}...", f"Force syncing to origin/{branch}..."))
        result = subprocess.run([git, 'checkout', '-f', '-B', branch, f'origin/{branch}'], check=False)
        if result.returncode == 0:
            print(L("[OK] 代码更新完成", "[OK] Code updated"))
            break
        print(L("[错误] 同步失败", "[ERROR] Sync failed"))
        print(L(f"当前镜像源: {get_mirror_display_name()}", f"Current mirror: {get_mirror_display_name()}"))
        choice = input(L("是否切换镜像源并重试? (y/n, 默认n): ",
                         "Switch mirror and retry? (y/n, default n): ")).strip().lower()
        if choice in ['y', 'yes']:
            if switch_mirror() and git_fetch_with_mirror_prompt():
                continue
        return False

    # 清理平台特定文件
    import platform

    if platform.system() == 'Windows':
        # Windows 环境清理 macOS 文件
        files_to_remove = [
            'macOS_1_首次安装.sh',
            'macOS_2_启动Qt界面.sh',
            'macOS_3_检查更新并启动.sh',
            'macOS_4_更新维护.sh',
            'macOS_common.sh',
            '.gitattributes',
            '.gitignore',
            'LICENSE.txt'
        ]
    elif platform.system() == 'Darwin':
        # macOS 环境清理 Windows 文件
        files_to_remove = [
            '1-Start.ps1',
            '2-Install-or-Update.ps1',
            '3-Uninstall-Legacy.ps1',
            '.gitattributes',
            '.gitignore',
            'LICENSE.txt'
        ]
    else:
        files_to_remove = []

    for file in files_to_remove:
        if os.path.exists(file):
            try:
                os.remove(file)
            except Exception:
                pass  # 忽略删除失败

    return True


def update_dependencies(args):
    """更新依赖"""
    print()
    print("=" * 40)
    print("更新/安装依赖")
    print("=" * 40)
    print()
    
    # 设置参数，让 prepare_environment 处理所有逻辑
    args.update_deps = True
    args.frozen = False
    args.reinstall_torch = False
    
    # 检测已安装的 PyTorch 类型来决定 requirements 文件
    req_file, pytorch_type, detail = get_requirements_file_from_env()
    if req_file:
        args.requirements = req_file
        print(f"检测到 PyTorch 类型: {pytorch_type} ({detail})")
        print(f"使用: {req_file}")
    else:
        args.requirements = 'auto'
        print("未检测到 PyTorch,将进行首次安装...")
    
    print()
    
    try:
        prepare_environment(args)
        print()
        print("[OK] 依赖更新完成")
        return True
    except Exception as e:
        print(f"[ERROR] 依赖更新失败: {e}")
        return False


def update_dependencies_selective(args, missing_packages):
    """只更新/安装缺失的依赖包
    
    正确处理 PyTorch 相关包需要从专门源下载的逻辑
    安装前会检查包是否已安装，避免重复安装
    """
    print()
    print("=" * 40)
    print("安装缺失依赖")
    print("=" * 40)
    print()
    
    if not missing_packages:
        print("[信息] 没有缺失的依赖包")
        return True
    
    # 导入依赖检查工具
    packaging_dir = PATH_ROOT / 'packaging'
    if str(packaging_dir) not in sys.path:
        sys.path.insert(0, str(packaging_dir))
    
    try:
        from build_utils.package_checker import _check_req
        from packaging.requirements import Requirement
        has_checker = True
    except ImportError:
        has_checker = False
        print("[警告] 无法导入依赖检查工具，将不进行安装前检查")
    
    # 从 pyproject.toml 读取 PyTorch 源
    primary_index_url = None
    req_file = getattr(args, 'requirements', None)
    try:
        primary_index_url = get_variant_index_url(req_file)
        if primary_index_url:
            print(f"检测到 PyTorch 源: {primary_index_url}")
    except Exception:
        pass
    
    # 安装前再过滤一遍已满足的包
    to_install = []
    skip_count = 0
    for pkg in missing_packages:
        if has_checker:
            try:
                if _check_req(Requirement(pkg)):
                    print(f"{_dep_base_name(pkg)} 已安装，跳过")
                    skip_count += 1
                    continue
            except Exception:
                pass
        to_install.append(pkg)

    if not to_install:
        print("[信息] 所有包均已满足，无需安装")
        return True

    print(f"共需要安装 {len(to_install)} 个包 (跳过 {skip_count} 个)")
    print()

    try:
        run_pip_packages(to_install, primary_index_url, "缺失依赖")
        print()
        print("=" * 40)
        print(f"安装完成: {len(to_install)} 个包")
        print("=" * 40)
        return True
    except Exception as e:
        print(f"[失败] 安装缺失依赖失败: {e}")
        return False


def check_all_updates():
    """检查所有更新（代码+依赖）并返回检查结果"""
    ensure_git_safe_directory()
    update_branch = get_update_branch()
    print()
    print("=" * 40)
    print("正在检查所有更新...")
    print("=" * 40)
    print()
    
    # 1. 检查代码版本和提交
    print(f"[1/2] 检查代码版本... (分支: {update_branch}, 镜像: {get_mirror_display_name()})")
    version_file = PATH_ROOT / "packaging" / "VERSION"
    try:
        if version_file.exists():
            current_version = version_file.read_text(encoding='utf-8').strip()
        else:
            current_version = "unknown"
    except Exception:
        current_version = "unknown"
    
    # fetch远程
    try:
        subprocess.run([git, 'fetch', 'origin'], capture_output=True, check=False, timeout=10)
    except Exception:
        pass
    
    # 获取远程版本
    try:
        result = subprocess.run(
            [git, 'show', f'origin/{update_branch}:packaging/VERSION'],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        if result.returncode == 0:
            remote_version = result.stdout.strip()
        else:
            remote_version = "unknown"
    except Exception:
        remote_version = "unknown"
    
    # 获取本地和远程的 commit hash
    try:
        local_commit = subprocess.run(
            [git, 'rev-parse', 'HEAD'],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        ).stdout.strip()
    except Exception:
        local_commit = "unknown"
    
    try:
        remote_commit = subprocess.run(
            [git, 'rev-parse', f'origin/{update_branch}'],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        ).stdout.strip()
    except Exception:
        remote_commit = "unknown"
    
    # 判断是否需要更新：版本号不同 或 提交不同
    version_differs = (current_version != remote_version and remote_version != "unknown")
    commit_differs = (local_commit != remote_commit and remote_commit != "unknown" and local_commit != "unknown")
    code_needs_update = version_differs or commit_differs
    
    print(f"  当前版本: {current_version}")
    print(f"  远程版本: {remote_version}")
    print(f"  本地提交: {local_commit[:8] if local_commit != 'unknown' else 'unknown'}")
    print(f"  远程提交: {remote_commit[:8] if remote_commit != 'unknown' else 'unknown'}")
    
    if code_needs_update:
        if version_differs:
            print("  状态: [需要更新 - 版本不同]")
        else:
            print("  状态: [需要更新 - 有新提交]")
    else:
        print("  状态: [已是最新]")
    
    # 2. 检查依赖
    print()
    print("[2/2] 检查依赖...")
    
    # 检测已安装的 PyTorch 类型
    req_file, pytorch_type, detail = get_requirements_file_from_env()
    if req_file:
        print(f"  检测到 PyTorch: {pytorch_type} ({detail})")
        print(f"  依赖方案: {req_file}")
    else:
        print("  未检测到 PyTorch")
        req_file = None

    # 检查依赖是否满足
    deps_needs_update = False
    missing_packages = []
    if req_file and PYPROJECT_FILE.exists():
        # 导入依赖检查工具
        packaging_dir = PATH_ROOT / 'packaging'
        if str(packaging_dir) not in sys.path:
            sys.path.insert(0, str(packaging_dir))

        print("  正在检查依赖完整性...")
        try:
            from build_utils.package_checker import get_missing_packages
            missing_packages = get_missing_packages(get_variant_packages(req_file))
            if missing_packages:
                deps_needs_update = True
                print(f"  状态: [有缺失依赖，共 {len(missing_packages)} 个]")
                # 显示缺失的包（最多显示10个）
                if len(missing_packages) <= 10:
                    for pkg in missing_packages:
                        pkg_name = pkg.split('==')[0].split('>=')[0].split('<=')[0].split('[')[0].strip()
                        print(f"    - {pkg_name}")
                else:
                    for pkg in missing_packages[:10]:
                        pkg_name = pkg.split('==')[0].split('>=')[0].split('<=')[0].split('[')[0].strip()
                        print(f"    - {pkg_name}")
                    print(f"    ... 还有 {len(missing_packages) - 10} 个包")
            else:
                print("  状态: [依赖完整]")
        except ImportError:
            print("  状态: [无法检查，建议更新]")
            deps_needs_update = True
    else:
        print("  状态: [需要安装]")
        deps_needs_update = True
    
    # 检查完成提示
    print()
    print("=" * 40)
    print("检查完成")
    print("=" * 40)
    
    # 汇总结果
    print()
    print("检查结果汇总:")
    print("=" * 40)
    print(f"代码: {'需要更新' if code_needs_update else '已是最新'}")
    print(f"依赖: {'需要更新/安装' if deps_needs_update else '已满足'}")
    print("=" * 40)
    
    return code_needs_update, deps_needs_update, current_version, remote_version, req_file, missing_packages


def cleanup_caches():
    """清理 uv/pip 下载缓存，释放磁盘空间（自动执行，不询问）"""
    print()
    print('正在清理下载缓存...')
    os.environ.setdefault('UV_CACHE_DIR', str(PATH_ROOT / 'packaging' / 'uv_cache'))
    uv = find_uv()
    if uv:
        subprocess.run(f'{uv} cache clean', shell=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(f'"{python}" -m pip cache purge', shell=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('[OK] 缓存已清理')


def run_deps_with_retry(task, action_label_zh, action_label_en):
    """执行依赖安装任务，失败时询问是否重试（安装/更新共用）

    Args:
        task: 无参函数，返回 False 或抛异常表示失败
        action_label_zh/en: 动作名称，用于提示文案
    Returns:
        bool: 是否最终成功
    """
    while True:
        try:
            ok = task()
        except Exception as e:
            print()
            print("=" * 40)
            print(L(f"[错误] 依赖安装失败: {e}", f"[ERROR] Dependency installation failed: {e}"))
            print("=" * 40)
            ok = False
        if ok is not False:
            return True
        print(L("已安装成功的包会被保留，重试只会安装剩余的包",
                "Already-installed packages are kept; retry only installs the rest"))
        choice = input(L("是否重试? (y/n, 默认y): ", "Retry? (y/n, default y): ")).strip().lower()
        if choice not in ['', 'y', 'yes']:
            print(L(f"已取消，请检查网络后重新运行 [{action_label_zh}]",
                    f"Cancelled. Please check your network and run [{action_label_en}] again"))
            return False


def run_install(args):
    """安装：选择线路 → 同步代码 → 检测显卡并选择 CPU/GPU 版本 → 安装依赖"""
    print()
    print("=" * 40)
    print(L("安装", "Install"))
    print("=" * 40)
    print()

    # 选择线路（镜像源）
    current = get_remote_url().removesuffix('.git')
    default_idx = 1
    for i, (zh_name, en_name, url) in enumerate(GIT_MIRRORS, 1):
        if url.removesuffix('.git') == current:
            default_idx = i
            break
    print(L("请选择下载线路:", "Select download route:"))
    for i, (zh_name, en_name, url) in enumerate(GIT_MIRRORS, 1):
        mark = L('  (当前)', '  (current)') if i == default_idx else ''
        print(f"[{i}] {L(zh_name, en_name)}{mark}")
    print()
    choice = input(L(f"请选择 (1-{len(GIT_MIRRORS)}, 默认{default_idx}): ",
                     f"Select (1-{len(GIT_MIRRORS)}, default {default_idx}): ")).strip()
    if choice.isdigit() and 1 <= int(choice) <= len(GIT_MIRRORS):
        selected = GIT_MIRRORS[int(choice) - 1]
    else:
        selected = GIT_MIRRORS[default_idx - 1]
    if selected[2].removesuffix('.git') != current:
        subprocess.run([git, 'remote', 'set-url', 'origin', selected[2]], capture_output=True)
    print(L(f"✓ 使用线路: {selected[0]}", f"✓ Route: {selected[1]}"))

    # 同步代码到远程分支
    print()
    print(L("[1/2] 同步代码...", "[1/2] Syncing code..."))
    if not update_code_force(skip_confirm=True):
        print(L("[警告] 代码同步失败，将使用当前本地代码继续安装",
                "[WARNING] Code sync failed; continuing installation with current local code"))

    # 显卡检测 + CPU/GPU/AMD 版本选择 + 依赖安装（prepare_environment 内部完成交互）
    print()
    print(L("[2/2] 安装依赖...", "[2/2] Installing dependencies..."))
    args.requirements = 'auto'
    args.reinstall_torch = False
    args.update_deps = True
    args.frozen = False

    print()
    if run_deps_with_retry(lambda: (prepare_environment(args), True)[1], "安装", "Install"):
        cleanup_caches()
        print()
        print("=" * 40)
        print(L("[完成] 安装完成", "[DONE] Installation complete"))
        print("=" * 40)


def run_full_update(args):
    """更新：检查代码+依赖，需要时同步代码并安装依赖"""
    code_needs_update, deps_needs_update, current_ver, remote_ver, req_file, missing_packages = check_all_updates()

    print()
    if not code_needs_update and not deps_needs_update:
        print(L("[信息] 代码和依赖都已是最新，无需更新", "[INFO] Code and dependencies are up to date"))
        return

    print()
    confirm = input(L("是否继续更新? (y/n): ", "Continue update? (y/n): ")).strip().lower()
    if confirm not in ['y', 'yes']:
        print(L("取消更新", "Update cancelled"))
        return

    print()
    print("=" * 40)
    print(L("开始更新", "Starting update"))
    print("=" * 40)

    update_success = True

    if code_needs_update:
        print()
        print(L("[1/2] 更新代码...", "[1/2] Updating code..."))
        if not update_code_force(skip_confirm=True):
            update_success = False
            print(L("[错误] 代码更新失败，跳过依赖更新",
                    "[ERROR] Code update failed; skipping dependency update"))
        else:
            print(L("基于更新后的代码重新检查依赖...", "Re-checking dependencies against updated code..."))
            _, deps_needs_update, _, _, req_file, missing_packages = check_all_updates()
    else:
        print()
        print(L("[1/2] 代码已是最新，跳过", "[1/2] Code already up to date, skipping"))

    if update_success and deps_needs_update:
        print()
        print(L("[2/2] 更新依赖...", "[2/2] Updating dependencies..."))
        if req_file:
            args.requirements = req_file

        # 如果有缺失包列表，只安装缺失的包
        def do_update_deps():
            if missing_packages:
                print(L(f"只安装缺失的 {len(missing_packages)} 个包...",
                        f"Installing only the {len(missing_packages)} missing package(s)..."))
                return update_dependencies_selective(args, missing_packages)
            return update_dependencies(args)

        run_deps_with_retry(do_update_deps, "更新", "Update")
    elif update_success:
        print()
        print(L("[2/2] 依赖已满足，跳过", "[2/2] Dependencies satisfied, skipping"))

    if update_success:
        if deps_needs_update:
            cleanup_caches()
        print()
        print("=" * 40)
        print(L("[完成] 更新完成", "[DONE] Update complete"))
        print("=" * 40)


def maintenance_menu():
    """安装或更新 菜单"""
    init_language()
    print()
    print("=" * 40)
    print(L("漫画翻译器 - 安装或更新", "Manga Translator UI - Install / Update"))
    print("=" * 40)

    # 创建一个简单的 args 对象用于依赖更新
    class Args:
        def __init__(self):
            self.frozen = False
            self.requirements = 'auto'
            self.reinstall_torch = False
            self.update_deps = False

    args = Args()

    # 首次显示版本信息
    check_version_info()

    while True:
        branch, detached = get_current_branch()
        print()
        note = L(" (tag/游离状态)", " (tag/detached)") if detached else ""
        print(L(f"当前分支: {branch}{note}    镜像源: {get_mirror_display_name()}",
                f"Branch: {branch}{note}    Mirror: {get_mirror_display_name()}"))
        print()
        print(L("请选择操作:", "Select an action:"))
        print(L("[1] 安装 (检测显卡, 选择 CPU/GPU 版本并安装依赖)",
                "[1] Install (detect GPU, choose CPU/GPU build, install dependencies)"))
        print(L("[2] 更新 (代码+依赖)", "[2] Update (code + dependencies)"))
        print(L("[3] 切换分支 (main/beta)", "[3] Switch branch (main/beta)"))
        print(L("[4] 切换版本 (按 tag)", "[4] Switch version (by tag)"))
        print(L("[5] 切换镜像源", "[5] Switch mirror"))
        print(L("[6] 重新检查版本", "[6] Re-check version"))
        print(L("[7] 切换语言 (中文/English)", "[7] Language (中文/English)"))
        print(L("[8] 退出", "[8] Exit"))
        print()

        choice = input(L("请选择 (1-8): ", "Select (1-8): ")).strip()

        if choice == '1':
            run_install(args)
            input(L("\n按回车键继续...", "\nPress Enter to continue..."))

        elif choice == '2':
            run_full_update(args)
            input(L("\n按回车键继续...", "\nPress Enter to continue..."))

        elif choice == '3':
            switch_branch()
            input(L("\n按回车键继续...", "\nPress Enter to continue..."))

        elif choice == '4':
            switch_version_by_tag()
            input(L("\n按回车键继续...", "\nPress Enter to continue..."))

        elif choice == '5':
            switch_mirror()
            input(L("\n按回车键继续...", "\nPress Enter to continue..."))

        elif choice == '6':
            check_version_info()

        elif choice == '7':
            switch_language()

        elif choice == '8':
            print()
            print(L("退出", "Exit"))
            break

        else:
            print(L("无效选项", "Invalid option"))


def launch_ui(args):
    """启动UI界面 (Qt)"""
    from desktop_qt_ui.main import main as qt_main
    qt_main()


def launch_cli(args):
    """启动命令行版本"""
    import manga_translator.__main__ as cli_main
    # 传递参数给命令行版本
    cli_main.main()


def main():
    """主函数"""
    # 检查Python版本
    if not is_python_version_valid():
        sys.exit(1)

    # 解析命令行参数
    parser = argparse.ArgumentParser(description='漫画翻译器启动脚本')
    parser.add_argument("--update", action='store_true', help="启动前检查并自动更新")
    parser.add_argument("--frozen", action='store_true', help="跳过依赖检查(打包版本)")
    parser.add_argument("--install-deps-only", action='store_true', help="仅安装依赖,不启动UI")
    parser.add_argument("--reinstall-torch", action='store_true', help="重新安装PyTorch")
    parser.add_argument("--update-deps", action='store_true', help="更新依赖到最新版本(步骤4使用)")
    parser.add_argument("--requirements", default='auto', help="依赖方案 (auto=自动选择, 或指定 cpu/gpu/amd/metal)")
    parser.add_argument("--cli", action='store_true', help="使用命令行模式")
    parser.add_argument("--verbose", action='store_true', help="显示详细日志")
    parser.add_argument("--maintenance", action='store_true', help="启动更新维护菜单")
    
    args, unknown = parser.parse_known_args()
    
    # 如果是维护模式，直接进入维护菜单
    if args.maintenance:
        # 切换到项目根目录
        os.chdir(PATH_ROOT)
        maintenance_menu()
        return

    # 显示版本信息
    commit = commit_hash()
    print('=' * 60)
    print('漫画翻译器 Manga Translator UI')
    print('=' * 60)
    print(f'版本: {VERSION}')
    print(f'分支: {BRANCH}')
    print(f'提交: {commit[:8]}')
    print(f'Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')
    print(f'Python路径: {sys.executable}')
    print('=' * 60)

    # 切换到项目根目录 (launch.py 在 packaging/ 下,需要切换到父目录)
    APP_DIR = PATH_ROOT
    os.chdir(APP_DIR)

    # 更新检查
    if update_repository(args):
        return  # 更新后会自动重启

    # 准备环境
    print('\n正在检查依赖...')
    use_amd_pytorch, amd_gfx_version = prepare_environment(args)

    # 如果只是安装依赖,则退出
    if args.install_deps_only:
        print('\n依赖安装完成!')
        
        # 如果是 AMD GPU 且安装了 amd 方案，提示 PyTorch 状态
        if use_amd_pytorch and amd_gfx_version:
            print('\n✓ AMD ROCm PyTorch 已安装/更新')
            print(f'  gfx 版本: {amd_gfx_version}')
        
        return

    # 启动应用
    print('\n正在启动应用...\n')
    try:
        if args.cli:
            launch_cli(args)
        else:
            launch_ui(args)
    except KeyboardInterrupt:
        print('\n\n用户取消')
        sys.exit(0)
    except Exception as e:
        print(f'\n错误: {e}')
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
