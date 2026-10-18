"""
统一区域几何数据模型 — 纯数据类（无 Qt 依赖）

管理三类几何状态：
- 源区域（检测区域）：lines, center, angle  →  派生 polygons_local / source_box_local
- 自定义白框：用户手动拖动后需要持久化的框（矩形或透视四边形）
- 渲染框：样式计算后得到、需要持久化的框

坐标系约定：
- 世界坐标（world / model）：图片像素坐标
- 局部坐标（local）：以 center 为原点、未旋转的坐标系
  world_to_local:  dx, dy = world - center;  lx = dx*cos + dy*sin;  ly = -dx*sin + dy*cos
  local_to_world:  wx = cx + lx*cos - ly*sin;  wy = cy + lx*sin + ly*cos

透视支持：
- 白框/渲染框可以是 4 点四边形（quad），点顺序固定为
  [左上, 右上, 右下, 左下]（与矩形 [left, top, right, bottom] 的角点一致）。
- 有 quad 时，quad 为权威数据；white_frame_rect_local / render_box_rect_local
  继续保存其轴对齐包围盒（bbox），供旧代码兼容读取。
"""

import math
from typing import List, Optional, Tuple

import numpy as np


def is_rect_like(value) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 4


def is_quad_like(value) -> bool:
    """判断是否为 4 个 [x, y] 点组成的四边形。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return False
        try:
            float(point[0])
            float(point[1])
        except (TypeError, ValueError):
            return False
    return True


def _as_quad(value) -> Optional[List[List[float]]]:
    if not is_quad_like(value):
        return None
    return [[float(p[0]), float(p[1])] for p in value[:4]]


def quad_bbox(quad) -> List[float]:
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return [min(xs), min(ys), max(xs), max(ys)]


def _is_parallelogram(points, tol: float = 1.5) -> bool:
    """4 点是否构成平行四边形（含矩形/旋转矩形，不含透视梯形）。"""
    if len(points) < 4:
        return False
    pts = [(float(p[0]), float(p[1])) for p in points[:4]]
    p0, p1, p2, p3 = pts
    e0 = (p1[0] - p0[0], p1[1] - p0[1])
    e1 = (p2[0] - p1[0], p2[1] - p1[1])
    e2 = (p3[0] - p2[0], p3[1] - p2[1])
    e3 = (p0[0] - p3[0], p0[1] - p3[1])
    return (
        abs(e2[0] + e0[0]) <= tol
        and abs(e2[1] + e0[1]) <= tol
        and abs(e3[0] + e1[0]) <= tol
        and abs(e3[1] + e1[1]) <= tol
    )


def normalize_region_geometry_data(region_data: dict) -> dict:
    """把历史几何字段规范为当前语义。

    旧版本会把自动渲染框保存在 white_frame_rect_local；当前版本中该字段只表示
    用户自定义白框，自动框使用 render_box_rect_local。
    透视字段 white_frame_quad_local / render_box_quad_local 为 4 点四边形，
    顺序 [左上, 右上, 右下, 左下]；存在时同步其 bbox 供旧代码读取。
    """
    if not isinstance(region_data, dict):
        return region_data

    normalized = dict(region_data)

    custom_quad = _as_quad(normalized.get("white_frame_quad_local"))
    if custom_quad is not None:
        normalized["white_frame_quad_local"] = custom_quad
        if "has_custom_white_frame" not in normalized:
            normalized["has_custom_white_frame"] = True
        if not is_rect_like(normalized.get("white_frame_rect_local")):
            normalized["white_frame_rect_local"] = quad_bbox(custom_quad)

    render_quad = _as_quad(normalized.get("render_box_quad_local"))
    if render_quad is not None:
        normalized["render_box_quad_local"] = render_quad
        if not is_rect_like(normalized.get("render_box_rect_local")):
            normalized["render_box_rect_local"] = quad_bbox(render_quad)

    custom_wf_local = normalized.get("white_frame_rect_local")
    render_box_local = normalized.get("render_box_rect_local")
    has_custom = bool(normalized.get("has_custom_white_frame", False))
    if (
        custom_quad is None
        and not has_custom
        and is_rect_like(custom_wf_local)
        and not is_rect_like(render_box_local)
    ):
        normalized["render_box_rect_local"] = list(custom_wf_local)
        normalized.pop("white_frame_rect_local", None)
    return normalized


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
        custom_white_frame_quad_local: Optional[List[List[float]]] = None,
        render_box_quad_local: Optional[List[List[float]]] = None,
    ):
        self.lines = lines  # List[List[[x, y]]]（世界坐标）
        self.center = list(center)  # [cx, cy]
        self.angle = float(angle)  # degrees

        # 派生：源区域各多边形在局部坐标系中的顶点
        self.polygons_local: List[List[List[float]]] = []
        self._source_box_local: Optional[List[float]] = None
        self._rebuild_polygons_local()

        # 自定义白框 / 渲染框（局部坐标）
        self._custom_white_frame_quad_local = _as_quad(
            custom_white_frame_quad_local
        )
        self._render_box_quad_local = _as_quad(render_box_quad_local)

        self._custom_white_frame_local: Optional[List[float]] = (
            list(custom_white_frame_local)
            if custom_white_frame_local is not None
            else (
                quad_bbox(self._custom_white_frame_quad_local)
                if self._custom_white_frame_quad_local is not None
                else None
            )
        )
        self._render_box_local: Optional[List[float]] = (
            list(render_box_local)
            if render_box_local is not None
            else (
                quad_bbox(self._render_box_quad_local)
                if self._render_box_quad_local is not None
                else None
            )
        )
        self.has_custom_white_frame: bool = bool(
            (has_custom_white_frame or self._custom_white_frame_quad_local is not None)
            and (
                self._custom_white_frame_local is not None
                or self._custom_white_frame_quad_local is not None
            )
        )

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def white_frame_local(self) -> Optional[List[float]]:
        # 解绑：用户手动白框优先于渲染框。
        # 渲染框（render_box）只是字号反算的小框，不应该夺权白框 UI。
        if self.has_custom_white_frame:
            if self._custom_white_frame_quad_local is not None:
                return quad_bbox(self._custom_white_frame_quad_local)
            if self._custom_white_frame_local is not None:
                return self._custom_white_frame_local
        if self._render_box_quad_local is not None:
            return quad_bbox(self._render_box_quad_local)
        if self._render_box_local is not None:
            return self._render_box_local
        return self._source_box_local

    @property
    def white_frame_quad_local(self) -> Optional[List[List[float]]]:
        """当前生效的透视四边形（局部坐标）；没有透视时为 None。"""
        if (
            self.has_custom_white_frame
            and self._custom_white_frame_quad_local is not None
        ):
            return self._custom_white_frame_quad_local
        if self._render_box_quad_local is not None:
            return self._render_box_quad_local
        return None

    @property
    def custom_white_frame_local(self) -> Optional[List[float]]:
        return self._custom_white_frame_local

    @property
    def custom_white_frame_quad_local(self) -> Optional[List[List[float]]]:
        return self._custom_white_frame_quad_local

    @property
    def render_box_local(self) -> Optional[List[float]]:
        return self._render_box_local

    @property
    def render_box_quad_local(self) -> Optional[List[List[float]]]:
        return self._render_box_quad_local

    def white_frame_polygon_local(self) -> Optional[List[List[float]]]:
        """白框的 4 个局部角点（矩形或透视四边形）。"""
        quad = self.white_frame_quad_local
        if quad is not None:
            return [list(p) for p in quad]
        rect = self.white_frame_local
        if rect is None:
            return None
        left, top, right, bottom = rect
        return [
            [left, top],
            [right, top],
            [right, bottom],
            [left, bottom],
        ]

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
        return (dx * cos_a + dy * sin_a, -dx * sin_a + dy * cos_a)

    def local_to_world(self, lx: float, ly: float) -> Tuple[float, float]:
        cos_a, sin_a = self._angle_trig()
        return (
            self.center[0] + lx * cos_a - ly * sin_a,
            self.center[1] + lx * sin_a + ly * cos_a,
        )

    # ------------------------------------------------------------------
    # 工厂
    # ------------------------------------------------------------------

    @classmethod
    def from_region_data(
        cls,
        region_data: dict,
        prev_state: Optional["RegionGeometryState"] = None,
    ) -> "RegionGeometryState":
        region_data = normalize_region_geometry_data(region_data)
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

        # 从 region_data 恢复自定义白框 / 渲染框
        custom_wf_local = region_data.get("white_frame_rect_local")
        render_box_local = region_data.get("render_box_rect_local")
        custom_quad = _as_quad(region_data.get("white_frame_quad_local"))
        render_quad = _as_quad(region_data.get("render_box_quad_local"))
        has_custom = region_data.get("has_custom_white_frame", False)
        has_custom_explicit = "has_custom_white_frame" in region_data
        custom_wf_explicit = "white_frame_rect_local" in region_data
        render_box_explicit = "render_box_rect_local" in region_data
        custom_quad_explicit = "white_frame_quad_local" in region_data
        render_quad_explicit = "render_box_quad_local" in region_data

        # 仅当本次数据没有显式给出自定义白框状态时，才继承上一次自定义白框
        if (
            prev_state is not None
            and prev_state.has_custom_white_frame
            and not has_custom_explicit
            and not custom_wf_explicit
            and not custom_quad_explicit
        ):
            custom_wf_local = (
                list(prev_state._custom_white_frame_local)
                if prev_state._custom_white_frame_local is not None
                else None
            )
            custom_quad = (
                [list(p) for p in prev_state._custom_white_frame_quad_local]
                if prev_state._custom_white_frame_quad_local is not None
                else None
            )
            has_custom = True

        if (
            prev_state is not None
            and prev_state._render_box_local is not None
            and not render_box_explicit
            and not render_quad_explicit
        ):
            render_box_local = list(prev_state._render_box_local)

        if (
            prev_state is not None
            and prev_state._render_box_quad_local is not None
            and not render_box_explicit
            and not render_quad_explicit
        ):
            render_quad = [
                list(p) for p in prev_state._render_box_quad_local
            ]

        if custom_quad is not None:
            has_custom = True

        return cls(
            lines=lines,
            center=center,
            angle=angle,
            custom_white_frame_local=custom_wf_local if has_custom else None,
            render_box_local=render_box_local,
            has_custom_white_frame=has_custom,
            custom_white_frame_quad_local=custom_quad if has_custom else None,
            render_box_quad_local=render_quad,
        )

    # ------------------------------------------------------------------
    # 自定义白框 / 渲染框操作
    # ------------------------------------------------------------------

    def set_render_box(self, dst_points: Optional[np.ndarray]):
        """接收渲染框（世界坐标），同步到渲染框状态。

        dst_points 是世界坐标系中的 4 角点 (shape: [1,4,2] 或 [4,2])。
        轴对齐矩形（局部坐标下）仍按旧的中心 + 邻边长度重建；其他形状
        作为透视四边形保存，并同步其 bbox。
        """
        if dst_points is None:
            self._render_box_local = None
            self._render_box_quad_local = None
            return

        # 展平为 (4, 2)
        pts_world = (
            dst_points.reshape(-1, 2) if len(dst_points.shape) == 3 else dst_points
        )
        if pts_world is None or len(pts_world) < 4:
            self._render_box_local = None
            self._render_box_quad_local = None
            return

        pts_world_list = [(float(p[0]), float(p[1])) for p in pts_world[:4]]
        # 世界 → 局部
        pts_local = np.array(
            [self.world_to_local(float(p[0]), float(p[1])) for p in pts_world_list],
            dtype=np.float64,
        )

        if _is_parallelogram(pts_world_list):
            # 矩形/旋转矩形/平行四边形：沿用旧行为，用中心 + 邻边长度
            # 重建局部 AABB（局部 AABB 与 item 的旋转由父级 item 承担）。
            cpx = float(np.mean(pts_local[:, 0]))
            cpy = float(np.mean(pts_local[:, 1]))
            width = float(
                np.hypot(
                    pts_local[1][0] - pts_local[0][0],
                    pts_local[1][1] - pts_local[0][1],
                )
            )
            height = float(
                np.hypot(
                    pts_local[3][0] - pts_local[0][0],
                    pts_local[3][1] - pts_local[0][1],
                )
            )
            if width <= 0.0 or height <= 0.0:
                self._render_box_local = None
                self._render_box_quad_local = None
                return
            hw, hh = width / 2.0, height / 2.0
            self._render_box_local = [cpx - hw, cpy - hh, cpx + hw, cpy + hh]
            self._render_box_quad_local = None
            return

        # 透视四边形（对边不平行）：保留局部 4 点
        self._render_box_quad_local = [
            [float(p[0]), float(p[1])] for p in pts_local
        ]
        self._render_box_local = quad_bbox(self._render_box_quad_local)

    def set_custom_white_frame_local(self, rect_local: List[float]):
        """用户拖白框（矩形模式）时调用 — 同步当前可见框与持久化白框。"""
        rect = list(rect_local)
        self._custom_white_frame_local = rect
        self._custom_white_frame_quad_local = None
        self._render_box_local = list(rect)
        self._render_box_quad_local = None
        self.has_custom_white_frame = True

    def set_custom_white_frame_quad(self, quad_local: List[List[float]]):
        """用户拖白框（透视模式）时调用 — 保存 4 点四边形。"""
        quad = _as_quad(quad_local)
        if quad is None:
            return
        self._custom_white_frame_quad_local = quad
        self._custom_white_frame_local = quad_bbox(quad)
        self._render_box_quad_local = [list(p) for p in quad]
        self._render_box_local = list(self._custom_white_frame_local)
        self.has_custom_white_frame = True

    def clear_custom_white_frame_quad(self):
        """退出透视模式：保留 bbox 作为矩形白框。"""
        if self._custom_white_frame_quad_local is None:
            return
        self._custom_white_frame_local = quad_bbox(
            self._custom_white_frame_quad_local
        )
        self._custom_white_frame_quad_local = None
        self._render_box_local = list(self._custom_white_frame_local)
        self._render_box_quad_local = None
        self.has_custom_white_frame = True

    def to_region_data_patch(self) -> dict:
        """序列化自定义白框状态为可合并到 region_data 的补丁字典。"""
        has_custom = bool(
            self.has_custom_white_frame
            and (
                self._custom_white_frame_local is not None
                or self._custom_white_frame_quad_local is not None
            )
        )
        patch = {
            "has_custom_white_frame": has_custom,
            "white_frame_rect_local": (
                list(self._custom_white_frame_local)
                if has_custom and self._custom_white_frame_local is not None
                else None
            ),
            "white_frame_quad_local": (
                [list(p) for p in self._custom_white_frame_quad_local]
                if has_custom and self._custom_white_frame_quad_local is not None
                else None
            ),
        }
        return patch

    def to_render_box_patch(self) -> dict:
        patch = {
            "render_box_rect_local": (
                list(self._render_box_local)
                if self._render_box_local is not None
                else None
            ),
            "render_box_quad_local": (
                [list(p) for p in self._render_box_quad_local]
                if self._render_box_quad_local is not None
                else None
            ),
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
            [[x - cx, y - cy] for x, y in line] for line in self.lines
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
