# 富文本渲染重构（richtext.v1）代码审查报告

- **日期**：2026-07-09
- **分支**：beta（未提交的工作区改动）
- **范围**：`git diff HEAD`（36 个文件，+1437/−751）+ 新增文件：`manga_translator/rendering/rich_text.py`（380 行）、`desktop_qt_ui/editor/rich_text_editing.py`（644 行）、`desktop_qt_ui/ui/widgets/rich_text_floating_editor.py`（660 行）、`desktop_qt_ui/editor/render_text_value.py`（26 行）、设计文档 `RICH_TEXT_RENDERING.md`、`tests/` 两个测试文件
- **方法**：8 个独立查找角度（逐行扫描 / 删除行为审计 / 跨文件调用追踪 / 复用 / 简化 / 效率 / 实现深度 / CLAUDE.md 规范）产出 42 条候选 → 去重为 33 条论断 → 15 个独立验证代理逐条对抗验证（判 REFUTED 必须从代码构造出反驳并引用行号；两条关键结论有 numpy/实机复现支撑）
- **结论**：**29 条 CONFIRMED、2 条 PLAUSIBLE、2 条 REFUTED**；CLAUDE.md 规范检查无违规

---

## 一、新架构综述

本次重构把译文渲染源从纯字符串升级为结构化富文本文档，核心设计（见 `RICH_TEXT_RENDERING.md`）：

**协议**：`richtext.v1` —— `RichTextDocument > Paragraph(blocks) > inlines`，inline 三种：`text`（带 style）、`ruby`（注音，base+text）、`tcy`（纵中横）。`style` 支持 bold/italic/color/scale/fontSize/fontPath/stroke/outerStroke/glow/emphasis/noTcy/kerning/preKerning/lineKerning/nextKerning/transform。**严格解析**：未知键直接抛 `ValueError`（`_reject_unknown_keys`），不接受旧字段别名（`spans`/`source`/`font_size`/`fontFamily` 等）。

**TextBlock 三字段**：
- `translation`：当前译文字符串（替换/断句/繁简/后处理继续操作它；property setter 保证恒为 `str`，且**每次赋值清空 `translation_rich`**）
- `translation_raw`：替换前译文
- `translation_rich`：结构化文档，**渲染时优先**（`get_translation_for_rendering()`），为空回退 `translation`

**字符串→富文本边界**：`text_replacement_layout.sync_translation_raw_from_layout()`——字符串世界的替换/断句/raw 同步全部完成后，把含 `[BR]`/`【BR】`/`<br>`/换行的译文统一转成多 paragraph 文档。`<H>...</H>` 不再是协议，只是普通文本。

**渲染链路**：`ensure_rich_text_document()` → `paragraph.spans` → 富文本横/竖排布局（`_build_rich_vertical_layout` / `_render_rich_text_horizontal`）→ 按 span 局部 RGBA 合成 → 裁剪。纯字符串（无换行标记）仍走旧渲染器。

**编辑器**：新增浮动富文本编辑器（选中区域时出现），通过 `update_translation_rich` 写回；`render_text_value.py` 作为"取渲染文本"的共享入口（rich 优先）。

架构方向本身是对的（协议单点、严格校验、字符串兼容收口在明确边界），**但实现存在几类系统性偏差**：编辑器侧用裸 dict 重实现了协议层（违反"协议解析只放 rich_text.py"）、编辑操作走"拍平→重建"丢失树形节点、严格校验的失败在各消费层被吞掉或炸穿、以及富文本分支绕过了字号自适应/HQ/反转等旧管线不变量。

---

## 二、发现总览

严重度：P0 = 数据丢失或成品图错误（主流程可达）；P1 = 交互回归/一致性问题；P2 = 架构/重复/死代码；P3 = 性能。

| 编号 | 级别 | 判定 | 位置 | 摘要 |
|---|---|---|---|---|
| F04 | P0 | CONFIRMED | textblock.py:153 / manga_translator.py:1415 | 非法 translation_rich 加载→区域被吞→回写 JSON **永久丢失** |
| F01 | P0 | CONFIRMED（实测） | rich_text_editing.py:87 | 打一个字，全文档 ruby/tcy 被静默拍平 |
| F05 | P0 | CONFIRMED（numpy 复现） | text_render.py:185 | add_color 回归：注音永不可见；stroke=0 整段透明 |
| F03 | P0 | CONFIRMED | workflow_service.py:995 | 导入翻译不清 translation_rich → 成图渲染旧译文 |
| F27 | P0 | CONFIRMED | property_panel.py:1723 | ⇄ 按钮仍产出 `<H>`，渲染器当普通字符画上成品图 |
| F28 | P0 | CONFIRMED | rendering/__init__.py:2498 | 全部多行区域静默丢失 HQ 超采样渲染 |
| F07 | P0 | CONFIRMED | rendering/__init__.py:1544 | 富文本区域绕过字号收缩/自动换行/气泡适配 |
| F08 | P1 | CONFIRMED | rich_text_floating_editor.py:311 | 浮动编辑器陈旧文档：覆盖属性面板修改、撤销复活 |
| F09 | P1 | CONFIRMED | ui/editor/view.py:363 | 选中即抢焦点：Delete 失效、A/D/Q/W/E 打进译文 |
| F29 | P1 | CONFIRMED | render_text_value.py:24 | 未翻译区域不再回退显示 OCR 原文，画布空白 |
| F10 | P1 | CONFIRMED | geometry_commit_pipeline.py:84 | 白框字号反算用纯文本、正算用富文本 → 白框回跳 |
| F30 | P1 | CONFIRMED | textblock.py:518 | 阿语（hr）富文本路径丢失 LTR 数字/拉丁反转 |
| F18 | P1 | CONFIRMED | rich_text.py:90 | glow/outerStroke/noTcy/lineKerning/nextKerning 有 UI 无渲染 |
| F02 | P1 | PLAUSIBLE | textblock.py:328 | setter 无条件清 rich；仅部分翻译返回的错误路径可达 |
| F11 | P2 | CONFIRMED | rich_text_editing.py:10 | 编辑器裸 dict 重实现协议层，已与 rich_text.py 语义分叉 |
| F12 | P2 | CONFIRMED | 7 处 | "富文本→纯文本"包装复制 7 份 + 恒真 isinstance 防御 |
| F13 | P2 | CONFIRMED | text_render.py:417 | 富文本竖排几何公式逐字复制旧路径 |
| F14 | P2 | CONFIRMED | rich_text_editing.py:23 | BR 编解码三份（含 text_render.py 重复定义 _BR_RE） |
| F15 | P2 | CONFIRMED | rich_text_floating_editor.py:377 | 字体目录枚举第 4 份拷贝 |
| F16 | P2 | PLAUSIBLE | text_render.py:200 | hex 颜色解析第 3+ 份（需先扩展公共 helper 再收口） |
| F17 | P2 | CONFIRMED | rich_text_editing.py:268 | 同文件内 7 份 blocks→inlines 游标行走 |
| F19 | P2 | CONFIRMED | textblock.py:148 | try/except 当类型分派；两个零调用 API |
| F20 | P2 | CONFIRMED | text_render.py:153 | 恒 False 桩函数、死 rotate_90/block_cache/block 分支 |
| F22 | P3 | CONFIRMED | rich_text_floating_editor.py:257 | 每次按键 3 遍全量布局，且绕过现有防抖定时器 |
| F24 | P3 | CONFIRMED | rich_text.py:267 | 同一 dict 一条链解析 3~6 次；spans 每访问全量 deepcopy |
| F21 | P3 | CONFIRMED | text_render.py:1694 | 测量用完整光栅化实现，批量渲染 2/3 光栅化纯浪费 |
| F23 | P3 | CONFIRMED | text_render.py:1528 | set_font 在字符循环内：每字符 8~12 次 stat + 缓存永不命中 |
| F25 | P3 | CONFIRMED | rich_text_editing.py:320 | 每键每字符 3+ 次样式深拷贝 |
| F26 | P3 | CONFIRMED | text_render.py:308 | _paste_rgba 无透明快路径，逐字符全量浮点混合 |
| F06 | — | **REFUTED** | rendering/__init__.py:2570 | "测量/绘制两套几何导致拉伸"不成立（补边吸收） |
| F31 | — | **REFUTED** | photoshop_export.py:452 | "PSD 行数统计错"不成立（编辑器写回的就是 [BR]） |

---

## 三、P0：数据丢失与成品图错误

### F04 非法 translation_rich 加载 → 区域永久丢失（最严重）

> 威胁模型更正（2026-07-10）：审查时以"旧实验格式"为主触发源，经确认该格式从未发布、
> 无存量用户文件，**不需要任何旧格式迁移/兼容层**。真实触发源是"任何非法 rich 值"：
> ① 手改导入 JSON（导出原文→外部编辑→load_text 是设计内工作流，导出 JSON 携带 rich 文档）；
> ② 未来版本交叉（协议新增字段后，新版本存档被旧版本打开即 reject）；
> ③ 程序自身写坏文档的 bug（如 F01）。核心缺陷是"解析失败⇒删区域⇒无备份覆盖写回"的放大器。

三个子项全部 CONFIRMED：

- **F04a** `textblock.py:153`：构造函数裸调 `_normalize_rich_translation_value(translation_rich)`，无 try；旧实验格式（`source`/`spans`/`fontFamily`/`font_size` 等键——`tests/test_rich_text_rendering.py:72-141` 的 reject 用例证明这些形状真实存在，项目记忆中 stash 里也有此类半成品）经 `_reject_unknown_keys`（rich_text.py:342-345）抛 `ValueError` 直接穿出 `__init__`。旁边 `translation` 参数的 try（:148-151）也只捕 `TypeError`，漏 `ValueError`。
- **F04b** `manga_translator.py:1415-1419`：load_text 流程 `TextBlock(**region_data)` 抛错被 `except Exception` 吞掉并 `continue`，坏区域从列表剔除；渲染成功后 :3666-3671 **必然**调 `_save_text_to_file`，以空 dict 全量重建并覆盖写回同一路径（:912-915）——被丢区域连同**原文、坐标一起从工程 JSON 中永久消失，无备份**。
- **F04c** 编辑器侧 `text_render_pipeline.py:58-61` 裸 `except Exception: return None`（`log_tag` 参数在 except 里根本没用——**零日志**）→ 旧格式区域画布空白无任何提示；`translation_service.py:186` 只捕 `(TypeError, KeyError)`，`ValueError` 穿出后被外层 :231-233 接住 → **整批翻译静默返回 `[None]*len(texts)`**（预期的 raw-dict 回退被绕过）。

**修复建议**：严格校验上提到加载边界，做字段级降级——解析失败时丢 `translation_rich` 保 `translation` 并记录用户可见警告；`load_text` 的 except 范围收窄到单字段；回写前校验区域数不减，或坏区域原样透传。

### F01 打字拍平 ruby/tcy（实测复现）

`rich_text_editing.py:66-87`：`apply_text_change` 把文档拍平成「可见文本 + 逐字符 style 数组」再由 `_document_from_text_and_styles` 重建，**只会生成 `text` run**——这个中间表示表达不了 ruby/tcy 树节点。查找代理实测：给「漢字」加注音后在文档**任意位置**敲一个字符，全文档所有 ruby/tcy 节点静默降级为纯文本，注音消失且坏文档进撤销栈；`_wrap_range_as_node` 对另一选区做 T/R 包装同样清掉已有节点。三个独立角度（A/C/G）交叉命中。

**修复建议**：编辑操作下沉为对 inline 树的结构化拼接——同文件 `_apply_style_to_inlines` 已是正确做法；或在拍平桥接中把 ruby/tcy 段作为不可分原子参与 diff 对齐。

### F05 add_color 透明回归（numpy 复现）

新版 `add_color`（text_render.py:179/185）删除了旧版 `np.maximum(stroke_char_map, text_alpha)` 的合并——描边色非 None 而描边层全零时，输出 alpha 全零。验证器用 numpy 复刻新旧实现：**旧版 max alpha=255 可见，新版=0 全透明**。

- **场景①（必现）**：横排注音——`_rich_ruby_surface` 固定传 `global_stroke_ratio=0.0`（:1227）→ 全零 border（:1123）→ `_rich_colorized_surface` 仍把区域 bg 当描边色传入（:1214）→ **只要区域描边开启（默认），注音永远不可见**。
- **场景③**：span 级 `style.stroke.width=0`（rich_text.py:24 保留 0 值）→ 该 span 整段透明；若全区域皆此类 span，`put_text_horizontal` 返回 None（:2042），区域被跳过（rendering/__init__.py:2559-2561）。
- 场景②（fg≈bg 区域整体消失）的触发被推翻：`fg_bg_compare`（generic.py:1324-1331）会先校正低对比背景色，全 RGB 网格验证校正后色差最小 45.77 > 15——仅用户显式设 `stroke_width=0` 时可达。

**修复建议**：恢复文字/描边 alpha 合并（`np.maximum`），或 stroke_ratio=0 时传 None 描边色走纯文字分支。

### F03 导入翻译后渲染旧译文

`workflow_service.py:995/1011`（`safe_update_large_json_from_text`）对裸 JSON dict 写 `region['translation']`，**全文件零处理 `translation_rich`**，旧富文本原样写回（:1042-1055）；而这是 UI 导入、管线 `load_text`（manga_translator.py:1214）、服务端路由（translation.py:1136/1311）的**唯一**导入通道。渲染侧 rich 优先（textblock.py:517-519）→ **成图和画布永远显示旧译文，属性面板却显示新译文**。编辑器自己的纯文本写入点（editor_controller.py:777）记得 pop——不对称即隐患。验证修正：`controller_export_service.py:521` 处有守卫不算问题点；OpenCC 路走 TextBlock setter 会清 rich，归入 F02。

**修复建议**：导入路径写 `translation` 时同步 `pop('translation_rich')`（与 editor_controller.py:777 一致）；长期应收口为单一"区域译文写入"helper。

### F27 ⇄ 按钮产出字面 `<H>` 被画上成品图

生产链完整存活：property_panel.py:601/871/2066 按钮插入 ⇄ → `convert_arrows_to_tags`（:95-128）→ `<H>...</H>` 写入 translation（editor_controller.py:688/754）；消费方已全删（全仓 `_H_BLOCK_RE`/`auto_add_horizontal_tags`/`prepare_text_for_direction_rendering` 零命中）。**新增测试甚至明确断言 `<H>ABC</H>` 的 `<`、`H`、`>` 被逐字计高**（test_rich_text_rendering.py:653-664），设计文档 :183 也写明"`<H>` 现在只是普通文本"——按钮成了给成品图写乱码的功能。

**修复建议**：按钮改为生成 `tcy` inline（协议指定的局部横排方案）或临时下架；打开旧工程时对存量 `<H>` 做一次性迁移/剥除。

### F28 多行区域全部静默丢失 HQ 渲染

`sync_translation_raw_from_layout`（rendering/__init__.py:2372）无条件把**所有**含 `[BR]`/`\n` 的译文转成 `translation_rich`（text_replacement_layout.py:161-162 → textblock.py:346-354）；`use_hq_render = (not text_is_structured) and should_use_hq_rendering`（:2498-2502，diff 证实旧代码无此守卫）；`text_render_hq.py` 全文无富文本支持。旧行为：font_size<35 或宽/高<1000 走超采样（text_render_hq.py:200-206）。**低分辨率+小字号+多行是漫画翻译最常见场景**——这些区域全部失去抗锯齿，同页单行区域却仍走 HQ，观感不一致。

**修复建议**：HQ 路径支持富文本（放大字号渲染再缩小对富文本布局同样可行），或对"仅由 BR 转换产生、无任何样式"的文档继续走旧 HQ 字符串路径。

### F07 富文本区域绕过字号自适应

rendering/__init__.py:1544-1560：富文本区域用**初始估算字号**（region.font_size 或 `(h+w)/200`，:1131-1138）做 min/max 截断后直接 `continue`，跳过断行优化（:1566）、[BR] 行布局（:1579）、无 BR 自动换行（:1612）、balloon_fill 二分收缩（:1931）、strict 收缩循环（:2088）全部五个阶段；且无等价收缩——富文本测量按段落单行不换行（text_render.py:1673），`calc_font_from_box` 虽已支持富文档（:315）但该分支从不调用。触发链：编辑器单字上色 → 导入翻译重渲染（workflow_service.py:1021 写 `skip_font_scaling=false`）→ **长译文以估算字号溢出气泡**。（编辑器自身导出默认 `skip_font_scaling=True` 走固定字号分支，不受影响。）

**修复建议**：让收缩迭代接受文档测量（`calc_font_from_box` 已可用），而不是旁路；自动换行可先对"无样式纯 BR 文档"降级到字符串路径复用旧引擎。

---

## 四、P1：交互回归与一致性

### F08 浮动编辑器陈旧文档覆盖模型

浮动编辑器唯一内容同步入口是选中变化时的 `set_region`（rich_text_floating_editor.py:311 ← view.py:408）；`regions_changed` 的订阅者不含它（view.py:402、graphics_view.py:235、property_panel.py:881）。两条已验证路径：① 属性面板改译文（会 pop rich，editor_controller.py:777）后保持选中点样式按钮 → `_apply_style` 基于陈旧 `_document` 覆写回旧文（:519-532，相等守卫 :732-736 因新旧不等而放行）；② 撤销后再输入 → `_on_text_changed` 用陈旧 `toPlainText()` 纯函数重建（rich_text_editing.py:66-84 不读模型）→ **已撤销内容复活**。

**修复建议**：浮动编辑器订阅 `regions_changed`（带自写回环防护），或在 `_apply_style`/`_on_text_changed` 前按 region_id 从模型重取文档。

### F09 选中即抢焦点，画布快捷键被劫持

view.py:363-367（本次新增）：单选区域即 `show()+raise_()+focus_text()`，`focus_text` 即 `text_box.setFocus()`（QTextEdit）。快捷键管理器未改：焦点在文本控件时 Delete 跳过删除区域（shortcut_manager.py:267-273）、A/D/Q/W/E 被注入文本框（:280-333）→ 经 contentsChange → `update_translation_rich` **译文被静默改成「…a」而不是翻页**。改动前选中后焦点留在画布。

**修复建议**：显示不抢焦点（去掉 focus_text，双击或快捷键显式进入编辑），或文本框安装 eventFilter 把导航键还给画布。

### F29 未翻译区域画布空白

旧回退 `text_to_render = original_translation or text_block.text` 在删除侧；新链路对 TextBlock 恒走 `get_translation_for_rendering()`（空译文返回空串，textblock.py:517），`render_text_value.py:26` 的 `or text` 对 TextBlock 是死分支；OCR 流程只写 `text` 字段（editor_controller.py:1591-1597）→ **仅检测/OCR 的区域由"显示原文预览"变为完全空白**。

**修复建议**：`render_text_value_from_text_block` 在渲染值为空时回退 `text_block.text`（即把 :26 的死分支修活）。

### F10 白框字号反算/正算两套文本源

geometry_commit_pipeline.py:84 反算仍取纯 `translation` 传 `calc_font_from_box`（该文件本次**漏改**——`calc_font_from_box` 本身已支持富文档，rendering/__init__.py:315-317）；正算侧已全换 `render_text_value_from_region`（editor_controller.py:69、render_layout_pipeline.py:60/71）。注音行加 0.5 倍字号附加高度（text_render.py:1703-1708）→ 反算字号偏大 → 下次字体编辑按富文本重写白框，**白框回跳/文字溢出**。

**修复建议**：一行改动——geometry_commit_pipeline.py:84 换用 `render_text_value_from_region(region_data)`。

### F30 阿语富文本路径丢失 LTR 反转

textblock.py:518-519 对 rich 提前返回，跳过仅存于字符串路径的 'r' 方向 LTR 数字/拉丁块反转（:523-547）；而多行译文（auto_linebreak 以 [BR] 连接）渲染前被无条件转 rich → **阿语多行区域数字/英文字序与单行区域及旧版相反**。富文本渲染器按逻辑序排 span（text_render.py:1240-1344），`reversed_direction` 恒 False（rendering/__init__.py:2520/2533 只比较 'hl'，而 ARA 是 'hr'）。

**修复建议**：反转逻辑移入富文本渲染路径（对 span 内文本按同规则做 LTR 块反转），或 BR→rich 转换时对 'r' 方向先做字符串反转。

### F18 五个样式字段"有 UI 无渲染"

glow/outerStroke/noTcy/lineKerning/nextKerning：schema 完整（rich_text.py:90-97/139-146/159-166）、编辑器映射齐全、浮动编辑器有 G/OS/NT/LK/NK 按钮，但**渲染器零读取**（全仓检索证实；对照组 emphasis 确有 5 处消费，检索方法可靠）。用户点了没任何效果，且字段会写进存档——将来删字段会让旧文档校验报错。

**修复建议**：遵循设计文档"先定义字段并实现布局绘制再上 UI"——未实现渲染前下架这五个按钮/输入行（保留 schema 兼容读取）。

### F02 setter 无条件清 rich（PLAUSIBLE）

机制全部属实：setter 无新旧值比较（textblock.py:328-330）、后处理循环无条件重赋值（manga_translator.py:4891/4940）、isinstance 守卫恒真（getter 恒返 str）。但主干流程要么进后处理前刚赋新译文（rich 本该清）、要么不调后处理（load_text/replace_translation）。**真实可达路径**：`translate_json_only` 载入含 rich 的 JSON 且翻译服务**部分返回**时（:4504 守卫跳过赋值），未更新区域保留旧译文却在 :4891 被剥离 rich 并写回。

**修复建议**：setter 仅在值变化时失效 rich；后处理赋值前比较新旧值。

---

## 五、P2：架构与重复（清理项）

设计文档明确"文本协议解析只放在 rich_text.py"，但实现中协议知识扩散成了多份拷贝：

- **F11** 编辑器裸 dict 重实现协议层：`rich_text_editing.py:10-15/40-50/53-59` 逐一重复 `rich_text.py` 的 `RICH_TEXT_FORMAT`/`_BR_RE`/`is_rich_text_document`/`legacy_line_breaks_to_document`/`plain_text`；且**已语义分叉**——编辑器版 `is_rich_text_document` 只认 dict 不认 `RichTextDocument` 实例，拿到实例会走 `str(document)` 分支产出 `"{'format': ...}"` 垃圾文本。同目录 `render_text_value.py` 已证明可直接 import。
- **F12** "富文本→纯文本"两行包装复制 **7 份**（manga_translator.py:93、photoshop_export.py:21、textblock.py:83、rendering/__init__.py:138、model_api_renderer.py:158、text_renderer_backend.py:23、render_text_value.py:10），另有 6 处恒真 `isinstance(region.translation, str)` 死防御。应收口为 `rich_text.plain_text_of(value)` / `has_content(value)`。
- **F13** 富文本竖排几何逐字复制旧路径公式（spacing text_render.py:417-418 ≡ :2148-2152；对齐 :1621-1624 ≡ :2176-2179；列位 :436-445 ≡ :2165-2169；布局骨架 :1475-1568 ≡ :1965-2009）——行距模型一改就是两处。
- **F14** BR 编解码三份：rich_text_editing.py:23 ≡ property_panel.py:1725；rich_text_editing.py:29 与 text_render.py:149-150 均 ≡ `rich_text.normalize_rich_linebreaks`（text_render.py:35 还重复定义了 `_BR_RE`，:21 明明已 import rich_text）。
- **F15** 字体目录枚举第 4 份拷贝（rich_text_floating_editor.py:377-386 ≈ property_panel.py:779-803 ≈ layout.py:679-684 ≈ dynamic_settings.py:921-924）。
- **F16**（PLAUSIBLE）`_parse_rgb` 与 generic.py:1336 `hex2rgb`/config.py:55/textblock.py:171 重复，但它额外支持 #RGB 缩写、tuple 透传、钳制与回退——需先扩展公共 helper 再替换。
- **F17** `rich_text_editing.py` 文件内部同一"blocks→inlines 游标行走"复制 7 处，应抽 `walk(document) -> (start, end, run)` 生成器。
- **F19** textblock.py:148-151 用 try/except TypeError 当类型分派 + :163 直写 `_translation` 绕过 setter；`clear_translation_rich()` 与 `sync_plain_when_empty` 全仓库零生产调用，应删。
- **F20** text_render.py 死代码：:153-155 恒 False 桩函数（其结果作为 `rotate_90` 传入使 :1158 分支死亡）、`block_cache` 参数（:1975 `_ = block_cache`）、传统竖排路径不可达的两处 `kind=='block'` 分支（:1985/:2181）。

---

## 六、P3：性能（与编辑器卡顿直接相关）

最近提交刚做过"editor canvas lag"诊断，本批改动在两条热路径上叠加了显著开销，六条全部 CONFIRMED：

- **F22 每键 3 遍全量布局、绕过防抖**：contentsChange 直连无 QTimer（rich_text_floating_editor.py:257→404-415）；每键①白框同步 `calc_box_from_font` 全量测量（editor_controller.py:743）→ ②命令提交后 `updated` 分支**绕过 render_debounce_timer**（graphics_view_rendering.py:49-57，防抖仅在 :62-66 整批分支）同步再测量 → ③ `use_cache=False` 全量渲染（:227）。
- **F24 重复解析 + deepcopy**：`ensure_rich_text_document` 对 dict 无缓存（rich_text.py:319-324），一条链解析 3~6 次；字号二分每试一档都重新解析；`Paragraph.spans` 是 property，**每次访问对每个 span 的 style 做 deepcopy**（:267-282），连 `plain_text()` 都走它。
- **F21 测量用完整光栅化**：measure 只消费 QTextLayout 就能给的 logical_width/ascent/descent，却走逐字形 pathForGlyph+QImage 光栅化+findNonZero（text_render.py:1694→1048）；竖排连 RGBA 上色/特效都做完再丢弃。批量路径每区域 3 次全量光栅化，2/3 纯浪费。廉价替代 `_line_metrics`（:1022-1027）已存在且有缓存。
- **F23 set_font 风暴**：`_style_font_scope` 在 for char 循环内（:1528/:1582），带 fontPath 的 span 每字符 8~12 次 `os.path.exists` + 每次清空 qfonts/measures/vertical 缓存 → `_vertical_base` 缓存永不命中。无选区点 F 会给全文所有 span 加 fontPath（rich_text_editing.py:211-212 空选区扩全文）。
- **F25 每键逐字符 3+ 次深拷贝**（rich_text_editing.py:320/:81-83/:99），style 全程只读，共享引用+按 run 边界拷贝即可。
- **F26 `_paste_rgba` 无透明快路径**（text_render.py:308-327），竖排逐字符调用，目标区域大多全透明。

**建议顺序**：先 F22（去抖 + 白框测量复用渲染那次）与 F24（入口解析一次下传 RichTextDocument、spans 缓存/去 deepcopy）——这两项对编辑器手感收益最大；再 F21（测量降级 `_line_metrics`）、F23（font scope 提到 span 级 + 缓存 key 加字体维度）、F25、F26。

---

## 七、被推翻的候选（记录以免重复上报）

- **F06「测量/绘制两套几何导致文字拉伸」**：执行顺序与双几何前提属实，但 `render()` 在透视变换前把渲染结果按 `dst_points` 长宽比**对称补边**（rendering/__init__.py:2570-2601，h_ext/w_ext 恒非负）→ 单应变换是等比缩放，像素级差异被吸收为留白/微缩，不产生压扁错位。
- **F31「PSD num_lines 行数统计错」**：编辑器富文本写回 `translation` 的就是 `[BR]` 形式（`document_to_storage_text` → `plain_text_to_storage_text`，rich_text_editing.py:22-23），后端 BR→rich 转换也不改 `_translation` → photoshop_export.py:452 统计正确。
  - 注：此实现同时意味着 translation 字段以标记语言（[BR]）与结构化文档并存——与设计文档"编辑器后续应直接编辑 richtext.v1，不要以标记语言字符串作为内部状态"存在张力，但它维持了 PSD 导出等下游兼容，属有意为之的折中，记录备查。

---

## 八、测试与覆盖观察

新增 `tests/test_rich_text_editing.py`、`tests/test_rich_text_rendering.py` 覆盖了协议解析（含旧格式 reject）、测量与横竖排渲染，但**恰好缺**本报告 P0 的场景：

- 无"编辑操作后 ruby/tcy 存活"的用例（F01 会被第一条这样的测试拦住）
- 无"旧格式 translation_rich 加载降级"的集成用例（F04）
- test_rich_text_rendering.py:653-664 断言 `<H>` 逐字计高——测试固化了 F27 的错误行为的下半段（渲染侧），上半段（UI 仍在生产 `<H>`）无测试

## 九、修复优先级路线图

1. **止损（丢数据）**：F04 加载边界字段级降级 + 回写保护；F01 编辑操作结构化改造（`_apply_style_to_inlines` 模式推广）
2. **成图正确性**：F05 恢复 alpha 合并；F03 导入路径 pop rich；F27 ⇄ 按钮改 tcy/下架；F28 HQ 支持富文本或纯 BR 文档降级走旧路径；F07 收缩迭代接入富文本测量
3. **交互**：F09 焦点策略；F08 订阅模型变更；F29 一行回退；F10 一行换 `render_text_value_from_region`；F30 反转迁移；F18 未实现字段下架 UI
4. **性能**（编辑器卡顿）：F22 → F24 → F21 → F23 → F25 → F26
5. **收口清理**：F11/F12/F14/F17（协议与纯文本提取回归 rich_text.py 单点）、F13（几何 helper 共享）、F15/F16、F19/F20（删死代码）；顺带 F02 setter 加值比较

---

## 十、修复记录（2026-07-10）

**全部有效发现已修复**：29 条 CONFIRMED + 2 条 PLAUSIBLE（F02/F16）共 31 条全部落地；F06/F31 为 REFUTED 无需修复。F10 在"正文中心锚定/紧凑框"重构中先行修复（见 RICH_TEXT_RENDERING.md「正文中心与锚定」）。修复由 4 个按文件所有权隔离的代理并行完成：A 核心渲染（F05/F07/F28/F13/F14/F16/F20/F21/F23/F24/F26/F12）、B 数据层（F04a/b/F02/F19/F30/F12）、C 编辑器逻辑（F01/F04c/F29/F11/F17/F25/F14/F12）、D 编辑器 UI/服务（F03/F27/F08/F09/F18/F22/F15/F04c/F12）。

**回归验证**：`tests/test_rich_text_rendering.py` 29/29、`tests/test_rich_text_editing.py` 23/23（新增 10 条含 F01 复现）、`tests/test_textblock_rich_safety.py` 13/13（新建）；核心/编辑器/UI/服务全部模块 offscreen 导入无错。

**关键修法**：F04 = 解析失败降级（丢 rich 保区域+警告）+ load_text 回写保险丝（有失败即拒绝覆盖）；F01 = 节点归属拍平（逐字符携带 ruby/tcy 节点身份，重建按身份重组）；F05 = 恢复 alpha 并集 + stroke_ratio≤0 传 None 描边色；F28 = 无样式文档回退 HQ 字符串路径（plain_equivalent_text）；F07 = calc_font_from_box 收缩 + balloon 蒙版二分（只收缩不放大、不重排）；F27 = ⇄ 生产链移除 + 存量 `<H>` 显示/导入双侧剥除。

**遗留后续项**（非缺陷，记录备查）：
- F22 主体已修（发射 180ms 去抖+全路径 flush 防丢键）；两个次级热点待做：editor_controller 白框同步测量与 graphics_view_rendering `updated` 分支绕过 render_debounce_timer。
- HQ 富文本原生支持（带样式/注音文档仍不走超采样）；TextBlock 层缓存解析后的 RichTextDocument（每区域一次解析）。
- 浮动编辑器"空选区=全文"的样式应用语义（建议要求显式选区）。
- 并发管线另有一处 `_save_text_to_file` 回写（当前不跑 load_text 无风险；若挪入需同查 `ctx.load_text_parse_failures`）。
- `calc_text_block_dimensions`/`_solve_unified_no_br_layout` 两处近似列间距公式未统一到共享 helper（缺 <1 倍率分支，统一会改变现有布局，需单独决策）。
- 'r' 方向区域的 translation_rich 现持久化反转后的 LTR 文本（与渲染一致）；浮动编辑器中该类区域拉丁段显示为反转后顺序。

---

## 附：机器可读 Top-10（按严重度）

```json
[
  {"file": "manga_translator/utils/textblock.py", "line": 153, "summary": "translation_rich 严格校验无守护穿出构造函数，load_text 吞异常丢区域后覆盖回写工程 JSON，区域连原文坐标永久丢失", "failure_scenario": "加载含非法 translation_rich 的工程（手改导入 JSON / 新旧版本交叉 / 程序写坏文档）→ ValueError → 区域被 continue 剔除 → 渲染后 _save_text_to_file 全量重建覆盖写回 → 该区域从工程文件永久消失"},
  {"file": "desktop_qt_ui/editor/rich_text_editing.py", "line": 87, "summary": "apply_text_change 拍平重建只产 text run，任意一次键入静默摧毁全文档 ruby/tcy 节点（已实测复现）", "failure_scenario": "给「漢字」加注音后在任意位置敲一个字符 → contentsChange → 重建文档只剩 text inline → 注音/纵中横全部消失且入撤销栈"},
  {"file": "manga_translator/rendering/text_render.py", "line": 185, "summary": "add_color 删除文字/描边 alpha 合并，描边层全零+描边色非 None 时整段输出全透明（numpy 复现：旧 255 新 0）", "failure_scenario": "区域描边开启（默认）时横排注音永不可见；span 级 stroke.width=0 整段消失，全区域皆此类时 put_text_horizontal 返回 None 区域被跳过"},
  {"file": "desktop_qt_ui/services/workflow_service.py", "line": 995, "summary": "唯一导入翻译通道写 translation 不清 translation_rich，渲染 rich 优先导致成图永远显示旧译文", "failure_scenario": "富文本编辑过的区域走导入翻译 → JSON 中 translation 更新而 rich 保留 → 画布/成图显示旧译文，属性面板显示新译文"},
  {"file": "desktop_qt_ui/ui/widgets/property_panel.py", "line": 1723, "summary": "⇄ 按钮仍把选区转成 <H>...</H> 写入 translation，渲染管线已删全部 <H> 解析，字面 <H> 被画上成品图", "failure_scenario": "点 Horizontal⇄ 按钮或打开含 <H> 旧工程后触发文本回写 → 渲染器把 <、H、> 当普通字符逐字排版（竖排还逐字旋转）"},
  {"file": "manga_translator/rendering/__init__.py", "line": 2498, "summary": "use_hq_render 对结构化文本恒 False，而渲染前所有含 [BR] 的多行译文被无条件转 rich，多行区域全部静默丢失 HQ 超采样", "failure_scenario": "低分辨率/小字号多行译文（最常见场景）→ 转 rich → 跳过 text_render_hq → 锯齿模糊，同页单行区域仍走 HQ 观感不一致"},
  {"file": "manga_translator/rendering/__init__.py", "line": 1544, "summary": "富文本区域用初始估算字号直接 continue，跳过断行优化/自动换行/balloon_fill/strict 全部字号适配阶段且无等价实现", "failure_scenario": "编辑器单字上色 → 导入翻译重渲染（skip_font_scaling=false）→ 长译文以估算字号不换行溢出气泡"},
  {"file": "desktop_qt_ui/ui/widgets/rich_text_floating_editor.py", "line": 311, "summary": "浮动编辑器只在 selection_changed 时同步文档，不订阅模型变更，陈旧文档整体覆盖模型", "failure_scenario": "属性面板改译文后点样式按钮 → 被改回旧文；撤销后再输入 → 已撤销内容复活"},
  {"file": "desktop_qt_ui/ui/editor/view.py", "line": 363, "summary": "单选区域即 show+focus_text 抢走键盘焦点，Delete 不再删区域，A/D/Q/W/E 字母被写进译文", "failure_scenario": "点击任意文本框后按 Delete/A/D → 区域删不掉、译文被静默改成「…a」且不翻页"},
  {"file": "desktop_qt_ui/editor/render_text_value.py", "line": 24, "summary": "TextBlock 渲染取值恒走 get_translation_for_rendering，删除了空译文回退 OCR 原文的行为，:26 的 or text 是死分支", "failure_scenario": "仅检测/OCR 未翻译的区域在编辑器画布完全空白（旧版显示原文预览），用户以为识别失败"}
]
```

---

*审查方法说明：候选由 8 个并行查找代理产出，最终判定以 15 个独立验证代理的对抗验证为准（REFUTED 需构造代码级反驳）。两条被推翻候选与全部修正细节保留在第七节与各条目内。*
