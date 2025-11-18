"""
智能长图拼接器测试脚本

用途:
1. 测试基础拼接功能
2. 测试边界检测
3. 验证分段策略
"""

import cv2
import numpy as np
from manga_translator.image_stitcher import SmartImageStitcher, BubbleBoundaryDetector


def create_test_image(index, height=1500, width=800, add_bubbles=False):
    """
    创建测试图片
    
    Args:
        index: 图片编号
        height: 高度
        width: 宽度
        add_bubbles: 是否添加模拟气泡
    """
    # 白色背景
    img = np.ones((height, width, 3), dtype=np.uint8) * 255
    
    # 添加图片编号
    cv2.putText(
        img, f'Page {index}',
        (width//2 - 100, 100),
        cv2.FONT_HERSHEY_SIMPLEX,
        2, (0, 0, 0), 3
    )
    
    # 添加高度信息
    cv2.putText(
        img, f'Height: {height}px',
        (width//2 - 150, height//2),
        cv2.FONT_HERSHEY_SIMPLEX,
        1, (100, 100, 100), 2
    )
    
    if add_bubbles:
        # 顶部气泡
        cv2.rectangle(img, (100, 20), (700, 120), (0, 0, 0), 2)
        cv2.putText(img, 'Top Bubble', (250, 80), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        
        # 底部气泡
        cv2.rectangle(img, (100, height-120), (700, height-20), (0, 0, 0), 2)
        cv2.putText(img, 'Bottom Bubble', (200, height-60),
                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    
    return img


def test_basic_stitching():
    """测试1: 基础拼接功能"""
    print("\n" + "="*60)
    print("测试1: 基础拼接功能")
    print("="*60)
    
    # 创建10张测试图片（总高度15000px，应拼接为1段）
    images = [create_test_image(i+1, height=1500) for i in range(10)]
    configs = [None] * 10
    images_with_configs = list(zip(images, configs))
    
    # 拼接
    stitcher = SmartImageStitcher(max_height=20000)  # 高阈值，应全部拼接
    segments = stitcher.stitch_images(images_with_configs)
    
    # 验证
    print(f"\n✅ 结果: {len(segments)}个段")
    assert len(segments) == 1, "应该拼接为1个段"
    
    img, _, meta = segments[0]
    print(f"   段1: {meta['image_count']}张图, 总高度{meta['total_height']}px")
    assert meta['total_height'] == 15000, "总高度应为15000px"
    
    print("\n✅ 测试1通过!")


def test_auto_segmentation():
    """测试2: 自动分段"""
    print("\n" + "="*60)
    print("测试2: 自动分段")
    print("="*60)
    
    # 创建20张图片（总高度30000px，应分为2段）
    images = [create_test_image(i+1, height=1500) for i in range(20)]
    configs = [None] * 20
    images_with_configs = list(zip(images, configs))
    
    # 拼接
    stitcher = SmartImageStitcher(max_height=15000)
    segments = stitcher.stitch_images(images_with_configs)
    
    # 验证
    print(f"\n✅ 结果: {len(segments)}个段")
    assert len(segments) == 2, f"应该分为2段，实际{len(segments)}段"
    
    for i, (img, _, meta) in enumerate(segments):
        print(f"   段{i+1}: {meta['image_count']}张图, 总高度{meta['total_height']}px")
        assert meta['total_height'] <= 15000, f"段{i+1}超过最大高度"
    
    print("\n✅ 测试2通过!")


def test_bubble_detection():
    """测试3: 气泡边界检测"""
    print("\n" + "="*60)
    print("测试3: 气泡边界检测")
    print("="*60)
    
    # 创建两张图片：一张底部有气泡，一张顶部有气泡
    img_with_bottom_bubble = create_test_image(1, height=1500, add_bubbles=True)
    img_with_top_bubble = create_test_image(2, height=1500, add_bubbles=True)
    img_no_bubble = create_test_image(3, height=1500, add_bubbles=False)
    
    detector = BubbleBoundaryDetector(margin=100)
    
    # 测试3.1: 两张都有气泡
    result1 = detector.detect_boundary_bubbles(img_with_bottom_bubble, img_with_top_bubble)
    print(f"\n测试3.1 - 两张都有气泡:")
    print(f"   状态: {result1['status']}")
    print(f"   底部气泡: {len(result1['bottom_bubbles'])}个")
    print(f"   顶部气泡: {len(result1['top_bubbles'])}个")
    print(f"   可以安全分段: {result1['safe_to_split']}")
    assert not result1['safe_to_split'], "两侧都有气泡，不应标记为安全"
    
    # 测试3.2: 无气泡边界
    result2 = detector.detect_boundary_bubbles(img_no_bubble, img_no_bubble)
    print(f"\n测试3.2 - 无气泡边界:")
    print(f"   状态: {result2['status']}")
    print(f"   可以安全分段: {result2['safe_to_split']}")
    assert result2['safe_to_split'], "无气泡边界应标记为安全"
    assert result2['status'] == 'safe', "状态应为'safe'"
    
    print("\n✅ 测试3通过!")


def test_smart_split_point():
    """测试4: 智能分段点选择"""
    print("\n" + "="*60)
    print("测试4: 智能分段点选择")
    print("="*60)
    
    # 创建混合图片：有些有气泡，有些没有
    images = []
    for i in range(15):
        # 第5张和第10张无气泡（应该优先选择这些位置分段）
        has_bubbles = (i != 4 and i != 9)
        img = create_test_image(i+1, height=1200, add_bubbles=has_bubbles)
        images.append(img)
    
    configs = [None] * 15
    images_with_configs = list(zip(images, configs))
    
    # 拼接（max_height设为能容纳约8张图）
    stitcher = SmartImageStitcher(
        max_height=10000,  # ~8张图
        search_range=5
    )
    segments = stitcher.stitch_images(images_with_configs)
    
    print(f"\n✅ 结果: {len(segments)}个段")
    for i, (img, _, meta) in enumerate(segments):
        indices = meta['image_indices']
        print(f"   段{i+1}: 图{indices[0]+1}-{indices[-1]+1} ({len(indices)}张), 高度{meta['total_height']}px")
    
    print("\n✅ 测试4通过!")


def save_test_results(segments, output_dir='test_output'):
    """保存测试结果"""
    import os
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    for i, (img, _, meta) in enumerate(segments):
        filename = f"{output_dir}/segment_{i+1}_h{meta['total_height']}.png"
        cv2.imwrite(filename, img)
        print(f"   保存: {filename}")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("智能长图拼接器 - 测试套件")
    print("="*60)
    
    try:
        # 运行测试
        test_basic_stitching()
        test_auto_segmentation()
        test_bubble_detection()
        test_smart_split_point()
        
        print("\n" + "="*60)
        print("🎉 所有测试通过!")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    # 运行测试
    run_all_tests()
    
    # 可选：生成示例长图并保存
    print("\n是否生成示例长图? (y/n): ", end='')
    choice = input().strip().lower()
    
    if choice == 'y':
        print("\n生成示例长图...")
        images = [create_test_image(i+1, height=1500, add_bubbles=(i % 3 == 0)) 
                 for i in range(12)]
        configs = [None] * 12
        images_with_configs = list(zip(images, configs))
        
        stitcher = SmartImageStitcher(max_height=10000)
        segments = stitcher.stitch_images(images_with_configs)
        
        save_test_results(segments)
        print("\n✅ 示例长图已保存到 test_output/ 目录")
