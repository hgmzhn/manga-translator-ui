# -*- coding: utf-8 -*-
"""
依赖包检查工具
Package checking utilities
"""

import functools
import itertools
import pathlib
import subprocess
import sys
from typing import List, Optional

try:
    # packaging < 22.0
    from packaging.requirements import Requirement
except ImportError:
    try:
        # packaging >= 22.0
        from packaging.requirements import Requirement
    except (ImportError, ModuleNotFoundError):
        # Fallback: parse requirements manually
        import re
        class Requirement:
            def __init__(self, requirement_string):
                self.requirement_string = requirement_string
                # Simple regex to extract package name
                match = re.match(r'^([a-zA-Z0-9\-_\.]+)', requirement_string.strip())
                self.name = match.group(1) if match else requirement_string

from packaging.utils import canonicalize_name

try:
    import importlib.metadata as importlib_metadata
except (ModuleNotFoundError, ImportError):
    import importlib_metadata
from packaging.version import Version


def package_version(name: str) -> Optional[Version]:
    """获取已安装包的版本"""
    try:
        return Version(importlib_metadata.distribution(canonicalize_name(name)).version)
    except importlib_metadata.PackageNotFoundError:
        return None


def _nonblank(text):
    """过滤空行和注释行"""
    return text and not text.startswith('#')


def _is_requirement(line):
    """判断是否是依赖包行（过滤 pip 选项）"""
    line = line.strip()
    # 过滤空行和注释
    if not line or line.startswith('#'):
        return False
    # 过滤 pip 选项 (--xxx 或 -x)
    if line.startswith('-'):
        return False
    # 过滤 URL 形式的依赖（以 http:// 或 https:// 开头的需要保留）
    # 这些是 wheel 文件的直接链接
    return True


@functools.singledispatch
def yield_lines(iterable):
    """提取有效行"""
    return itertools.chain.from_iterable(map(yield_lines, iterable))


@yield_lines.register(str)
def _(text):
    return filter(_nonblank, map(str.strip, text.splitlines()))


def drop_comment(line):
    """去除注释"""
    return line.partition(' #')[0]


def join_continuation(lines):
    """合并续行"""
    lines = iter(lines)
    for item in lines:
        while item.endswith('\\'):
            try:
                item = item[:-2].strip() + next(lines)
            except StopIteration:
                return
        yield item


def load_req_file(requirements_file: str) -> List[str]:
    """加载requirements文件"""
    with pathlib.Path(requirements_file).open(encoding='utf-8') as reqfile:
        lines = join_continuation(map(drop_comment, yield_lines(reqfile)))
        # 过滤掉 pip 选项（如 --extra-index-url）
        valid_reqs = [line for line in lines if _is_requirement(line)]
        return list(map(lambda x: str(Requirement(x)), valid_reqs))


def _yield_reqs_to_install(req: Requirement, current_extra: str = ''):
    """检查需要安装的依赖"""
    if req.marker and not req.marker.evaluate({'extra': current_extra}):
        return

    try:
        version_str = importlib_metadata.distribution(req.name).version
    except importlib_metadata.PackageNotFoundError:
        yield req
    else:
        if not version_str:
            # 存在损坏或不完整的 dist-info，但没有可比较的版本；重新安装该依赖。
            yield req
            return

        # 对于 PyTorch 等包，移除本地版本标识符（如 +cu128）进行比较
        # 例如：2.9.1+cu128 -> 2.9.1
        version_base = version_str.split('+')[0]

        # 先用基础版本号检查
        if req.specifier.contains(version_base, prereleases=True):
            # 版本匹配，检查子依赖
            for child_req in (importlib_metadata.metadata(req.name).get_all('Requires-Dist') or []):
                child_req_obj = Requirement(child_req)
                need_check, ext = False, None
                for extra in req.extras:
                    if child_req_obj.marker and child_req_obj.marker.evaluate({'extra': extra}):
                        need_check = True
                        ext = extra
                        break
                if need_check:
                    yield from _yield_reqs_to_install(child_req_obj, ext)
        else:
            # 版本不匹配，但如果已安装的版本更新，也认为满足
            # 例如：要求 ==2.8.0，已安装 2.9.1，认为满足（向后兼容）
            try:
                installed_version = Version(version_base)
                # 检查 specifier 中是否有精确版本要求（==）
                has_exact_match = any(spec.operator == '==' for spec in req.specifier)

                if has_exact_match:
                    # 有精确版本要求，检查已安装版本是否更新
                    # 提取要求的版本号
                    for spec in req.specifier:
                        if spec.operator == '==':
                            required_version = Version(spec.version)
                            if installed_version >= required_version:
                                # 已安装版本更新或相等，认为满足
                                return
                            break

                # 其他情况，版本不匹配，需要安装
                yield req
            except Exception:
                # 版本解析失败，按原逻辑处理
                yield req


def _check_req(req: Requirement):
    """检查单个依赖是否满足"""
    return not bool(list(itertools.islice(_yield_reqs_to_install(req), 1)))


def get_missing_packages(reqs: List[str]) -> List[str]:
    """获取缺失或需要更新的包列表"""
    missing = []
    for req_str in reqs:
        req = Requirement(req_str)
        if not _check_req(req):
            missing.append(req_str)
    return missing


def check_reqs(reqs: List[str]) -> bool:
    """检查所有依赖是否满足"""
    return all(map(lambda x: _check_req(Requirement(x)), reqs))


def check_req_file(requirements_file: str) -> bool:
    """检查requirements文件中的依赖是否满足"""
    try:
        return check_reqs(load_req_file(requirements_file))
    except Exception as e:
        print(f'检查依赖文件失败: {e}')
        return False


def get_missing_packages_from_file(requirements_file: str) -> List[str]:
    """获取requirements文件中缺失或需要更新的包列表"""
    try:
        reqs = load_req_file(requirements_file)
        return get_missing_packages(reqs)
    except Exception as e:
        print(f'检查依赖文件失败: {e}')
        return []

