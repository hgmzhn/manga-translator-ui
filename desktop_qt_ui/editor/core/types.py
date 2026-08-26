"""Editor-owned mask type identifiers."""

from enum import Enum


class MaskType(str, Enum):
    """蒙版类型"""
    RAW = "raw"              # 原始蒙版
    REFINED = "refined"      # 优化后的蒙版





