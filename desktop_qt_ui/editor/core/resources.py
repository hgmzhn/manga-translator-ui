"""资源数据结构

定义编辑器中使用的所有资源类。
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict

import numpy as np
from PIL import Image

from .types import MaskType


@dataclass
class ImageResource:
    """图片资源

    image 以 eager 方式打开（见 ResourceManager），不持有文件句柄，因此这里
    只丢引用、不 close：同一个 PIL 对象可能同时被 session、导出快照等多方持有，
    由引用计数决定何时回收，谁都不需要判断"还有没有别人在用"。
    """
    path: str
    image: Image.Image  # PIL Image
    width: int
    height: int
    load_time: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    qimage: Any = None  # QImage,后台线程预转避免主线程阻塞;Any 类型避免 core 层引入 Qt 依赖

    def touch(self) -> None:
        """记录一次访问，供 LRU 淘汰排序使用。"""
        self.last_access = time.time()

    def release(self) -> None:
        """丢弃本资源持有的引用（不关闭图像，可能仍被他方持有）。"""
        self.image = None
        self.qimage = None


@dataclass
class MaskResource:
    """蒙版资源"""
    mask_type: MaskType
    data: np.ndarray
    width: int
    height: int
    create_time: float = field(default_factory=time.time)
    
    def release(self) -> None:
        """释放资源"""
        if self.data is not None:
            self.data = None
    
    def __del__(self):
        """析构函数，确保资源释放"""
        self.release()


@dataclass
class RegionResource:
    """文本区域资源"""
    region_id: int
    data: Dict  # 区域数据（包含坐标、文本、样式等）
    create_time: float = field(default_factory=time.time)
    update_time: float = field(default_factory=time.time)

