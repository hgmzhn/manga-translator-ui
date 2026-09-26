---
name: rich-text
description: 编辑漫画区域的局部文字样式与 richtext.v1 文档，包括注音和纵中横；只改区域基础样式时无需加载。
---

# 富文本编辑协议：richtext.v1

读取本 skill 后提供 edit_rich_text 工具。要修改一段文字的加粗、斜体、颜色或其他样式，在其 edits 中使用
`op="replace_rich_text"`，提供 `region_id` 和完整 `document`。该操作会替换这个区域的整份富文本文档，
因此必须保留未修改的正文、段落、节点和样式。优先基于当前 `translation_rich` 编辑；没有该字段时，
根据当前 `translation` 创建文档，保留原有换行。只改样式时不得改正文。

## 文档结构

- 文档：`{"format":"richtext.v1","blocks":[...]}`。
- 每个段落：`{"type":"paragraph","inlines":[...]}`。换行用不同的 paragraph 表达。
- 普通文字节点：`{"type":"text","text":"正文","style":{...}}`。
- 同一段落可以包含多个 text 节点。把目标文字单独拆成节点即可只修改该片段。
- 未设置的文字样式使用渲染默认值或区域基础样式。省略无关字段，不填 null，也不添加未知字段。
- `translation_rich` 是结构化 JSON，不是 HTML、Markdown 或带标签的正文字符串。

## 局部样式示例

以下演示只把“重要”两个字加粗并改为红色，其余正文保持不变。
实际调用 edit_rich_text 时，page_id 取宿主提供的公开整数 id，使用实际 region_id 和新的 command_id。

```json
{
  "page_id": 1,
  "command_id": "rich-style-001",
  "edits": [
    {
      "op": "replace_rich_text",
      "region_id": "区域001",
      "document": {
        "format": "richtext.v1",
        "blocks": [
          {
            "type": "paragraph",
            "inlines": [
              {"type": "text", "text": "这是", "style": {}},
              {"type": "text", "text": "重要", "style": {"bold": true, "color": "#ff3300"}},
              {"type": "text", "text": "的事情。", "style": {}}
            ]
          }
        ]
      }
    }
  ]
}
```

## style 字段

| 字段 | 类型和含义 |
|---|---|
| bold、underline、strikethrough、emphasis | 布尔值：加粗、下划线、删除线、着重号 |
| italic | 布尔值或角度数值；true 使用默认斜体，0/false 关闭斜体 |
| color | 文字颜色，`#RRGGBB` |
| fontSize | 大于 0 的绝对字号，优先于 scale |
| scale | 大于 0 的区域字号倍率；1 为原字号 |
| fontFamily | 字体家族名，不能是磁盘路径 |
| stroke、outerStroke | `{"color":"#ffffff","width":0.08}`；宽度为非负字号比例，0 关闭对应描边 |
| glow | `{"color":"#ffffff","blur":0.1}`；blur 为非负字号比例 |
| noTcy | 布尔值，禁止自动纵中横 |
| verticalAdvance | `"half"` 或 `"full"`，竖排半格/全格推进 |
| kerning、preKerning | 字后、字前间距调整，可为负数 |
| lineKerning、nextKerning | 与前一行、后一行的间距调整，可为负数 |
| transform | offsetX、offsetY、rotation、mirrorX、mirrorY、scaleX、scaleY |

transform 的 offsetX/offsetY 为偏移百分比，rotation 为角度，mirrorX/mirrorY 为布尔值；
scaleX/scaleY 必须大于 0，1 为原尺寸。未修改的 transform 子字段保持原值。
区域样式使用 font_size/font_family/font_color，富文本 style 使用 fontSize/fontFamily/color，不能混用。

## 注音 Ruby 与纵中横 TCY

它们是 inlines 中的独立节点，不是 style 字段，内部只放 text 节点。

```json
{
  "format": "richtext.v1",
  "blocks": [
    {
      "type": "paragraph",
      "inlines": [
        {
          "type": "ruby",
          "base": [{"type": "text", "text": "漢字", "style": {}}],
          "text": [{"type": "text", "text": "かんじ", "style": {"scale": 0.5}}]
        },
        {
          "type": "tcy",
          "content": [{"type": "text", "text": "12", "style": {}}]
        }
      ]
    }
  ]
}
```

Ruby 的 base 是正文，text 是注音；TCY 的 content 是竖排内横排的文字。
保留已有 Ruby/TCY 结构，不能为了改样式把注音丢掉。
当前只接入编辑工具时，局部样式使用拆分 text 节点后 replace_rich_text 完成；
set_span_style 需要有效的 occurrence_id，没有宿主提供的匹配记录时不得编造这个 ID。
提交后检查工具自动返回的执行状态和新图片，再决定是否继续调整；不会自动返回完整区域数据，需要确认属性时用 read_page 按区域、字段读取。
