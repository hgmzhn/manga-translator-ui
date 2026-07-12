# 富文本渲染说明

当前渲染器以结构化富文本文档作为渲染源。富文本能力只接受 `richtext.v1` 结构化数据；旧字符串只保留强制换行标记的兼容收口。

## 数据结构

标准格式是 `richtext.v1`：

```json
{
  "format": "richtext.v1",
  "blocks": [
    {
      "type": "paragraph",
      "inlines": [
        {
          "type": "text",
          "text": "普通",
          "style": {}
        },
        {
          "type": "text",
          "text": "红字",
          "style": {
            "color": "#ff0000",
            "fontFamily": "Arial Unicode MS",
            "fontSize": 32,
            "stroke": {
              "color": "#000000",
              "width": 6
            }
          }
        },
        {
          "type": "ruby",
          "base": [
            {
              "type": "text",
              "text": "漢字",
              "style": {}
            }
          ],
          "text": [
            {
              "type": "text",
              "text": "かんじ",
              "style": {}
            }
          ]
        },
        {
          "type": "tcy",
          "content": [
            {
              "type": "text",
              "text": "2026",
              "style": {}
            }
          ]
        }
      ]
    }
  ]
}
```

这里故意不设计 `source` 字段。富文本编辑器或导入器应直接生成 `richtext.v1`，不要把标记语言原文塞回渲染层。

## 协议校验

渲染器现在按协议严格解析，存储结构就是渲染结构，不再接受旧字段别名或外层包装：

- 顶层只允许 `format` 和 `blocks`。
- `paragraph` block 只允许 `type` 和 `inlines`，不接受旧的 `spans` 字段。
- `text` inline 只允许 `type`、`text`、`style`。
- `ruby` inline 只允许 `type`、`base`、`text`。
- `tcy` inline 只允许 `type`、`content`。
- `style` 字段只认 camelCase 协议名，例如 `fontSize`、`fontFamily`、`outerStroke`、`noTcy`、`preKerning`、`lineKerning`、`nextKerning`。
- `transform` 字段只认 `offsetX`、`offsetY`、`rotation`、`mirrorX`、`mirrorY`。
- `source`、`document`、`font_size`、`fontFamily`、`outer_stroke` 这类非协议字段会直接报错。

唯一保留的旧输入兼容是普通字符串里的强制换行标记：`[BR]`、`【BR】`、`<br>` 和真实换行。它们不会在翻译赋值或布局前提前转换，而是在替换、断句、去换行标点等字符串处理结束后，由 `sync_translation_raw_from_layout()` 统一收口成多个 `paragraph` block。

## TextBlock 存储

`TextBlock` 现在只保留三类译文字段：

- `translation`：当前译文字符串。翻译结果、替换、繁简转换、后处理重试、AI 断句和自动断句都继续操作这个字段。
- `translation_raw`：替换前译文字符串。布局阶段如果插入或调整了 `[BR]`，会通过现有 BR 定位/投影逻辑同步回 raw。
- `translation_rich`：结构化 `richtext.v1` 文档。渲染时优先读取这个字段；如果为空，就退回 `translation` 字符串。

传统 BR 的收口位置在 `text_replacement_layout.sync_translation_raw_from_layout()`：这里已经完成字符串替换、断句优化和 raw 同步，适合作为“字符串世界”到“富文本世界”的边界。这样前面的字符串处理不会因为中途变成 dict 而跳过。

BR 收口只负责把传统换行字符串拆成多个 `paragraph`，不会从 `TextBlock` 区域字段迁移字体、字号、颜色或描边。样式必须直接存在于 `translation_rich` 的 `style` 里：

- `style.color`：字体颜色。
- `style.stroke.color` / `style.stroke.width`：描边颜色和描边宽度比例，`width` 使用相对字号的比例值。
- `style.fontSize`：当前区域字号。
- `style.fontFamily`：Qt 字体 family。字体文件仅在加载阶段注册，协议不保存路径。

## 模块职责

- `rich_text.py`
  - 定义 `richtext.v1` 数据模型。
  - 提供 `RichTextDocument`、`Paragraph`、`TextRun`、`RubyRun`、`TcyRun`、`TextStyle`、`StrokeStyle`、`GlowStyle`、`TextTransform`。
  - 提供 `ensure_rich_text_document()` 和 `is_rich_text_document()` 给渲染入口判断和规范化输入。

- `text_render.py`
  - 字符串输入继续走原来的纯文本渲染路径。
  - 只有 `RichTextDocument` 或 `{"format": "richtext.v1", ...}` dict 才会走结构化富文本渲染路径。
  - 渲染路径不解析标记语言，也不做 tag strip。

- `rendering/__init__.py`、`utils/textblock.py`
  - 避免把结构化富文本文档当字符串处理。
  - 结构化文档会跳过大小写转换、自动断句等纯字符串专用逻辑。
  - 旧换行写法 `[BR]`、`【BR】`、`<br>`、真实换行只作为兼容输入存在，在 raw 同步边界转换成多个 `paragraph` block。
  - 旧的竖排内横排设置和方向标记不再作为协议支持；需要局部排版能力时应新增结构化 node 或 style 字段。

- `text_replacements.py`
  - 普通字符串替换继续使用原来的 BR 占位保护。
  - 替换层只处理 `translation` 字符串；已有 `translation_rich` 的区域直接跳过替换。
  - 传统 BR 的结构化转换放在 raw 同步后执行，不需要额外的富文本替换保护层。

## 渲染链路

结构化富文本路径：

```text
RichTextDocument / richtext.v1 dict
  -> ensure_rich_text_document()
  -> paragraph.spans
  -> 横排或竖排富文本布局
  -> 复用现有 Qt/glyph helper 做字形光栅化
  -> 按 span 做局部 RGBA 合成
  -> 裁剪有效 alpha 区域
  -> 返回 RGBA 图层
```

普通字符串路径仍然保持原逻辑：

```text
string
  -> 替换 / 断句 / 去换行标点 / raw 同步
  -> sync_translation_raw_from_layout()
  -> 旧换行标记兼容转换到 translation_rich
  -> richtext.v1 document
  -> 结构化富文本渲染路径
```

不含旧换行标记的普通字符串仍按纯文本渲染：

```text
string
  -> 原有 compact 处理
  -> 原有横排或竖排渲染器
```

## 当前支持范围

结构化富文本路径目前支持：

- `text` inline 节点。
- `ruby` inline 节点。
- `tcy` inline 节点；横排按普通 inline 渲染，竖排按纵中横块渲染。
- 局部填充颜色。
- 局部字号倍率和绝对字号。
- 局部描边颜色和描边宽度。
- 着重号。
- 从 `richtext.v1` 直接横排和竖排渲染。
- 结构化文档测量入口：
  - `get_string_width()`
  - `get_string_height()`
  - `calc_horizontal()`
  - `calc_vertical()`
  - `calc_vertical_metrics()`
  - `measure_rich_text_metrics()`（渲染框尺寸 + 正文中心点）

## 正文中心与锚定

渲染框（白框、dst_points、绘制图层）包含框外装饰：横排的首行注音在框顶部、
末行着重号在框底部；竖排的首列注音在框右侧。竖排渲染框是紧凑框，左右溢出
各按真实需要计算，不做对称扩张。因此**渲染框正中心 ≠ 正文中心**。

测量层把正文中心作为事实返回，锚定（“什么钉住不动”）是调用方的策略：

- `calc_box_from_font(center=None)` 返回 `(宽, 高, 行数, 正文中心)`，正文中心
  是框内坐标（相对渲染框左上角）；纯文本与无装饰文档恒为 `(宽/2, 高/2)`。
- `calc_box_from_font(center=...)` 返回 `(dst_points, 正文中心世界坐标)`，
  dst_points 以 center 为渲染框正中心，正文点与角点走同一旋转变换。
- 批量管线（`_calc_region_dst_points_for_font`）：管线自算锚点（top / 气泡
  center）语义是“正文中心该在哪”，拿到 dst 后按 `锚点 − 正文世界坐标` 平移
  整框；编辑器授权中心（`skip_font_scaling` → `center_box`）保持渲染框中心
  语义不平移，与编辑器预览逐像素一致。纯文本平移量恒为零。
- 编辑器白框同步（`_sync_white_frame_size_for_font_change`）：
  `新框正中心 = (旧框正中心 + 旧正文差值) − 新正文差值`，
  差值 = 正文中心 − 框正中心（框内坐标），新旧文本各问一次后端。
  增删注音/着重号时正文本体钉住不动，白框只向装饰一侧扩缩。

## 设计规则

- 不要在渲染层新增标记语言解析。
- 不要在结构化文档旁边保存标记语言原文。
- 不要为了布局或渲染对结构化文档调用 `str(...)`。
- 不要恢复旧的竖排内横排设置或方向标记兼容层。
- `<H>...</H>` 现在只是普通文本，不再参与测量、断句或渲染协议；需要局部横排时使用 `tcy` inline 节点。
- 文本协议解析只放在 `rich_text.py`。
- 字形光栅化和合成逻辑暂时仍在 `text_render.py`，后续可以继续拆成独立 layout / paint 模块。
- 新增富文本能力时，先定义明确的 node 或 style 字段，再实现布局和绘制。

## 后续工作

- 把富文本横排/竖排布局从 `text_render.py` 拆到独立 layout 模块。
- 把 span 级 RGBA 合成拆到独立 paint 模块。
- 增加结构化文档的单元测试：
  - 解析
  - 序列化
  - 测量
  - 横排渲染
  - 竖排渲染
- 编辑器侧本次暂不改；后续应直接编辑 `richtext.v1`，不要再以标记语言字符串作为内部状态。
