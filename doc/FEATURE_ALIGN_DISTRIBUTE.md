# manga-translator-ui 编辑器增强功能

## 分支: feature/align-distribute-buttons

### 1. PS 风格对齐/分布工具栏

**位置**: `desktop_qt_ui/widgets/editor_toolbar.py`

单行 8 个 QPainter 矢量图标按钮（28×28），配色自适应主题。布局：

```
[选区|画布] [左对齐][水平居中][右对齐][垂直间距分布][顶对齐][垂直居中][底对齐][水平间距分布]
```

- 参照模式：选区（包围盒基准）/ 画布（整图基准）
- 画布参照时支持单文本框居中
- 按钮启用条件：对齐 ≥2 框（画布模式 ≥1）、间距分布 ≥3 框
- 间距分布：等分空白间隙，非等分边缘位置

**核心逻辑**:
- `desktop_qt_ui/editor/alignment_service.py` — `align_items()` / `distribute_items()` / `distribute_spacing_items()`
- `desktop_qt_ui/editor/commands.py` — `MultiRegionUpdateCommand`（批量原子更新 + 撤销/重做）
- 25 个单元测试: `desktop_qt_ui/editor/tests/test_alignment_service.py`

### 2. 批量移动

**位置**: `desktop_qt_ui/editor/graphics_items.py`

选中多个文本框后，拖拽任意一个，其余同步跟随。

- `_capture_batch_drag_peers()` — 记录其他选中项的初始位置
- `_move_batch_peers()` — 应用相同场景位移
- `_commit_batch_peers()` — 提交所有位移到模型

### 3. 智能间距吸附

**位置**: `desktop_qt_ui/editor/graphics_items.py`

拖拽文本框时检测等距关系（如 A-B 间距 = B-C 间距），显示双组辅助线和距离标签。

- `_detect_spacing_snap()` — 四方向独立检测
- 阈值 5px，间距优先于边缘吸附
- 辅助线 + 距离标签: `_show_guide_lines()`

### 5. Bug 修复

| 修复 | 位置 |
|------|------|
| 导出漂移（center 更新不同步 local 坐标） | `controller_export_service.py:apply_white_frame_center` |
| 对齐后双重位移（center + wf_local 都改） | `editor_controller.py:_sync_items_positions` |
| 导出 JSON 中 `rich_text` 被后端当文字渲染 | `export_service.py:_save_regions_data_internal` |

### 6. 工具栏精简

- 隐藏缩放按钮（+/-）和百分比标签
- 图标按钮统一 10px 间距
