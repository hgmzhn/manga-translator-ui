---
name: mtui-manga-proofreading
description: >-
  End-to-end unattended manga translation, post-translation QA, surgical artwork/inpaint repair,
  and raw-faithful typesetting workflow via manga-translator-ui and mtui-mcp. Activate this skill
  whenever asked to translate/localize an untranslated raw manga folder (or a batch of volumes) OR
  proofread translations, inspect/fix inpainting or speech bubbles, adjust typesetting/line breaks,
  or generate 3-column (Raw | Before | After) comparison reports on an already-translated manga folder.
---

# MTUI 无人值守漫画翻译 · 校对 · 嵌字

把**生肉目录**或**已初翻目录**（也可以是包含多卷/多话的父目录）一路做到出版级质量，最后交付 `日文原图 Raw | 初译 Before | 精修 After` 三栏对比图和一份汇总报告。

## 0. 运行模式：无人值守，跑完为止

用户经常挂机批量处理大量漫画，所以：

- **中途不停、不提问、不阶段性汇报。** 从 Stage 0 一直跑到 Stage 7；批量任务就一卷接一卷跑完，**全部结束后一次性**输出汇总报告。后台任务完成通知只是继续下一步的信号，不是汇报的时机。
- **遇到问题，自己判断最优方案并直接处理。** 按「诊断 → 选最优方案 → 执行 → 验证 → 记录」处理（见 [unattended_batch_ops.md](references/unattended_batch_ops.md) 第 2 节）。处理过程和结果写进 `run_log.md`，最后集中汇报。**只记录不处理是不合格的**；只有试过至少两种不同方案都无效时，才能标为 `UNRESOLVED`。**不允许用跳过 Gate B 的方式换速度，也不允许把未解决项隐瞒成已完成。**
- **零 GUI。** 所有初翻、字体检测、修图、渲染都以 `QT_QPA_PLATFORM=offscreen` 离屏执行；不弹窗，也不要关闭用户已打开的 `desktop_qt_ui`。
- **自动判断输入状态。** `translate-raw` 会自动跳过已有 JSON 和渲染结果的页，并始终把初译结果备份到 `before_result/`。所以不管输入是生肉还是熟肉，Stage 0 都照常运行。

### 目录约定

每话的所有中间产物放在 `<CHAPTER>/manga_translator_work/mtui_qa/`（下文记作 `<QA>`，页面扫描不会读到这里）：

```
<QA>/before_result/   初译渲染备份（Stage 0）
<QA>/audit/           script-audit 台账、suspect_ocr_raw_crops/
<QA>/qa_before.json   Gate B 修改前的审计基线
<QA>/qa_after.json    修改后的审计结果
<QA>/verify_crops/    逐区域自检图
<QA>/comparisons/     三栏对比图（交付物）
<QA>/run_log.md       本话的问题账本
```

脚本统一用 `python mtui_mcp/skills/mtui-manga-proofreading/cli/audit_and_compare.py <子命令>`（下文简写为 `aac <子命令>`），在仓库根目录用 `.venv`（或内置 `packaging/python`）的 Python 执行。

---

## 1. 红线（任何情况都不能违反）

| # | 红线 | 原因 |
|---|---|---|
| 1 | **以原图为唯一准绳**：不盲信 OCR `source_text`，不按“同一角色/同一分镜”去猜字体 | 大多数离谱错译来自 OCR 误读；日漫同一角色连续两个气泡也可能换字体 |
| 2 | **`P001 → P_end` 零跳页、零跳框**：字体扫描、译文核对、视觉自检都要覆盖全部区域 | 抽检会漏掉短框、框外字和粗体 |
| 3 | **连体多室泡（有内凹尖角/缩颈/内刺）1 室 1 框，禁止合并**，跨室断句对齐原文语法分界，相邻气室字块不重叠 | 合并会让译文横跨缩颈，破坏版面 |
| 4 | **竖排气泡 `alignment` 保持 `"left"`（每列齐顶）或 `"auto"`**；整体居中只靠 `center-bubbles` 调 `center` | `"center"` 会让短列上下居中，列首错位 |
| 5 | **YuzuMarker 高置信字体锁定（前置例外优先）**：按 [style_and_pitfalls.md §1.2](references/style_and_pitfalls.md) 互斥判定顺序排除前置例外后，对 `pred_conf ≥ 0.90` 且 `top1 ≥ 0.68` 的判断予以锁定；要推翻，必须先用 `mtui_inspect_region_visual` 看单框高清原图并找到证据 | 凭缩略图推翻高置信结果，是过去最常见的倒退原因 |
| 6 | **本身已经很粗的字库一律 `bold=False`**；原图是粗体时绝不能留在细字重 | 叠假粗会糊成一团，漏掉粗体则丢失语气 |
| 7 | **修图只动文字本身**：气泡黑框和框外原画零误伤；框外无框字用 `tight_halo_rects` + `neural`，禁止大矩形、`solid_fill`、`telea` | 框外字底下就是原画，大面积遮罩会铲平网点和线条 |
| 8 | **封面署名、页眉书名刊头、预告页框外宣传语不翻译**，用 `mtui_delete_regions(restore_artwork=True)` 还原原画 | 这些属于装帧，不是正文 |
| 9 | **Gate B 改过的页必须回归验证**：`newly_introduced_error_count == 0`，且改动页 100% 重新出图逐张复核 | 防止越改越坏 |

---

## 2. 标准流程 Stage 0 ~ 7

### Stage 0 · 初翻与备份
```bash
aac translate-raw --chapter-dir "<CHAPTER>" --backup-before-dir "<QA>/before_result"
```
需要时也可以用 `mtui_run_translation_pipeline`。初翻完成后直接进入 Stage 1。

### Stage 1 · 全卷扫描（字体 + 错译雷达 + QA 基线）
1. **全量字体识别 + QA 初始基线**（优先走 MCP，直接复用同一个常驻 worker 并在扫描后自动释放显存，零额外 GPU 进程）：
   - MCP（首选）：`mtui_qa_audit(target_path="<CHAPTER>", force_yuzumarker=True, glossary_file="<yaml|txt>", save_json_path="<QA>/qa_initial.json", summary_only=True)`
   - CLI（MCP 不可用时兜底）：
     ```bash
     aac qa-audit --chapter-dir "<CHAPTER>" --out-json "<QA>/qa_initial.json" --force-yuzumarker [--glossary <yaml|txt>]
     ```
2. **双语台账与疑似 OCR 误读原图切片**：
   ```bash
   aac script-audit --chapter-dir "<CHAPTER>" --out-dir "<QA>/audit" [--glossary <yaml|txt>]
   ```

整章 QA 报告很大，**一律用 `summary_only=True` 加文件落盘**，需要细节时再按页读取 JSON。

### Stage 2 · 对照原图逐框核对译文
- 逐张查看 `<QA>/audit/suspect_ocr_raw_crops/`，修正 OCR 误读导致的错译和碎框（**合并碎框必须看原图重译整句，不能拼接旧译文**）。
- 按 `P001 → P_end` 结合分镜画面，逐框核对主宾指代、否定与反讽、多义词、术语一致性、语序和腔调。完整清单见 [mistranslation_checklist.md](references/mistranslation_checklist.md)。
- 术语统一用 `mtui_manage_glossary`，通读用 `mtui_chapter_script(reading_order="rtl")`。

### Stage 3 · 修图、版面与字体写入
- **字体与字重**：唯一以 [style_and_pitfalls.md §1.2 ~ §1.3](references/style_and_pitfalls.md) 的互斥判定顺序与映射表为准：
  - **第一步（前置例外优先）**：凡命中 `top1 < 0.68`、`[NARROW-KANA-STRETCH]`、`[THIN-STEM]`、`n_clean ≤ 2`（无相邻框传播）或非核心楷书等例外，**一律不锁定 `≥ 0.90`**，必须先 1:1 对照原图与相邻气泡笔画再决定；
  - **第二步（高置信锁定）**：仅在未命中上述任何例外、且 `pred_conf ≥ 0.90` 同时 `top1 ≥ 0.68` 时锁定，直接按映射表采用（推翻须单框 1:1 原图证据）；
  - **第三步（中高与低置信）**：未命中第一步且 `0.84 ≤ pred_conf < 0.90`（`top1 ≥ 0.68`）一般可采用（与同页同款原字冲突时以 1:1 原图为准）；`pred_conf < 0.84` 必须对照原图后再决定，不能盲从告警。
- **断句、字号、竖排符号**：按 [style_and_pitfalls.md §2](references/style_and_pitfalls.md) 执行，核心是「在符合自然气口的前提下尽量贴合气泡框体」。
- **修图**：按 [style_and_pitfalls.md §3](references/style_and_pitfalls.md) 执行。框内残墨用避开黑框的内缩遮罩加 `solid_fill`；黑框被擦断时用 `erase_rects` 还原；框外无框字用 `tight_halo_rects` 加 `neural`。
- **不翻译的范围**（封面、刊头、目录页、预告页、拟声词）：见 [style_and_pitfalls.md §4](references/style_and_pitfalls.md)。

### Stage 4 · 中文母语通读
整话再通读一遍，重点检查跨页术语、人物声线、相邻气泡有没有复读或抢译。

### Stage 5 · 重渲染与光学居中
分片编辑期间可先 `re_render=False` 批量保存 JSON，Stage 5 统一完成成图刷新与光学居中（优先走 MCP，在同一个常驻 worker 内完成，不启动第二个 GPU 进程）：
- MCP（首选）：若此前存在 `re_render=False` 延迟渲染的修改页，直接调用 `mtui_render_pages(target_path="<CHAPTER>", center_bubbles=True)` 一次性重渲染全部页面并完成闭环光学居中（若所有改动页均已最新出图，也可调用 `mtui_center_bubbles(target_path="<CHAPTER>", passes=2)`）。
- CLI（兜底）：`aac center-bubbles --chapter-dir "<CHAPTER>" --apply-mcp` **仅对中心发生位移的气泡页触发重渲染**；因此若此前做过 `re_render=False` 的纯文本/字体修改，**运行该命令前必须先对所有延迟提交的改动页执行一次 `mtui_render_pages`**，确保不会把旧成图留给后续 `verify-crops` 复核。

### Stage 6 · 双闸门终审（Gate A + Gate B）
1. **保存修改前审计基线（防磁盘膨胀：禁止整章全页建图片快照）**：调用 `mtui_qa_audit(target_path="<CHAPTER>", save_json_path="<QA>/qa_before.json", summary_only=True)` 保存轻量审计 JSON 基线。快照采用**「按需单页存档」**——只有 Gate B 发现某一页确实需要返工动刀时，才在修改该单页前执行一次 `mtui_snapshots(target_path="<PAGE>", action="create", label="pre_gate_b")`，**并将返回的 `snapshot_id` 记入本次运行的 `created_snapshot_ids` 清单**（供单页回退与 Stage 7 按精确 ID 清理使用）。
2. **Gate A（算法指标）**：`error == 0`，`warning` 逐条处理或说明原因。
3. **Gate B（100% 逐区域视觉自检）**：
   ```bash
   aac verify-crops --chapter-dir "<CHAPTER>" --out-dir "<QA>/verify_crops" [--pages 7,13 | --page-start X --page-end Y] [--cards-per-sheet 3]
   ```
   先核对 `verify_crops_manifest.json`：`selected_pages` 必须 100% 覆盖本次负责的页码范围，`missing_artifact_pages` 必须为空（绝不允许因缺 JSON / `missing_inpainted` 修图底图 / 成图静默漏检或把框外字退化为双栏卡；`skipped_pages` 仅允许经原图核实确无正文的无字页），且已看过的 sheet 数必须等于 `verify_sheet_count`。每张 `verify_sheet_*.png` 都要 `read_file` 看过，一张不跳。气泡卡片是 `RAW | RENDERED` 双联图；框外无框字卡片是 `RAW | INPAINTED | RENDERED` 三联图，重点看中间栏的修图质量。按**五项必检**逐框核对：
   1. 修图：黑框完整、残墨清干净、框外原画无误伤、无框字的遮罩影响降到最小、`[ORPHAN-MASK]` 已还原；**Gate B 擦除气泡内残墨后必须复核居中**：擦净大块残墨（如原图省略号/振假名）后，气泡有效空间已变化，必须只对该 `region_index` 重居中或手动微调 `center`（避免因未重居中导致偏心，或整页重居中顶偏同页已手动微调的区域；**注意：对卡片带 `[PERSP-QUAD]` 标记即 `has_perspective_quad=True` 的四点透视框，切勿在未同步保留 `white_frame_quad_local` 的情况下覆盖 `center` 或 `angle`，以免清掉透视变形**）；
   2. 字体与字重符合原图（遵守 `[LOCKED>=0.90]`）；
   3. 字号占空比：常规对白占气室内高 62%~75%；1~3 字的短吼放大到 55%~65%；
   4. 断句：没有破词、行首没有粘附助词、没有孤字尾列、没有单列突刺、没有中间凹坑、字块不重叠；
   5. 竖排齐顶：卡片顶栏 `align=left`，没有 `[!=left!]` 标记。
4. **零倒退回归锁**：记录 Gate B 期间**所有**改过的页面 `modified_pages`（只改了字体、字重或对齐的页也算），将**重新出图**与**局部重居中**严格分开处理后执行回归检查：
   - **所有改动页必须重新出图**：对 `modified_pages` 中的每一页确保调用 `mtui_render_pages(target_path="<PAGE>")`（或编辑时传 `re_render=True`）刷新最终成图；**切勿对所有 `modified_pages` 无差别再次跑整页 `mtui_center_bubbles`**（以免把仅改字体/对齐页中已手工校准的气泡中心重新顶偏），只有当某个具体气泡改了断句/字号/遮罩空间确实需要重居中时，才按上文第 1 条对该目标框单独微调 `center`（仅在整页无手工微调框且全部白泡均需自动居中时，才对该页调用 `mtui_center_bubbles(target_path="<PAGE>")`）；
   - `mtui_qa_audit(target_path="<CHAPTER>", save_json_path="<QA>/qa_after.json", diff_against_json="<QA>/qa_before.json", summary_only=True)`，要求 `regression_diff.newly_introduced_error_count == 0`；
   - 对 `modified_pages` 再跑一次 `verify-crops --pages <modified_pages> --cards-per-sheet 2`（同样核对 `missing_artifact_pages == []`），并逐张复核。
   - 如果出现倒退：先分析是哪次修改引入的，针对性修复；修不好就优先传入本次记录的 `snapshot_id`（或 `mtui_snapshots(target_path="<PAGE>", action="restore", label="pre_gate_b")`）回退这一页，再用另一种方式重做这次修改，直到通过。

### Stage 7 · 三栏对比报告与临时快照清理
```bash
aac compare --raw-dir "<CHAPTER>" --before-dir "<QA>/before_result" --out-dir "<QA>/comparisons" --prefix cmp_v1_
```
（`--after-dir` 可省略：`aac compare` 会通过 `resolve_page_paths` 自动按 `last_output_path` / `last_export_dir` / `-translated` / `manga_translator_work/result` 定位每页最终成图；也可显式指定 `--after-dir "<DIR>"`。）
- **通关后仅按本次运行记录的 `snapshot_id` 清理临时快照**：确认本话全部预期页面均具备 Before 与 After 成图并已完成对比（`comparisons/` 生成完毕，或经核实全页完整覆盖且零像素差异），且全话无遗留 `UNRESOLVED` 项（`STATUS: DONE`）后，**仅遍历本次运行实际创建并记入 `created_snapshot_ids` 的临时快照 ID**——逐个核实该 `snapshot_id` 确属本次运行创建后，调用 `mtui_snapshots(target_path="<CHAPTER>", action="cleanup", snapshot_id="<RECORDED_ID>")` 精确删除；**严禁以共享 `label` 作为清理范围，严禁不传 `snapshot_id` 无过滤清空整章快照**（以免误删先前运行或用户手动保存的检查点），**且除非用户明确要求，严禁删除跨运行复用的 `initial_before_mcp` 基线快照**；若存在 `UNRESOLVED` 异常页，跳过异常页对应的 `snapshot_id`，保留异常页快照供用户抽查。
- 批量任务继续进入下一话；全部完成后，按 [unattended_batch_ops.md](references/unattended_batch_ops.md) 的模板输出**一份总报告**。

---

## 3. 分片并行（100% 全看的执行方式）

单个 agent 看不完整卷的自检图，Stage 2~3 和 Gate B 必须分片执行：

- **整章共享的步骤由主控来做**：Stage 0、Stage 1、术语表、Stage 5 居中渲染、Gate A 和回归锁。
- **分片数按页数自动计算**：`ceil(总页数 / 12)`，每片最多 12 页（保证一个子 agent 看完自己负责的全部 sheet 时上下文装得下）。分片**只能修改自己负责的页**；需要改术语表时，交给主控统一修改。
- **同时运行默认最多 8 个**（用户指定时以用户为准），多出的分片排队。所有分片共用一个 GPU worker，修图和渲染在里面是排队执行的，多开只能加快看图和改字。出现超时、显存不足或限流时，并发减半，稳定后再加回来。
- **分片开工前置硬约束（防漏读手册）**：宿主只会自动加载 `SKILL.md`，不会自动注入 `references/`。每个分片子 agent **启动后的第一轮工具调用必须并行 `read_file` 读取 [style_and_pitfalls.md](references/style_and_pitfalls.md)（唯一字体映射表与排版/修图细则）和 [mistranslation_checklist.md](references/mistranslation_checklist.md)**，严禁没读字体映射表就开始改页。
- **分片的交付内容**：已核对的页码范围、`selected_pages` 与 `skipped_pages`（必须与负责页集合对齐且 `missing_artifact_pages == []`）、`modified_pages`、本次新建的 `created_snapshot_ids`、已看过的 sheet 数量和 `verify_sheet_count` 总数（必须相等）、自动解决的问题、自主做出的判断、未解决项（附已尝试的方案）。
- **同步点**：所有分片完成 Stage 3 后，主控执行 Stage 5（`mtui_center_bubbles`），再把 Gate B 分给各分片；所有分片完成 Gate B 后，主控再执行回归锁。分片执行期间，主控不要跑全局居中。
- 详细的分片提示词模板见 [unattended_batch_ops.md](references/unattended_batch_ops.md)。

---

## 4. 参考资料索引

| 文件 | 内容 |
|---|---|
| [style_and_pitfalls.md](references/style_and_pitfalls.md) | **唯一的字体映射表**、断句/字号/竖排规则、修图协议、非正文范围、缺字回退 |
| [mistranslation_checklist.md](references/mistranslation_checklist.md) | OCR 误读陷阱、语境错译、高频反义句式（通用示例） |
| [unattended_batch_ops.md](references/unattended_batch_ops.md) | 批量遍历、问题自主处理流程、分片与并发、`run_log.md` 与最终汇总报告模板 |
| [case_studies.md](references/case_studies.md) | 实战作品案例集（具体作品的人名、页码、实例） |
| [local_font_specimens.png](references/local_font_specimens.png) | 本地中文字库字形样张，选字体时可以 `read_file` 对照 |
