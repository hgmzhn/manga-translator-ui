"""
稳定几何引擎 - 消除旋转编辑时的跳变问题
核心思路：以用户看到的视觉位置为绝对真理，通过补偿算法确保后端渲染完全一致
"""
import math
import copy
from typing import List, Tuple, Dict, Any, Optional
import numpy as np
from dataclasses import dataclass


@dataclass
class VisualAnchor:
    """视觉锚点 - 用户在屏幕上看到的固定位置"""
    x: float
    y: float
    
    def as_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)
    
    def __eq__(self, other) -> bool:
        if not isinstance(other, VisualAnchor):
            return False
        epsilon = 1e-9
        return abs(self.x - other.x) < epsilon and abs(self.y - other.y) < epsilon


@dataclass
class GeometryState:
    """几何状态 - 包含原始几何、旋转角度和视觉锚点"""
    raw_polygons: List[List[Tuple[float, float]]]  # 原始多边形坐标（未旋转）
    rotation_degrees: float  # 旋转角度
    visual_anchor: VisualAnchor  # 视觉锚点
    
    def get_backend_center(self) -> Tuple[float, float]:
        """模拟后端的中心点计算算法（包围盒中心）"""
        if not self.raw_polygons:
            return (0.0, 0.0)
            
        all_points = [p for poly in self.raw_polygons for p in poly]
        if not all_points:
            return (0.0, 0.0)
            
        xs, ys = zip(*all_points)
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


class StableGeometryEngine:
    """
    稳定几何引擎
    确保在任何旋转角度下编辑文本框都不会产生视觉跳变
    """
    
    @staticmethod
    def rotate_point(x: float, y: float, angle_degrees: float, 
                    center_x: float, center_y: float) -> Tuple[float, float]:
        """围绕指定中心点旋转一个点"""
        if angle_degrees == 0:
            return (x, y)
            
        angle_rad = math.radians(angle_degrees)
        cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
        
        # 平移到原点
        dx = x - center_x
        dy = y - center_y
        
        # 旋转
        rotated_x = dx * cos_a - dy * sin_a
        rotated_y = dx * sin_a + dy * cos_a
        
        # 平移回去
        return (rotated_x + center_x, rotated_y + center_y)
    
    @staticmethod
    def calculate_compensated_geometry(geometry_state: GeometryState) -> List[List[Tuple[float, float]]]:
        """
        核心补偿算法：
        计算"补偿后的几何坐标"，使得后端按自己的逻辑计算的中心点
        恰好等于用户期望的视觉锚点位置
        """
        # 1. 计算原始几何的中心点（模拟后端算法）
        backend_center = geometry_state.get_backend_center()
        
        # 2. 计算从后端中心到视觉锚点的偏移量
        offset_x = geometry_state.visual_anchor.x - backend_center[0]
        offset_y = geometry_state.visual_anchor.y - backend_center[1]
        
        # 3. 将所有原始坐标平移这个偏移量，得到"补偿后的几何"
        compensated_polygons = []
        for polygon in geometry_state.raw_polygons:
            compensated_polygon = [
                (x + offset_x, y + offset_y) 
                for x, y in polygon
            ]
            compensated_polygons.append(compensated_polygon)
        
        return compensated_polygons
    
    @staticmethod
    def get_visual_coordinates_for_display(geometry_state: GeometryState) -> List[List[Tuple[float, float]]]:
        """
        获取用于前端显示的坐标
        这些坐标是旋转后的，直接用于前端绘制
        """
        # 1. 获取补偿后的几何
        compensated_polygons = StableGeometryEngine.calculate_compensated_geometry(geometry_state)
        
        # 2. 如果有旋转，围绕视觉锚点进行旋转
        if geometry_state.rotation_degrees == 0:
            return compensated_polygons
            
        rotated_polygons = []
        for polygon in compensated_polygons:
            rotated_polygon = [
                StableGeometryEngine.rotate_point(
                    x, y, 
                    geometry_state.rotation_degrees,
                    geometry_state.visual_anchor.x, 
                    geometry_state.visual_anchor.y
                )
                for x, y in polygon
            ]
            rotated_polygons.append(rotated_polygon)
        
        return rotated_polygons
    
    @staticmethod
    def get_backend_data_for_rendering(geometry_state: GeometryState) -> Dict[str, Any]:
        """
        获取发送给后端的数据
        这个数据确保后端渲染出来的结果与前端显示完全一致
        """
        # 1. 获取补偿后的几何（未旋转）
        compensated_polygons = StableGeometryEngine.calculate_compensated_geometry(geometry_state)
        
        # 2. 将补偿后的几何转换为后端期望的格式
        lines_data = [
            [[float(x), float(y)] for x, y in polygon] 
            for polygon in compensated_polygons
        ]
        
        # 3. 计算后端将会得到的中心点（验证补偿是否正确）
        all_points = [p for poly in compensated_polygons for p in poly]
        if all_points:
            xs, ys = zip(*all_points)
            calculated_center = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
        else:
            calculated_center = (0.0, 0.0)
        
        return {
            'lines': lines_data,
            'angle': geometry_state.rotation_degrees,
            'center': list(calculated_center)  # 这应该等于visual_anchor
        }
    
    @staticmethod
    def add_polygon_to_geometry(geometry_state: GeometryState, 
                               new_polygon_world: List[Tuple[float, float]]) -> GeometryState:
        """
        向几何状态添加新多边形，确保视觉锚点不变
        这是解决编辑时跳变问题的关键函数
        """
        # 1. 将世界坐标的新多边形转换为原始坐标（未旋转的模型坐标）
        if geometry_state.rotation_degrees != 0:
            # 反向旋转，得到原始坐标
            new_polygon_raw = [
                StableGeometryEngine.rotate_point(
                    x, y, 
                    -geometry_state.rotation_degrees,  # 注意是负角度
                    geometry_state.visual_anchor.x,
                    geometry_state.visual_anchor.y
                )
                for x, y in new_polygon_world
            ]
        else:
            new_polygon_raw = list(new_polygon_world)
        
        # 2. 创建新的几何状态
        new_raw_polygons = geometry_state.raw_polygons + [new_polygon_raw]
        
        # 3. 保持相同的视觉锚点和旋转角度
        new_geometry_state = GeometryState(
            raw_polygons=new_raw_polygons,
            rotation_degrees=geometry_state.rotation_degrees,
            visual_anchor=geometry_state.visual_anchor  # 关键：视觉锚点不变！
        )
        
        return new_geometry_state
    
    @staticmethod
    def update_visual_anchor(geometry_state: GeometryState, 
                           new_anchor: VisualAnchor) -> GeometryState:
        """
        更新视觉锚点位置（比如拖拽移动时）
        """
        return GeometryState(
            raw_polygons=geometry_state.raw_polygons,
            rotation_degrees=geometry_state.rotation_degrees,
            visual_anchor=new_anchor
        )
    
    @staticmethod
    def update_rotation(geometry_state: GeometryState, 
                       new_rotation_degrees: float) -> GeometryState:
        """
        更新旋转角度，保持视觉锚点不变
        """
        return GeometryState(
            raw_polygons=geometry_state.raw_polygons,
            rotation_degrees=new_rotation_degrees,
            visual_anchor=geometry_state.visual_anchor
        )
    
    @staticmethod
    def verify_consistency(geometry_state: GeometryState) -> bool:
        """
        验证一致性：后端计算的中心是否等于视觉锚点
        用于调试和测试
        """
        backend_data = StableGeometryEngine.get_backend_data_for_rendering(geometry_state)
        calculated_center = backend_data['center']
        
        epsilon = 1e-9
        return (abs(calculated_center[0] - geometry_state.visual_anchor.x) < epsilon and
                abs(calculated_center[1] - geometry_state.visual_anchor.y) < epsilon)


class RegionGeometryManager:
    """
    区域几何管理器
    负责将现有的regions_data转换为稳定几何状态
    """
    
    @staticmethod
    def from_region_data(region_data: Dict[str, Any]) -> GeometryState:
        """从现有的region_data创建GeometryState"""
        lines = region_data.get('lines', [])
        angle = region_data.get('angle', 0)
        center = region_data.get('center')
        
        # 如果没有center，计算一个
        if not center:
            if lines:
                all_points = [p for poly in lines for p in poly]
                if all_points:
                    xs, ys = zip(*all_points)
                    center = [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]
                else:
                    center = [0.0, 0.0]
            else:
                center = [0.0, 0.0]
        
        # 将lines转换为原始多边形格式
        raw_polygons = [[(p[0], p[1]) for p in poly] for poly in lines]
        
        return GeometryState(
            raw_polygons=raw_polygons,
            rotation_degrees=angle,
            visual_anchor=VisualAnchor(center[0], center[1])
        )
    
    @staticmethod
    def to_region_data(geometry_state: GeometryState, 
                      original_region_data: Dict[str, Any]) -> Dict[str, Any]:
        """将GeometryState转换回region_data格式"""
        backend_data = StableGeometryEngine.get_backend_data_for_rendering(geometry_state)
        
        # 保留原始数据的其他字段
        new_region_data = copy.deepcopy(original_region_data)
        
        # 更新几何相关字段
        new_region_data['lines'] = backend_data['lines']
        new_region_data['angle'] = backend_data['angle']
        new_region_data['center'] = backend_data['center']
        
        return new_region_data


# 使用示例和测试函数
def test_stable_geometry_engine():
    """测试稳定几何引擎的正确性"""
    
    # 创建一个简单的矩形
    raw_polygons = [[(0, 0), (100, 0), (100, 50), (0, 50)]]
    visual_anchor = VisualAnchor(200, 150)  # 期望的视觉位置
    rotation = 30  # 旋转30度
    
    geometry_state = GeometryState(
        raw_polygons=raw_polygons,
        rotation_degrees=rotation,
        visual_anchor=visual_anchor
    )
    
    # 测试一致性
    is_consistent = StableGeometryEngine.verify_consistency(geometry_state)
    print(f"一致性测试: {'通过' if is_consistent else '失败'}")
    
    # 获取显示坐标
    display_coords = StableGeometryEngine.get_visual_coordinates_for_display(geometry_state)
    print(f"显示坐标: {display_coords}")
    
    # 获取后端数据
    backend_data = StableGeometryEngine.get_backend_data_for_rendering(geometry_state)
    print(f"后端数据: {backend_data}")
    
    # 测试添加新多边形
    new_polygon = [(250, 160), (300, 160), (300, 180), (250, 180)]
    new_state = StableGeometryEngine.add_polygon_to_geometry(geometry_state, new_polygon)
    
    # 验证添加后的一致性
    is_still_consistent = StableGeometryEngine.verify_consistency(new_state)
    print(f"添加多边形后一致性测试: {'通过' if is_still_consistent else '失败'}")
    
    return is_consistent and is_still_consistent


if __name__ == "__main__":
    test_stable_geometry_engine()