#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检测虚拟环境中安装的 PyTorch 版本类型（CPU/GPU）
"""

import sys

def detect_torch_type():
    """检测当前环境的 PyTorch 类型"""
    try:
        import torch
        
        # 检查是否是 AMD ROCm 版本
        if hasattr(torch.version, 'hip') and torch.version.hip:
            # AMD ROCm PyTorch
            return "AMD", f"rocm{torch.version.hip}" if torch.version.hip else "rocm"
        
        # 检查是否支持 CUDA (NVIDIA)
        elif torch.cuda.is_available():
            cuda_version = torch.version.cuda
            return "GPU", f"cu{cuda_version.replace('.', '')}" if cuda_version else "unknown"
        
        # 检查是否支持 MPS (Apple Silicon Metal)
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            return "Metal", "mps"
        
        else:
            # CPU 版本
            torch_version = torch.__version__
            if '+cpu' in torch_version or 'cpu' in torch_version:
                return "CPU", "cpu"
            else:
                # 可能是 CPU 版本但没有明确标识
                return "CPU", "cpu"
    except ImportError:
        # PyTorch 未安装
        return None, None

def get_dependency_group():
    """获取对应的 pyproject dependency group"""
    torch_type, variant = detect_torch_type()
    
    if torch_type == "GPU":
        return "cuda12.6" if (variant or "").startswith("cu12") else "cuda13.0"
    elif torch_type == "AMD":
        return "rocm7.2.1"
    elif torch_type == "Metal":
        return "metal"
    elif torch_type == "CPU":
        return "cpu"
    else:
        # PyTorch 未安装，无法确定
        return None


def get_requirements_file():
    """兼容旧调用；现在返回 dependency group 名称。"""
    return get_dependency_group()

if __name__ == "__main__":
    torch_type, variant = detect_torch_type()
    
    if torch_type:
        print(f"检测到 PyTorch 类型: {torch_type}")
        if variant:
            print(f"变体: {variant}")
        
        group = get_dependency_group()
        print(f"对应的依赖组: {group}")
        
        # --file-only 保留为旧调用兼容别名
        if len(sys.argv) > 1 and sys.argv[1] in ("--group-only", "--file-only"):
            print(group, end="")
    else:
        print("未检测到 PyTorch，无法确定版本类型")
        print("将在安装依赖时重新选择")
        sys.exit(1)
