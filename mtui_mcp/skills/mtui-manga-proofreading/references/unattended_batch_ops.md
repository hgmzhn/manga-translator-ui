# 无人值守批量运行手册

> 原则：**不停、不问、自己拿主意、跑完再汇报。**
> 遇到问题时，Agent 要**自己判断当前最优的处理方案并直接执行**，执行后验证效果，然后把“遇到了什么、怎么处理的、结果如何”记进 `run_log.md`，最后集中汇报。
> 只记录不处理是不合格的。只有在**至少尝试过两种不同方案**都无效时，才允许把问题标为 `UNRESOLVED`，并且要写明试过什么。

---

## 1. 批量遍历

1. 输入目录本身含有图片页 → 当作**一话**处理。
2. 输入目录不含图片、但子目录含图片 → 按自然排序（`第2话` 排在 `第10话` 前面）**逐个子目录**当作一话。跳过 `manga_translator_work`、`backups`、`.git`。
3. 各话**严格按顺序逐话执行 Stage 0~7**；一话内部按第 4 节分片并行。**上一话完全结束（含 Stage 7）前不要提前启动下一话的 Stage 0~1**；进入下一话跑命令行 `aac translate-raw` 前，若常驻 worker 刚跑过 `neural` 修图，先调用一次 `mtui_status(release_vram=True)` 释放常驻显存缓存，杜绝 8GB 显卡双进程抢显存。
4. 每话结束时（Stage 7 生成完 `comparisons/` 后），若全话 `STATUS: DONE`，立即调用 `mtui_snapshots(target_path="<CHAPTER>", action="cleanup")` 清空本话 `.mtui_mcp_snapshots` 临时快照省空间（若有 `UNRESOLVED` 页则仅清理正常页、保留异常页快照），再向 `<QA>/run_log.md` 写入本话最终状态并进入下一话。
5. **断点续跑**：如果某话的 `run_log.md` 已经标记 `STATUS: DONE`，并且 `comparisons/` 不为空，就直接跳过（除非用户明确要求重做）。

---

## 2. 问题处理：诊断 → 选最优方案 → 执行 → 验证 → 记录

### 2.1 通用决策顺序
1. **诊断原因**：先看报错、日志和 `mtui_status`，判断是环境问题（显存、进程、路径）、输入问题（坏图、缺页），还是质量问题（修图、版面、译文）。
2. **选方案**：按「效果最好 → 风险最小 → 代价最低」排序，从候选方案里选当前最优的一个**直接执行**，不需要等任何人确认。
3. **验证**：执行后必须重新检查（重新审计、重新出 verify 卡片、或重新调用该工具），确认真的解决了。
4. **没解决就换方案**：换一个**思路不同**的方案再试，而不是同一个方案反复重试。
5. **记录**：写进 `run_log.md` 对应的分类（见第 3 节）。

### 2.2 常见场景的推荐处理（参考用，Agent 应结合现场判断，有更好的方案就用更好的）

| 场景 | 推荐处理（按优先级） |
|---|---|
| MCP 调用报错或超时 | ① 查 `mtui_status`，worker 挂了就重新预热后重试；② 改用等价的 CLI 子命令（`aac qa-audit`、`aac verify-crops` 等）；③ 把大范围调用拆成单页调用 |
| GPU 显存不足 / worker 崩溃 | ① 降低并发（暂停新分片的 GPU 任务），重启 worker；② `neural` 修图改为逐区域处理，缩小修补范围；③ 框内白底区域改用 `solid_fill`（**只限框内**） |
| YuzuMarker 模型缺失或识别失败 | 改成纯原图目视判断字体（按映射表、对照字库样张），继续往下做，报告中注明“该话无模型辅助” |
| `translate-raw` 某页失败 | ① 重试；② 改用 `mtui_run_translation_pipeline` 单页翻译；③ 用 `mtui_ocr_or_translate_regions` 对这一页逐区域 OCR 和翻译 |
| 区域修图不达标（接缝、残墨、糊斑） | 依次尝试不同方法：收紧 `tight_halo_rects` → 拆成多个小区域 → 换 `neural_inpainter` → 用 `mtui_manage_paste_overlays` 从相邻干净区域仿制修补 → 调整译文位置和白描边来遮瑕。选最好的一版 |
| 版面怎么调都不理想（溢出、断句别扭） | 依次尝试：重新断句 → 微调虚词 → 调字号、行距、字距 → 在不改原意的前提下换一种更短的说法 |
| 回归锁出现新增 error | 先分析是哪次修改引入的，针对性修复；修不好就 `mtui_snapshots(target_path="<PAGE>", action="restore", label="pre_gate_b")` 回退这一页，再用另一种方式重做这次修改 |
| 有歧义、需要判断的翻译 | 结合画面和上下文，选最贴切的译法**直接写入**；在 `DECISIONS` 里记下原文、所选译文、备选译文和理由 |
| 疑似漏检的文字（审计没报，但原图里有） | 直接用 `mtui_add_region` 补框，翻译并嵌字 |
| 磁盘 / 路径 / 权限错误 | 先尝试修正路径或改用可写位置；确实无法读写这一话时才跳过，处理下一话 |

**禁止的“省事”做法**：跳过 Gate B、少看 verify sheet、把 `UNRESOLVED` 写成已解决、为了让审计通过去删除正常区域或改审计结果、只记录不处理。

---

## 3. `run_log.md` 格式

```markdown
# <作品名> / <话名>
STATUS: DONE | DONE_WITH_ISSUES
pages: 42   regions: 318   verify_sheets_reviewed: 106/106
gate_a: errors 0 (initial 37), warnings 12 (handled 12)
regression_diff: newly_introduced_error_count 0
modified_pages: [3, 7, 13, 21]

## AUTO_FIXED（遇到问题并已自动解决）
- P013 #4 框外字压在网点渐变上，tight_halo 修补后有接缝 → 拆成 3 个小区域并调整白描边后通过 → verify_sheet_031.png
- worker 在 P020 显存不足崩溃 → 并发从 8 降到 4 后重启，后续正常

## DECISIONS（Agent 自主做出的判断）
- P021 #2 `お前が言うな` → 选用“轮不到你说”（理由：角色吐槽语气），备选“你还好意思说”

## UNRESOLVED（试过至少两种方案仍未解决）
- （无）
```

---

## 4. 分片与并发

### 4.1 分片数量
- **按页数自动计算**：`分片数 = ceil(总页数 / 12)`。每片最多 12 页，这是为了让一个子 agent 看完自己负责的全部 verify sheet 时，上下文还装得下。
- 例：一话 40 页 → 4 片；一卷 180 页 → 15 片。

### 4.2 同时运行的数量
- **默认上限 8 个**，可以按机器情况调整，用户指定时以用户为准。分片数超过上限时，排队执行，前一个完成就补下一个。
- **不设更高上限的原因**：实测确认所有子 agent **共用同一个 mtui-mcp 进程，也就共用同一个 GPU worker**，所以多开子 agent 不会多占显存。MCP 的同步工具在线程池里并发执行（上限 40 个线程），所以审计、读 JSON 这类非 GPU 调用可以并行；但修图、渲染、OCR、字体识别在 worker 里是**加锁排队执行**的。多开子 agent 能加快的是看图、改字这类 LLM 工作，**加快不了 GPU 工作**。开得太多时，GPU 队列变长，容易出现调用超时（默认 300 秒）；另外还会受到模型 API 的并发限制和额度消耗影响。
- **自动降档**：出现 worker 超时、显存不足或 API 限流时，主控把并发减半（最低 2），稳定后再逐步加回来。
- **显存与单 Worker 原则**：Stage 1 字体识别 + QA 扫描优先走 `mtui_qa_audit(force_yuzumarker=True, ...)`，Stage 5 光学居中优先走 `mtui_center_bubbles(target_path="<CHAPTER>")`，全部复用同一个常驻 MCP worker，零额外进程。若降级使用命令行 `aac translate-raw` / `aac detect-fonts` / `aac center-bubbles --apply-mcp`，只能由主控在同步点运行，并在运行前通过 `mtui_status(release_vram=True)` 释放常驻 worker 缓存显存。

### 4.3 分片规则
- 每个分片**只能修改自己负责的页**（每页有独立的 JSON 和图片文件，不同分片之间不会冲突）。
- 术语表、`.yuzumarker_font_cache.json`、居中、回归锁这些整章共享的内容只由主控处理。分片需要改术语表时，写进回报里交给主控。

### 4.4 分片提示词模板

```
你负责 <CHAPTER> 的第 <A>~<B> 页（只能修改这些页）。
遵循 mtui-manga-proofreading skill，当前阶段：<Stage 2~3 | Gate B>。
已有资料：<QA>/audit/（台账与 OCR 切片）、术语表 <glossary_yaml_or_txt>、YuzuMarker 缓存已生成。
强制前置动作（第一轮必须执行）：
- 宿主不会自动注入 reference 文件！开工第一轮必须并行调用 read_file 读取：
  1) mtui_mcp/skills/mtui-manga-proofreading/references/style_and_pitfalls.md（唯一 9 大字系中文字体映射表、断句/字号/修图细则）
  2) mtui_mcp/skills/mtui-manga-proofreading/references/mistranslation_checklist.md（OCR 误读与防错译清单）
要求：
- 逐框处理，零跳框；Gate B 阶段运行
  aac verify-crops --chapter-dir "<CHAPTER>" --out-dir "<QA>/verify_crops/shard_<A>_<B>" --page-start <A> --page-end <B>
  然后逐张 read_file 看完全部 sheet，一张不跳。
- Gate B 阶段若发现某单页需要修改，**先对该单页执行一次按需存档** `mtui_snapshots(target_path="<PAGE>", action="create", label="pre_gate_b")` 再动刀（严禁对没改动的页批量建快照浪费磁盘）。
- 发现问题就自己判断最优方案并直接修复、验证，按 unattended_batch_ops.md 第 2 节处理；不要停下来提问，也不要只记录不处理。
- 需要修改术语表时不要直接改，写进回报里交给主控。
- 编辑时可先 re_render=False，最后对自己负责的页调用一次 mtui_render_pages。不要运行全局 center-bubbles。
回报内容（JSON）：{"pages":[A,B], "modified_pages":[...], "sheets_reviewed":n, "sheets_total":n,
"auto_fixed":[...], "decisions":[...], "glossary_proposals":[...], "unresolved":[...]}
```

主控收到回报后，核对 `sheets_reviewed == sheets_total`。不相等就把漏看的 sheet 重新派回这个分片；`unresolved` 里的项，主控可以换一种思路再试一次。

---

## 5. 最终汇总报告模板（全部任务结束后输出一次）

```markdown
# 批量处理汇总（<开始时间> → <结束时间>）

| 话 | 状态 | 页 / 区域 | Gate A（初始 → 最终 error） | 回归 | 自动解决 | 自主判断 | 未解决 | 对比图 |
|---|---|---|---|---|---|---|---|---|
| 第1话 | ✅ DONE | 42 / 318 | 37 → 0 | 0 | 6 | 3 | 0 | [comparisons](file:///...) |

## 自动处理过的问题（摘要）
- 第1话：显存不足 1 次（已降并发恢复）；框外字修图返工 4 处（均已通过）……

## 自主判断的翻译 / 版面决定（供抽查）
- 第1话 P021 #2：`お前が言うな` → “轮不到你说”

## 未能解决的问题（附已尝试的方案）
- （无）

## 术语表变更
- 新增：……
```
