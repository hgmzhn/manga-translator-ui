"""
稳定几何引擎测试工具
用于验证旋转编辑时的反跳变功能
"""
import sys
import os
import math
import json
from typing import List, Dict, Any, Tuple
import numpy as np

# 添加路径以导入我们的模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from stable_geometry_engine import (
    StableGeometryEngine, 
    GeometryState, 
    VisualAnchor, 
    RegionGeometryManager
)


class AntiJumpTester:
    """反跳变功能测试器"""
    
    def __init__(self):
        self.test_results = []
        self.verbose = True
        
    def log(self, message: str, level: str = "INFO"):
        """记录测试日志"""
        if self.verbose:
            print(f"[{level}] {message}")
        
    def create_test_textbox(self, center_x: float = 200, center_y: float = 150, 
                           width: float = 100, height: float = 50,
                           rotation: float = 30) -> Dict[str, Any]:
        """创建一个测试用的文本框数据"""
        # 创建矩形顶点（中心在原点）
        half_w, half_h = width / 2, height / 2
        vertices = [
            [-half_w, -half_h],
            [half_w, -half_h], 
            [half_w, half_h],
            [-half_w, half_h]
        ]
        
        # 平移到指定中心位置
        vertices = [[x + center_x, y + center_y] for x, y in vertices]
        
        return {
            'lines': [vertices],
            'angle': rotation,
            'center': [center_x, center_y],
            'text': 'TEST',
            'translation': 'TEST',
            'font_size': 20
        }
    
    def test_basic_consistency(self) -> bool:
        """测试基本一致性"""
        self.log("=== 测试基本一致性 ===")
        
        # 创建测试数据
        test_data = self.create_test_textbox(200, 150, 100, 50, 30)
        self.log(f"原始数据: center={test_data['center']}, angle={test_data['angle']}")
        
        # 转换为几何状态
        geometry_state = RegionGeometryManager.from_region_data(test_data)
        self.log(f"几何状态: anchor={geometry_state.visual_anchor.as_tuple()}, rotation={geometry_state.rotation_degrees}")
        
        # 验证一致性
        is_consistent = StableGeometryEngine.verify_consistency(geometry_state)
        self.log(f"一致性验证: {'✓ 通过' if is_consistent else '✗ 失败'}")
        
        # 获取后端数据
        backend_data = StableGeometryEngine.get_backend_data_for_rendering(geometry_state)
        calculated_center = backend_data['center']
        
        self.log(f"后端计算中心: {calculated_center}")
        self.log(f"视觉锚点: {geometry_state.visual_anchor.as_tuple()}")
        
        # 检查是否完全一致
        epsilon = 1e-9
        center_match = (abs(calculated_center[0] - geometry_state.visual_anchor.x) < epsilon and
                       abs(calculated_center[1] - geometry_state.visual_anchor.y) < epsilon)
        
        result = is_consistent and center_match
        self.log(f"基本一致性测试: {'✓ 通过' if result else '✗ 失败'}")
        self.test_results.append(('basic_consistency', result))
        return result
    
    def test_rotation_stability(self) -> bool:
        """测试旋转时的稳定性"""
        self.log("\n=== 测试旋转稳定性 ===")
        
        # 创建初始几何状态
        test_data = self.create_test_textbox(200, 150, 100, 50, 0)  # 无旋转开始
        geometry_state = RegionGeometryManager.from_region_data(test_data)
        
        original_anchor = geometry_state.visual_anchor
        self.log(f"原始锚点: {original_anchor.as_tuple()}")
        
        # 测试多个旋转角度
        test_angles = [15, 30, 45, 60, 90, 120, 180, 270, 360]
        all_stable = True
        
        for angle in test_angles:
            # 更新旋转角度
            rotated_state = StableGeometryEngine.update_rotation(geometry_state, angle)
            
            # 验证锚点是否保持不变
            anchor_unchanged = (rotated_state.visual_anchor == original_anchor)
            
            # 验证一致性
            is_consistent = StableGeometryEngine.verify_consistency(rotated_state)
            
            # 获取后端数据验证
            backend_data = StableGeometryEngine.get_backend_data_for_rendering(rotated_state)
            calculated_center = backend_data['center']
            
            # 检查中心是否匹配锚点
            epsilon = 1e-9
            center_match = (abs(calculated_center[0] - original_anchor.x) < epsilon and
                           abs(calculated_center[1] - original_anchor.y) < epsilon)
            
            stable_at_angle = anchor_unchanged and is_consistent and center_match
            all_stable = all_stable and stable_at_angle
            
            status = "✓" if stable_at_angle else "✗"
            self.log(f"  角度 {angle:3d}°: {status} 锚点不变={anchor_unchanged}, 一致性={is_consistent}, 中心匹配={center_match}")
        
        self.log(f"旋转稳定性测试: {'✓ 通过' if all_stable else '✗ 失败'}")
        self.test_results.append(('rotation_stability', all_stable))
        return all_stable
    
    def test_geometry_addition_no_jump(self) -> bool:
        """测试添加几何时无跳变"""
        self.log("\n=== 测试添加几何无跳变 ===")
        
        # 创建带旋转的初始几何状态
        test_data = self.create_test_textbox(200, 150, 100, 50, 45)
        geometry_state = RegionGeometryManager.from_region_data(test_data)
        
        original_anchor = geometry_state.visual_anchor
        self.log(f"旋转45°初始锚点: {original_anchor.as_tuple()}")
        
        # 模拟添加新的多边形（世界坐标）
        new_polygon_world = [(250, 160), (300, 160), (300, 180), (250, 180)]
        self.log(f"添加新多边形: {new_polygon_world}")
        
        # 添加几何
        new_geometry_state = StableGeometryEngine.add_polygon_to_geometry(
            geometry_state, new_polygon_world
        )
        
        # 验证锚点是否保持不变
        anchor_unchanged = (new_geometry_state.visual_anchor == original_anchor)
        
        # 验证一致性
        is_consistent = StableGeometryEngine.verify_consistency(new_geometry_state)
        
        # 获取后端数据
        backend_data = StableGeometryEngine.get_backend_data_for_rendering(new_geometry_state)
        calculated_center = backend_data['center']
        
        # 验证中心点匹配
        epsilon = 1e-9
        center_match = (abs(calculated_center[0] - original_anchor.x) < epsilon and
                       abs(calculated_center[1] - original_anchor.y) < epsilon)
        
        self.log(f"锚点保持不变: {'✓' if anchor_unchanged else '✗'}")
        self.log(f"后端一致性: {'✓' if is_consistent else '✗'}")  
        self.log(f"中心点匹配: {'✓' if center_match else '✗'}")
        self.log(f"计算中心: {calculated_center}")
        self.log(f"期望锚点: {original_anchor.as_tuple()}")
        
        result = anchor_unchanged and is_consistent and center_match
        self.log(f"几何添加无跳变测试: {'✓ 通过' if result else '✗ 失败'}")
        self.test_results.append(('geometry_addition_no_jump', result))
        return result
    
    def test_complex_scenario(self) -> bool:
        """测试复杂场景：多次操作后的稳定性"""
        self.log("\n=== 测试复杂场景 ===")
        
        # 创建初始状态
        test_data = self.create_test_textbox(150, 200, 80, 40, 0)
        geometry_state = RegionGeometryManager.from_region_data(test_data)
        original_anchor = geometry_state.visual_anchor
        
        self.log(f"初始锚点: {original_anchor.as_tuple()}")
        
        # 执行一系列复杂操作
        operations = [
            ('旋转到30°', lambda s: StableGeometryEngine.update_rotation(s, 30)),
            ('添加多边形1', lambda s: StableGeometryEngine.add_polygon_to_geometry(s, [(180, 220), (220, 220), (220, 240), (180, 240)])),
            ('旋转到60°', lambda s: StableGeometryEngine.update_rotation(s, 60)),
            ('添加多边形2', lambda s: StableGeometryEngine.add_polygon_to_geometry(s, [(160, 180), (200, 180), (200, 195), (160, 195)])),
            ('旋转到90°', lambda s: StableGeometryEngine.update_rotation(s, 90)),
            ('旋转到-45°', lambda s: StableGeometryEngine.update_rotation(s, -45)),
        ]
        
        current_state = geometry_state
        all_operations_stable = True
        
        for i, (operation_name, operation_func) in enumerate(operations):
            self.log(f"  执行操作 {i+1}: {operation_name}")
            
            # 执行操作
            current_state = operation_func(current_state)
            
            # 验证锚点不变
            anchor_unchanged = (current_state.visual_anchor == original_anchor)
            
            # 验证一致性
            is_consistent = StableGeometryEngine.verify_consistency(current_state)
            
            operation_stable = anchor_unchanged and is_consistent
            all_operations_stable = all_operations_stable and operation_stable
            
            status = "✓" if operation_stable else "✗"
            self.log(f"    {status} 锚点不变={anchor_unchanged}, 一致性={is_consistent}")
            
            if not operation_stable:
                backend_data = StableGeometryEngine.get_backend_data_for_rendering(current_state)
                self.log(f"    计算中心: {backend_data['center']}")
                self.log(f"    期望锚点: {original_anchor.as_tuple()}")
        
        self.log(f"复杂场景测试: {'✓ 通过' if all_operations_stable else '✗ 失败'}")
        self.test_results.append(('complex_scenario', all_operations_stable))
        return all_operations_stable
    
    def run_all_tests(self) -> bool:
        """运行所有测试"""
        self.log("开始稳定几何引擎测试...")
        
        tests = [
            self.test_basic_consistency,
            self.test_rotation_stability,
            self.test_geometry_addition_no_jump,
            self.test_complex_scenario
        ]
        
        all_passed = True
        for test in tests:
            try:
                result = test()
                all_passed = all_passed and result
            except Exception as e:
                self.log(f"测试异常: {e}", "ERROR")
                all_passed = False
        
        self.log(f"\n{'='*50}")
        self.log("测试总结:")
        
        for test_name, result in self.test_results:
            status = "✓ 通过" if result else "✗ 失败"
            self.log(f"  {test_name}: {status}")
        
        self.log(f"\n总体结果: {'✓ 所有测试通过' if all_passed else '✗ 有测试失败'}")
        self.log(f"{'='*50}")
        
        return all_passed


def main():
    """主测试函数"""
    print("稳定几何引擎 - 反跳变功能测试")
    print("="*50)
    
    tester = AntiJumpTester()
    success = tester.run_all_tests()
    
    # 保存测试报告
    report = {
        'timestamp': 'test_run',
        'total_tests': len(tester.test_results),
        'passed_tests': sum(1 for _, result in tester.test_results if result),
        'results': dict(tester.test_results),
        'overall_success': success
    }
    
    try:
        with open('anti_jump_test_report.json', 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n测试报告已保存到: anti_jump_test_report.json")
    except Exception as e:
        print(f"保存测试报告失败: {e}")
    
    return 0 if success else 1


if __name__ == "__main__":
    exit(main())