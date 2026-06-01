"""
统一区域几何数据模型 — 纯数据类（无 Qt 依赖）

管理三类几何状态：
- 源区域（检测区域）：lines, center, angle  →  派生 polygons_local / source_box_local
- 自定义白框：用户手动拖动后需要持久化的框
- 渲染框：样式计算后得到、需要持久化的框

坐标系约定：
- 世界坐标（world / model）：图片像素坐标
- 局部坐标（local）：以 center 为原点、未旋转的坐标系
  world_to_local:  dx, dy = world - center;  lx = dx*cos + dy*sin;  ly = -dx*sin + dy*cos
  local_to_world:  wx = cx + lx*cos - ly*sin;  wy = cy + lx*sin + ly*cos
"""
import math
from typing import List, Optional, Tuple

import numpy as np


class RegionGeometryState:
    """统一管理源区域 / 自定义白框 / 渲染框几何状态的纯数据类。"""

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    def __init__(
        self,
        lines: list,
        center: List[float],
        angle: float,
        custom_white_frame_local: Optional[List[float]] = None,
        render_box_local: Optional[List[float]] = None,
        has_custom_white_frame: bool = False,
    ):
        self.lines = lines                   # List[List[[x, y]]]（世界坐标）
        self.center = list(center)           # [cx, cy]
        self.angle = float(angle)            # degrees

        # 派生：源区域各多边形在局部坐标系中的顶点
        self.polygons_local: List[List[List[float]]] = []
        self._source_box_local: Optional[List[float]] = None
        self._rebuild_polygons_local()

        # 自定义白框 / 渲染框（局部坐标 [left, top, right, bottom]）
        self._custom_white_frame_local: Optional[List[float]] = (
            list(custom_white_frame_local) if custom_white_frame_local is not None else None
        )
        self._render_box_local: Optional[List[float]] = (
            list(render_box_local) if render_box_local is not None else None
        )
        self.has_custom_white_frame: bool = bool(
            has_custom_white_frame and self._custom_white_frame_local is not None
        )

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def white_frame_local(self) -> Optional[List[float]]:
        # 解绑：用户手动白框优先于渲染框。
        # 渲染框（render_box）只是字号反算的小框，不应该夺权白框 UI。
        if self.has_custom_white_frame and self._custom_white_frame_local is not None:
            return self._custom_white_frame_local
        if self._render_box_local is not None:
            return self._render_box_local
        return self._source_box_local

    @property
    def custom_white_frame_local(self) -> Optional[List[float]]:
        return self._custom_white_frame_local

    @property
    def render_box_local(self) -> Optional[List[float]]:
        return self._render_box_local

    # ------------------------------------------------------------------
    # 坐标变换（纯计算，无状态修改）
    # ------------------------------------------------------------------

    def _angle_trig(self) -> Tuple[float, float]:
        rad = math.radians(self.angle)
        return math.cos(rad), math.sin(rad)

    def world_to_local(self, wx: float, wy: float) -> Tuple[float, float]:
        cos_a, sin_a = self._angle_trig()
        dx = wx - self.center[0]
        dy = wy - self.center[1]
        return (dx * cos_a + dy * sin_a,
                -dx * sin_a + dy * cos_a)

    def local_to_world(self, lx: float, ly: float) -> Tuple[float, float]:
        cos_a, sin_a = self._angle_trig()
        return (self.center[0] + lx * cos_a - ly * sin_a,
                self.center[1] + lx * sin_a + ly * cos_a)

    # ------------------------------------------------------------------
    # 工厂
    # ------------------------------------------------------------------

    @classmethod
    def from_region_data(
        cls,
        region_data: dict,
        prev_state: Optional["RegionGeometryState"] = None,
    ) -> "RegionGeometryState":
        lines = region_data.get("lines", [])
        center = region_data.get("center")
        angle = region_data.get("angle", 0)

        if center is None:
            all_verts = [v for poly in lines for v in poly]
            if all_verts:
                xs = [v[0] for v in all_verts]
                ys = [v[1] for v in all_verts]
                center = [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]
            else:
                center = [0, 0]

        # 尝试从 region_data 恢复自定义白框 / 渲染框
        custom_wf_local = region_data.get("white_frame_rect_local")
        render_box_local = region_data.get("render_box_rect_local")
        has_custom = region_data.get("has_custom_white_frame", False)
        has_custom_explicit = "has_custom_white_frame" in region_data
        custom_wf_explicit = "white_frame_rect_local" in region_data
        render_box_explicit = "render_box_rect_local" in region_data

        # 仅当本次数据没有显式给出自定义白框状态时，才继承上一次自定义白框
        if (
            prev_state is not None
            and prev_state.has_custom_white_frame
            and not has_custom_explicit
            and not custom_wf_explicit
        ):
            custom_wf_local = (
                list(prev_state._custom_white_frame_local)
                if prev_state._custom_white_frame_local is not None else None
            )
            has_custom = True

        if (
            prev_state is not None
            and prev_state._render_box_local is not None
            and not render_box_explicit
        ):
            render_box_local = list(prev_state._render_box_local)

        # 兼容历史数据：旧版本会把自动渲染框写进 white_frame_rect_local
        if not has_custom and render_box_local is None and _is_rect_like(custom_wf_local):
            render_box_local = list(custom_wf_local)
            if not custom_wf_explicit:
                custom_wf_local = None

        return cls(
            lines=lines,
            center=center,
            angle=angle,
            custom_white_frame_local=custom_wf_local if has_custom else None,
            render_box_local=render_box_local,
            has_custom_white_frame=has_custom,
        )

    # ------------------------------------------------------------------
    # 自定义白框 / 渲染框操作
    # ------------------------------------------------------------------

    def set_render_box(self, dst_points: Optional[np.ndarray]):
        """接收渲染框（世界坐标），同步到渲染框状态。

        dst_points 是世界坐标系中的轴对齐矩形 4 角点 (shape: [1,4,2] 或 [4,2])。
        我们将其转为局部坐标，用中心 + 邻边长度重建局部 AABB（避免旋转后的 min/max 误差）。
        """
        if dst_points is None:
            self._render_box_local = None
            return

        # 展平为 (4, 2)
        pts_world = dst_points.reshape(-1, 2) if len(dst_points.shape) == 3 else dst_points
        if pts_world is None or len(pts_world) < 4:
            self._render_box_local = None
            return

        # 世界 → 局部
        pts_local = np.array(
            [self.world_to_local(float(p[0]), float(p[1])) for p in pts_world[:4]],
            dtype=np.float64,
        )

        # 用中心 + 邻边宽高重建局部 AABB
        cpx = float(np.mean(pts_local[:, 0]))
        cpy = float(np.mean(pts_local[:, 1]))
        width = float(np.hypot(pts_local[1][0] - pts_local[0][0],
                               pts_local[1][1] - pts_local[0][1]))
        height = float(np.hypot(pts_local[3][0] - pts_local[0][0],
                                pts_local[3][1] - pts_local[0][1]))
        if width <= 0.0 or height <= 0.0:
            self._render_box_local = None
            return

        hw, hh = width / 2.0, height / 2.0
        self._render_box_local = [cpx - hw, cpy - hh, cpx + hw, cpy + hh]

    def set_custom_white_frame_local(self, rect_local: List[float]):
        """用户拖白框时调用 — 同步当前可见框与持久化白框。"""
        rect = list(rect_local)
        self._custom_white_frame_local = rect
        self._render_box_local = list(rect)
        self.has_custom_white_frame = True

    def to_region_data_patch(self) -> dict:
        """序列化自定义白框状态为可合并到 region_data 的补丁字典。"""
        patch = {
            "has_custom_white_frame": bool(
                self.has_custom_white_frame and self._custom_white_frame_local is not None
            ),
            "white_frame_rect_local": (
                list(self._custom_white_frame_local)
                if self.has_custom_white_frame and self._custom_white_frame_local is not None
                else None
            ),
        }
        return patch

    def to_render_box_patch(self) -> dict:
        patch = {
            "render_box_rect_local": (
                list(self._render_box_local) if self._render_box_local is not None else None
            )
        }
        return patch

    def to_persisted_state_patch(self) -> dict:
        patch = self.to_region_data_patch()
        patch.update(self.to_render_box_patch())
        return patch

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _rebuild_polygons_local(self):
        cx, cy = self.center
        self.polygons_local = [
            [[x - cx, y - cy] for x, y in line]
            for line in self.lines
        ]
        self._auto_update_white_frame()

    def _auto_update_white_frame(self):
        all_pts = [p for poly in self.polygons_local for p in poly]
        if not all_pts:
            self._source_box_local = None
            return
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        self._source_box_local = [min(xs), min(ys), max(xs), max(ys)]


def _is_rect_like(value) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 4
