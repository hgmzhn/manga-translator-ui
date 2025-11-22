#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试高级文件夹功能
验证所有最新优化是否正常工作
"""

import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication

# 添加路径
sys.path.insert(0, str(Path(__file__).parent / "desktop_qt_ui"))

from desktop_qt_ui.widgets.advanced_folder_panel import show_advanced_folder_dialog


def test_advanced_folder():
    """测试高级文件夹功能"""
    print("="*80)
    print("🧪 测试高级文件夹功能")
    print("="*80)
    
    # 创建应用
    app = QApplication(sys.argv)
    
    # 默认起始目录
    start_dir = str(Path.home() / "Downloads")
    
    print(f"\n📂 起始目录: {start_dir}")
    print("\n✅ 功能验证清单：")
    print("  1. 列标题是否为 '最新话/数量'")
    print("  2. 多源作品的来源是否按最新话时间排序")
    print("  3. 最新来源是否显示最新话名称")
    print("  4. 最新来源是否自动展开")
    print("  5. 排序设置是否保存（关闭后重新打开验证）")
    print("\n🚀 正在打开高级文件夹对话框...\n")
    
    # 显示对话框
    selected_chapters = show_advanced_folder_dialog(
        parent=None,
        start_dir=start_dir
    )
    
    if selected_chapters:
        print(f"\n✅ 用户选择了 {len(selected_chapters)} 个章节：")
        for i, chapter in enumerate(selected_chapters[:5], 1):
            print(f"  {i}. {chapter}")
        if len(selected_chapters) > 5:
            print(f"  ... 还有 {len(selected_chapters) - 5} 个")
    else:
        print("\n❌ 用户取消或未选择章节")
    
    print("\n" + "="*80)
    print("✅ 测试完成！")
    print("="*80)
    
    return 0


if __name__ == "__main__":
    sys.exit(test_advanced_folder())
