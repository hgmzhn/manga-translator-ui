# Manga Translator Desktop UI 领域词汇表

## 核心概念

| 术语 | 定义 |
|------|------|
| **Editor Region** | 编辑器中的单个文本区域，包含文字内容、几何信息和渲染样式。核心数据结构为 `Dict[str, Any]`，存储在 `EditorSession` → `ResourceManager` 中。 |
| **ApiServer** | 嵌入在编辑器进程中的 HTTP REST API 服务器。运行在独立 daemon 线程，通过 `pyqtSignal` 安全地修改主线程中的 model 数据。 |
| **Export** | 通过后端流水线（inpainting + 渲染）将编辑器 region 数据合成为最终图片，输出到项目 `out/` 目录。API 支持导出当前页、指定文件、批量导出。 |
| **RenderParameters** | 渲染参数数据类，包含字体、颜色（`fg_color`/`bg_color`）、描边（`stroke_width`）、布局等字段。`bg_color` 同时也是描边颜色。 |
| **EditorController** | 编辑器控制器 (Controller in MVC)，处理所有业务逻辑和用户交互。 |
| **EditorModel** | 编辑器数据模型 (Model in MVC)，通过 `pyqtSignal` 通知 UI 更新。 |
| **UpdateRegionCommand** | 用于修改 region data 的 `QUndoCommand`，支持撤销/重做。ApiServer 的写入**不经过**此路径。 |
