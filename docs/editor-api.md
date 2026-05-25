# Editor API 接口文档

编辑器内嵌 HTTP REST API 服务器，供外部工具（如 Claude）获取和修改 region 数据。

- **地址**：`http://127.0.0.1:54321`（端口被占用时自动切换随机端口，通过 `/api/status` 查询实际端口）
- **协议**：HTTP REST，JSON 编码（UTF-8）
- **无需认证**，仅监听 `127.0.0.1`
- **索引约定**：所有 `{index}`、`{fi}`（文件索引）、`{ri}`（region 索引）均为 **1-based**（从 1 开始）

---

## 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/status` | 编辑器状态 |
| `GET` | `/api/regions` | 当前页所有 region |
| `GET` | `/api/regions/{index}` | 当前页单个 region |
| `PATCH` | `/api/regions/{index}` | 修改当前页 region（内存 + UI 刷新） |
| `GET` | `/api/files` | 文件列表 |
| `GET` | `/api/search?field=…&value=…` | 跨文件搜索 |
| `PATCH` | `/api/files/{fi}/regions/{ri}` | 修改其他文件 region（写回磁盘） |
| `POST` | `/api/export` | 导出当前页到 `out/` |
| `POST` | `/api/export/{file_index}` | 导出指定文件到 `out/` |
| `POST` | `/api/export` body `{"files": [1,2,3]}` | 批量导出 |

---

## GET /api/status

返回编辑器当前状态。

**响应示例：**

```json
{
  "source_image": "C:\\path\\to\\0003.JPEG",
  "region_count": 2,
  "selected_indices": [1],
  "port": 54321,
  "editor_initialized": true
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `source_image` | string | 当前打开的图片路径，未打开时为空字符串 |
| `region_count` | int | 当前页 region 数量 |
| `selected_indices` | int[] | 当前选中的 region 索引列表（1-based） |
| `port` | int? | 实际监听端口 |
| `editor_initialized` | bool | 编辑器是否已初始化 |

---

## GET /api/regions

返回当前页所有 region 的完整数据（深拷贝）。

**响应示例：**

```json
[
  {
    "lines": [[[531.0, 562.0], [1589.0, 562.0], [1589.0, 623.0], [531.0, 623.0]]],
    "center": [1096.87, 681.03],
    "text": "How did it go?",
    "translation": "用起来怎么样？",
    "angle": 0,
    "font_size": 79,
    "direction": "horizontal",
    "alignment": "left",
    "target_lang": "CHS",
    "source_lang": "en",
    "line_spacing": 2.0,
    "letter_spacing": 1.0,
    "stroke_width": 0.07,
    "font_path": "fonts/半圆体.ttf",
    "font_color": "#fbf8fc",
    "bg_color": [18, 161, 245],
    "render_box_rect_local": [-558.0, -88.0, 558.0, 88.0]
  }
]
```

编辑器未初始化时返回 `[]`。

---

## GET /api/regions/{index}

返回当前页指定索引的 region。`{index}` 从 1 开始。

**错误：**

| 状态码 | 条件 |
|--------|------|
| 400 | index 不是正整数 |
| 404 | 编辑器未初始化 或 index 超出范围 |

---

## PATCH /api/regions/{index}

修改当前页指定 region 的字段。`{index}` 从 1 开始。修改会立即反映在编辑器 UI 上。

**请求体：** JSON object，仅包含需要修改的字段（合并更新，未列出的字段保持不变）。

**请求示例：**

```bash
# 修改描边颜色为白色
curl -X PATCH http://127.0.0.1:54321/api/regions/0 \
  -H "Content-Type: application/json" \
  -d '{"bg_colors": [255, 255, 255]}'

# 修改译文
curl -X PATCH http://127.0.0.1:54321/api/regions/0 \
  -H "Content-Type: application/json" \
  -d '{"translation": "你好世界"}'

# 同时修改多个字段
curl -X PATCH http://127.0.0.1:54321/api/regions/0 \
  -H "Content-Type: application/json" \
  -d '{"translation": "你好世界", "bg_colors": [255, 255, 255], "font_size": 60}'
```

**响应示例：**

```json
{"ok": true, "index": 0}
```

**注意事项：**

- 几何字段（`center`、`lines`、`angle`、`white_frame_rect_local`、`has_custom_white_frame`、`render_box_rect_local`）**不可通过 API 修改**，会被静默忽略
- 设置 `bg_colors` 时会自动同步 `bg_color`，确保属性面板显示一致
- 修改**不经过撤销系统**（Ctrl+Z 无法回退 API 修改）
- 写入是异步的（通过 Qt 信号），HTTP 响应先于 UI 刷新返回

---

## GET /api/files

返回编辑器文件队列中的所有图片。

**响应示例：**

```json
[
  {
    "index": 0,
    "path": "C:\\path\\to\\0001.JPEG",
    "json_path": "C:\\path\\to\\manga_translator_work\\json\\0001_translations.json",
    "file_type": "source"
  },
  {
    "index": 1,
    "path": "C:\\path\\to\\0002.JPEG",
    "json_path": null,
    "file_type": "untranslated"
  }
]
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | int | 文件在列表中的索引 |
| `path` | string | 图片绝对路径 |
| `json_path` | string? | 关联的 `_translations.json` 路径，无则为 null |
| `file_type` | string | `"source"` = 已翻译（有 JSON），`"untranslated"` = 未翻译 |

---

## GET /api/search?field=…&value=…

跨文件搜索：在所有文件的 `_translations.json` 中查找指定字段值匹配的 region。

**查询参数：**

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `field` | 否 | `translation` | 要匹配的 region 字段名 |
| `value` | 是 | — | 精确匹配的值（URL 编码） |

**请求示例：**

```bash
# 搜索译文为"宵宫"的所有 region
curl "http://127.0.0.1:54321/api/search?field=translation&value=%E5%AE%B5%E5%AE%AB"

# 搜索原文
curl "http://127.0.0.1:54321/api/search?field=text&value=Hello"

# 搜索指定 source_lang 的 region
curl "http://127.0.0.1:54321/api/search?field=source_lang&value=en"
```

**响应示例：**

```json
[
  {
    "file_index": 1,
    "file_path": "C:\\path\\to\\0002.JPEG",
    "region_index": 1,
    "is_current_page": true,
    "region": { "translation": "宵宫", "text": "Yoimiya", ... }
  },
  {
    "file_index": 5,
    "file_path": "C:\\path\\to\\0006.JPEG",
    "region_index": 0,
    "is_current_page": false,
    "region": { "translation": "宵宫", "text": "Yoimiya", ... }
  }
]
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_index` | int | 在 `/api/files` 列表中的索引 |
| `file_path` | string | 图片绝对路径 |
| `region_index` | int | 在该文件中的 region 索引 |
| `is_current_page` | bool | 是否为编辑器当前打开的图片 |
| `region` | object | 完整的 region 数据（深拷贝） |

匹配规则为**精确匹配**（`str(region[field]) == value`）。

---

## PATCH /api/files/{file_index}/regions/{region_index}

修改指定文件的 region 数据。对非当前页的文件直接读写磁盘上的 `_translations.json`。

**请求体：** 与 `PATCH /api/regions/{index}` 相同，JSON object 合并更新。

**请求示例：**

```bash
# 修改第 5 个文件（0006.JPEG）的第 0 个 region 的描边颜色
curl -X PATCH http://127.0.0.1:54321/api/files/5/regions/0 \
  -H "Content-Type: application/json" \
  -d '{"bg_colors": [255, 255, 255]}'

# 修改译文
curl -X PATCH http://127.0.0.1:54321/api/files/5/regions/0 \
  -H "Content-Type: application/json" \
  -d '{"translation": "你好世界"}'
```

**响应示例：**

```json
{"ok": true, "file_index": 5, "region_index": 0}
```

**注意事项：**

- 如果 `{file_index}` 对应的是当前页，会自动走内存路径（与 `PATCH /api/regions/{ri}` 行为一致，UI 实时刷新）
- 非当前页的修改**直接写磁盘**，下次打开该图片时生效，不会触发 UI 刷新
- 几何字段同样被拒绝
- 文件无 JSON（`file_type` 为 `untranslated`）时返回 404

**错误：**

| 状态码 | 条件 |
|--------|------|
| 400 | 路径格式错误、请求体不是 JSON object |
| 404 | 文件索引或 region 索引超出范围 |

---

## 典型工作流

### 1. 修改当前页描边颜色

```bash
# 查看当前页 regions
curl http://127.0.0.1:54321/api/regions

# 修改 region 0 的描边颜色为白色
curl -X PATCH http://127.0.0.1:54321/api/regions/0 \
  -H "Content-Type: application/json" \
  -d '{"bg_colors": [255, 255, 255]}'
```

### 2. 查找并同步跨页重复译文

```bash
# 1. 获取当前页某 region 的译文
curl http://127.0.0.1:54321/api/regions/0
# → translation: "你好"

# 2. 搜索所有文件中相同译文
curl "http://127.0.0.1:54321/api/search?field=translation&value=%E4%BD%A0%E5%A5%BD"
# → 返回 5 个匹配，file_index 分别为 1, 4, 8, 13, 21

# 3. 审视结果，决定修改哪些
#    修改 file 4 的 region 1
curl -X PATCH http://127.0.0.1:54321/api/files/4/regions/1 \
  -H "Content-Type: application/json" \
  -d '{"translation": "您好", "bg_colors": [255, 255, 255]}'

# 4. 批量修改其余匹配项
curl -X PATCH http://127.0.0.1:54321/api/files/8/regions/1 \
  -H "Content-Type: application/json" \
  -d '{"translation": "您好"}'
```

### 3. 导出图片

```bash
# 导出当前页
curl -X POST http://127.0.0.1:54321/api/export
# → {"success": true, "output_path": ".../out/0007.jpeg"}

# 导出指定文件（第 7 张图）
curl -X POST http://127.0.0.1:54321/api/export/7
# → {"success": true, "output_path": ".../out/0007.jpeg"}

# 批量导出（第 7、8、9 张图）
curl -X POST http://127.0.0.1:54321/api/export \
  -H "Content-Type: application/json" \
  -d '{"files": [7, 8, 9]}'
# → {"results": [
#     {"file_index": 7, "success": true, "output_path": ".../out/0007.jpeg"},
#     {"file_index": 8, "success": true, "output_path": ".../out/0008.jpeg"},
#     {"file_index": 9, "success": true, "output_path": ".../out/0009.jpeg"}
#   ]}
```

### 3. 一次修改所有匹配项的描边颜色

```bash
# 搜索 + 批量 PATCH
for fi in 0 3 7 12 20; do
  curl -s -X PATCH "http://127.0.0.1:54321/api/files/$fi/regions/0" \
    -H "Content-Type: application/json" \
    -d '{"bg_colors": [255, 255, 255]}'
done
```

---

## 常见字段速查

| 字段 | 类型 | 说明 |
|------|------|------|
| `translation` | string | 译文 |
| `text` | string | 原文 |
| `font_size` | int | 字体大小 |
| `font_path` | string | 字体文件路径 |
| `font_color` | string | 字体颜色（hex，如 `"#ffffff"`） |
| `bg_color` | int[3] | 描边颜色（RGB，如 `[255, 255, 255]`） |
| `bg_colors` | int[3] | 描边颜色（别名，设置时自动同步 `bg_color`） |
| `stroke_width` | float | 描边宽度比例（默认 0.07） |
| `line_spacing` | float | 行间距倍数 |
| `letter_spacing` | float | 字间距倍数 |
| `alignment` | string | 对齐方式：`"left"` / `"center"` / `"right"` / `"auto"` |
| `direction` | string | 文本方向：`"horizontal"` / `"vertical"` / `"auto"` |
| `target_lang` | string | 目标语言代码 |
| `source_lang` | string | 源语言代码 |
