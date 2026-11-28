# 字体映射、版面规则、修图协议与非正文范围手册

> 本文件是**字体映射表的唯一来源**。SKILL.md 只引用这里，不再重复抄写。具体作品的实例见 [case_studies.md](case_studies.md)。

---

## 1. YuzuMarker 4x 字体识别与中文字库映射

### 1.1 识别原理（了解即可，Agent 无需实现）
- `engine_worker.py`（`action='detect_fonts'`）加载 `MTUI_ROOT/models/yuzumarker/` 下的 `4x-epoch=18` 和 `4x-epoch=84` 两个 ResNet-50 权重，以及 `font-labels-ex.json`（6,150 款日文商用字库），对每个文本框做 `bbox ± (3%, 8%)` 双尺度推理，并聚合成 9 大字系的概率，写入 `manga_translator_work/.yuzumarker_font_cache.json`。
- 扫描完自动释放显存。`≤2` 字的短框会参考相邻框结果。
- `bold_prob` 已经包含 `heavy_prob`，判断时**不要重复相加**。

### 1.2 判定原则
1. **只看原图**：原字体相同，译文字体就保持一致；原字体变了，就跟着换。不要按“角色/分镜/口吻”去猜，同一角色在普通对白、内心独白、威压台词、抓狂吐槽之间会频繁换字体。
2. **置信度分级（严格互斥判定顺序，自上而下匹配第一条即生效）**：
   - **第一步（前置例外优先，不触发高置信锁定）**：只要命中以下任一条件——① 带 `[NARROW-KANA-STRETCH]` 或 `[THIN-STEM]` 标记（见第 5 条）；② `n_clean ≤ 2` 且无相邻框传播（`neighbor_propagated_family` 为空）；③ **`top1 < 0.68`**（即使 `pred_conf ≥ 0.90`，也说明字系内部多款字库分票或字形模糊）；④ `KAISHO_BRUSH` 但 Top-1 非核心验证楷书（如 `HOT-FKaisho` / `DFSoKaiSho`）——**一律不锁定**，必须先 1:1 对照原图和相邻同款气泡笔画后再决定。
   - **第二步（高置信锁定）**：仅在**未命中第一步任何例外**，且同时满足 **`pred_conf ≥ 0.90` 与 `top1 ≥ 0.68`** 时，锁定映射结果直接采用（推翻前必须先用 `mtui_inspect_region_visual` 查看单框 1:1 原图并给出明确笔画证据）。
   - **第三步（中高置信与低置信）**：未命中第一步且 `0.84 ≤ pred_conf < 0.90`（`top1 ≥ 0.68`）时一般采纳，但若与同页同款原字冲突，以 1:1 原图结论为准；`pred_conf < 0.84` 时必须对照原图和相邻气泡后再决定。
3. **粗体零漏检，同时防过粗误加**：原图笔画明显粗重，或**非 `ANTIQUE` 字系**且高置信度 `bold_prob ≥ 0.90`（即使两个模型在两类粗体之间分票），就**不能**留在 `FZHei-B01` + `bold=False`（换用原生粗字重字库，或改成 `FZHei-B01` + `bold=True`）。但有两条硬边界：① **`pred_family == ANTIQUE` 例外**：常规对白字库（`AnticCezannePro-DB`、`ShueiAntiStd-B`）因文件名带 `-DB`/`-B`，`bold_prob` 常达 `0.89~1.00`，严禁仅凭 `bold_prob` 盲目加粗，必须核对原图笔画确实显著粗于同页常规基线；② **`n_clean ≤ 2` 短框例外**（如 `っ!?`、`──っ!?`）：小字短框的 YuzuMarker 预测极易噪声漂移，无同页大字粗笔或 `raw_sw > 1.18x` 常规基线佐证时**禁止加粗**；确系粗体的小字（`fs ≤ 22px`）优先用原生 **`Source Han Sans SC::Medium`** / **`Source Han Sans SC::Bold`**（`bold=False`），避免 `FZHei-B01` 假粗在小字号下糊成一团。
4. **原生重字重与北魏楷书不叠假粗**：`FZCuSong-B09`、`FZLanTingYuan-B-GBK`、`FZJianZhi-M23`、`Arphic Tankuin Bold GB`、`DFZongYiW7-GB`、`DFFangYuanW7-GB`、`ZHSRXT2011-GB`、`XinYuGongCangSangTi`、`FZSongHei-B07`，所有 `::Bold` / `::Heavy` 选择器，以及 **`FZBeiWeiKaiShu-S19`**（刀刻碑楷笔画棱角密，叠 Qt 合成假粗会发胖堵死字怀），一律 `bold=False`（原生重字重叠假粗报 `inherently_heavy_font_bolded` error，`FZBeiWeiKaiShu-S19` 叠假粗报 warning；此外 `FZZhunYuan-M02 + bold=True` 报 `unjustified_faux_bold` error）。
5. **窄条纯假名拉伸与薄竖笔防误判粗宋**：YuzuMarker 4x 推理遵循上游训练时的正方形拉伸（`_crop_exact_data_py` 不改 padding），极端细长纯假名短框（`n_clean ≤ 3`、长宽比 `≥ 3.5`、短边 `≤ 44px` 归一化尺度）或薄竖笔气泡（实测竖笔半宽 `stem_hw < 2.45px` 归一化尺度）容易被误判为 `FZCuSong-B09` 或高置信异字系。遇到带 `[NARROW-KANA-STRETCH]` / `[THIN-STEM]` 标记的自检卡或降级 `warning` 时不锁定 `≥0.90`，须 1:1 核对原图：常规黑体/アンチック假名保持 `FZHei-B01`，仅原图确系明朝才改 `FZYaSong-DB-GBK`（确认真粗明朝才用 `FZCuSong-B09`）。

### 1.3 映射表

| 字系 | 日版常见字体（YuzuMarker Top-1） | MTUI 字体 + `bold` | 典型场景与防误判 |
| :--- | :--- | :--- | :--- |
| **`ANTIQUE`** 常规对白 | `FOT-AnticCezannePro-DB`、`G-OTF-KAntiqueStd-ANM`、`FOT-DNPShueiAntiStd-B`、`FOT-TsukuAntique*` | 常规：**`FZHei-B01`（`False`）** | 汉字带明朝衬线，假名是等粗黑体。常规对白在假名多或短句时常被误识成 `TsukuARdGothic` / `DFEnKai` / `UDDigiKyokasho` / `HOT-FKaishoStd-B` / `DFSoKaiSho-W7`，普通对白仍然用 `FZHei-B01`（`False`），不要改成楷书、宋体或误加粗 |
| **`ROUND_GOTHIC`** 圆体 / 无衬线独白 | `FOT-Seurat*`、`FOT-RodinHappyPro-M/-B/-EB`、`A-OTF-GothicBBBPro-Medium` | 旁白 / 内心独白 / 轻声（`-M`、`Medium`）：**`FZZhunYuan-M02`（`False`）**<br>粗圆元气呐喊（`RodinHappy-B/-EB`、`SeuratCapie-B`、`Seurat-EB`）：**`FZLanTingYuan-B-GBK`（`False`）** | 严禁用 `FZZhunYuan-M02` 加假粗（`bold=True` 会触发 `unjustified_faux_bold` error）来冒充粗圆体。`GothicBBB-Medium` 是纯无衬线黑体，不要因为台词古风就改成宋体 |
| **`MINCHO`** 明朝 | `A-OTF-Ryumin*`、`FOT-DNPShueiShogoMinStd-Hv`、`FOT-TsukuMin*`、`DFLeiGaSoStd-W7`、`FOT-Matisse*` | 纤细独白（`Ryumin-Medium`，`bold_prob<0.25`）：**`FZYaSong-M-GBK`（`False`）**<br>常规中粗（`TsukuMin-B`、`Matisse-B`）：**`FZYaSong-DB-GBK`（`False`）**<br>特太 / 重墨（`Ryumin-Ultra/ExHeavy`、`ShueiShogoMin-Hv`、`LeiGaSo-W7`）：**`FZCuSong-B09`（`False`）** | 汉字和假名都是竖粗横细，带针尖起笔。特太明朝不要用偏细的中雅宋 |
| **`GUYIN`** 古印体 | `FOT-ManyoKoin*`、`HOT-FKointaiStd-B` | **`Arphic Tankuin Bold GB`（`False`）** | 笔画中段有虫蚀状断口。同一格内 1~2 字的短框（如 `ぐ…`、`ガッ`）要跟同格长框一起换 |
| **`KAISHO_BRUSH`** 重毛笔楷书 | `A-OTF-KaishoMCBK1Pro-DeBold`、`TT-JTCナミキ特太楷書`、`HOT-GFKaishoStd-E` | **`FZBeiWeiKaiShu-S19`（`False`）**；枯笔飞白的杀意特写：`XinYuGongCangSangTi`（`False`） | 有压迫感的低吼、威胁，不要漏留在 `FZHei-B01`，也**不要对 `FZBeiWeiKaiShu-S19` 开 `bold=True`**（靠原字骨力与合理字号体现分量）。遇到 `HOT-FKaisho` / `DFSoKaiSho` / 低置信 `DFKKaiShoC-W7` 须先对照同页常规气泡排除 `ANTIQUE` 误判 |
| **`REISHO`** 隶书 | `A-OTF-Reisho101Std-Medium`、`A-OTF-LikureiStd-Regular`、`DCHoLeiShoStd-W3`、`FOT-HoureiStd-EB` | `Reisho101` / `Likurei`：**`LiSu`（`False`）**<br>特粗 `HoureiStd-EB`：**`LiSu`（`True`）** | 蚕头燕尾、扁方结构，常见于神谕、咏唱、一本正经的冷面吐槽。竖排双标点写 ASCII `!?` / `!!`，走 `tcy`，避免 `⁉` 缺字 |
| **`POP_CHISEL`** 刀切楔形 | `FOT-ComicReggaeStd-B`、`FOT-ReggaeStd-B`、`A-OTF-TakeStd-Bold`、`DFSoGeiStd-W5/W7` | **`FZJianZhi-M23`（`False`）**；直角方黑/综艺体 `DFSoGei`：**`ZHSRXT2011-GB`（`False`，`张海山锐谐体.ttf`）** | 常见于震惊、质问、炸毛吐槽。`FZJianZhi-M23` 与 `ZHSRXT2011-GB` 一律 `bold=False`（禁止叠假粗） |
| **`KYOKASHO_PEN`** 教科书体 / 波浪 / 细竹签 | `A-OTF-KyokaICAPro-*`、`G-OTF-G*KyokaICA*`、`G-OTF-GHitujyun*`（森泽教科书体 / 笔顺体）、`FOT-ComicMysteryStd-DB`、`A-OTF-TakeStd-Light` | 教科书体 / 温柔告白 / 书信独白（`KyokaICA` / `Hitujyun`）：默认 **`LXGW WenKai::Medium`（`False`）**，明确 `-Regular`/`-Light`/`-L` 纤细体用 **`LXGW WenKai`（`False`）**<br>`ComicMystery`（惊悚、威吓、波浪笔触）：**`LXGW WenKai::Medium`（`True`）**<br>`TakeStd-Light`（极细、脱力、泪崩）：**`Xiaolai`（`False`）** | `KyokaICA` / `Hitujyun` 是日文手写楷笔教科书体，不要误划入圆体 `FZZhunYuan-M02`；若 Top-3 同时混有 `UDDigiKyokasho` 且教科书体占比 `<0.70`，先 1:1 核验是否为常规 `ANTIQUE`。粗的 `Take-Bold` 走 `POP_CHISEL`，细的 `Take-Light` 走这里 |
| **`KAKU_GOTHIC`** 粗黑 / 重黑 | `A-OTF-KakuminPro-Bold`、`A-OTF-ShinGo*-Bold/Heavy`、`A-OTF-UDShinGo*-Bold`、`RoBrushStd-UB` | `KakuminPro-Bold` 重音强调（`fs ≥ 22px`）：**`FZHei-B01`（`True`）**<br>`ShinGo-Bold/Heavy` 坚定 / 咆哮：**`Source Han Sans SC::Bold` / `::Heavy`（`False`）**<br>确系粗体的极小字（`fs ≤ 22px`）：优先原生 **`Source Han Sans SC::Medium` / `::Bold`（`False`）** | 不要漏留在 `FZHei-B01::Regular`（对应 `yuzumarker_bold_mismatch`），不要对 `::Bold`/`::Heavy` 再叠 `bold=True`，也不要给未证实粗笔画的 `≤2` 字小泡硬开 `FZHei-B01 + bold=True` |

> 本地字库样张：[local_font_specimens.png](local_font_specimens.png)。样张里还有 `HYJinKaiJ`（冷峻劲楷）、`HuiwenMincho-Plus`（怪谈老铅字明朝）、`DFPRareBook-Willow`（古籍体）、`Resource Han Rounded CN` 等备选字库。映射表没有覆盖、但原图风格明显相符时，可以对照样张选用，并在 `run_log.md` 里写明理由。

---

## 2. 版面规则

### 2.1 断句：「在符合自然气口的前提下尽量贴合气泡框体」
核心是权衡，不是套公式。要结合句式和气泡形状（椭圆、正圆、竖长、宽扁、上宽下窄）来取舍：

1. **大语法停顿会让列长严重失衡时，退到次级气口**，把过短的成分和相邻成分连读：
   - ❌ `当上 / 竞争率超高的雄性的 / 老婆!!` `[2,9,3]` → ✅ `当上竞争率 / 超高的雄性的 / 老婆!!` `[5,6,3]`
   - ❌ `… / 这回 / 要不要去﹃下层﹄？` → ✅ `那个︙ / 如果方便的话︙ / 下次还能见面的话 / 这回要不要 / 去﹃下层﹄？` `[3,7,8,5,5]`
2. **不要强套“拱形”模板**：`[6,5,6]`、`[6,6,4]`、句首带停顿的 `[3,7,8,5,5]` 都是协调的。为了凑形状把双音节词劈开，得不偿失。
3. 实在切不顺时，可以在不改意思和口吻的前提下，**增删 1~2 个虚词**（的 / 了 / 那）。
4. **两条底线**：
   - 气口：不拆双音节词和专有名词；`了 / 着 / 过 / 的 / 得 / 们` 不放在行首。
   - 框体：不要单字孤尾列（`[3,10,1]`、`[5,5,1]`、`[6,1]`），不要单列突刺（`[2,9,3]`、`[4,9,4]`、`[2,8]`），不要中间凹坑（`[3,7,8,2,9]`），不要倒三角（`[3,5,7]`），宽圆泡里不要只排 1~2 列细长条。
5. 单室椭圆对白泡优先排 **2~3 列均衡**，不要拆成 4 列碎句再配 20~24px 的小字。

### 2.2 字号占空比
- 常规多列对白占气室内高 **62%~75%**（通常 28~34px）。原图字太小影响阅读时，可以适当放大。
- **1~3 字的短吼、感叹**（如 `ていっ → 喝！`、`住手!!`）：按气泡内径放大到 **55%~65%**，不要保留 30~36px，让小字悬在大白泡中间。
- 初翻把双列呐喊误识为单列、给了超大字号（如 `fs=119`）时，改成双列后要按容器宽高重新下调。
- 对应检查码：`bubble_underfilled_font`、`raw_font_size_mismatch`、`text_overflows_bubble`。

### 2.3 连体多室泡
- 轮廓上有向内凹的分室尖角（cusp）、内刺或缩颈，就是多室泡。阶梯错位双室、左右并排的 `OO` 双圆、带内刺的双爆炸框都算。**1 室 1 框，禁止合并**，字体字重统一（`connected_bubble_font_mismatch == 0`），字块不越过缩颈（`rendered_regions_overlap == 0`）。
- 跨室断句落在原文两室的语法分界上。不要把下室句首的副词（如 `往往…`）塞到上室末尾，再凭空加省略号（`cross_lobe_dangling_clause == 0`）。

### 2.4 竖排符号与对齐
- 竖排只用竖排码位：`││ ︙ ︻︼ ︵︶ ﹁﹂ ﹃﹄ ︽︾ ︿﹀ ︴`。
- **防止二次旋转**：初翻的 `rich_text_rules.yaml` 常给横排 `～` 加 `transform.rotation: -90`。换成 `︴` 后要去掉 `rotation`（`richtext_builder.py` / `project_store.py` 已经会自动清理，复核时确认一下即可）。对应 `unrotated_vertical_symbol`、`double_rotated_vertical_symbol`。
- 竖排双标点 `!! !? ?! ??` 写半角 ASCII，`project_store.py` 会自动生成 `tcy`。
- **`alignment`**：竖排对白用 `"left"`（每列首字齐顶，对应 `config.json` 里的 `"alignment": "left"`）或 `"auto"`。只有明确居中排版的横排标题或装饰字才用 `"center"`。

---

## 3. 修图协议

### 3.1 框内（封闭白泡 / 方框）
- **保护黑框**：`apply_mask_operations` 默认开启 `protect_bubble_borders=True`。初翻已经擦断的弧线，按审计给出的 `suggested_restore_rect_xyxy` 传 `erase_rects` 还原（对应 `bubble_border_eroded`）。
- **清除残墨**：横排方框句首超出检测框的 `――`、竖排傍点 `・・・`、小振假名，用避开框线的内缩矩形加 `solid_fill` 抹白（对应 `inpaint_residual_ink`、`inpaint_gray_smudge`）。
- 框外周边的人物、服饰、道具、背景、网点、速度线、分镜线都不能误伤。

### 3.2 框外无框字（修图重灾区）
文字直接压在原画上，笔画下面的像素必须由模型重建，做不到物理上零改动。目标是**把破坏面积和违和感降到最小**：
1. **紧贴文字的遮罩**：`mtui_edit_mask_and_inpaint(..., tight_halo_rects=[[x1,y1,x2,y2]], inpaint_method="neural")`，只覆盖字的墨芯加描边光晕（外扩 +2~3px）。不要传宽大的 `add_rects`。个别漏擦的振假名或标点，只做极小范围的点补。
2. **纹理接续**：框外一律用 `neural`（`lama_large`），禁止用 `solid_fill` 或 `telea`。
3. **嵌字遮瑕**：译文尽量顺着原文的轨迹排布，配 `stroke_width=0.22~0.28` 的白描边，自然盖住修补接缝。
4. **验收**：重点看 `verify-crops` 三联图的中间栏 `INPAINTED`。已删除的区域要确认已经 `restore_artwork`（对应 `[ORPHAN-MASK]`、`frameless_overmasked_artwork`、`frameless_artwork_scar`）。

---

## 4. 非正文与拟声词范围

| 类型 | 处理 | 检查码 |
|---|---|---|
| 封面 / 彩页扉页 / 封底署名（书名 Logo、卷号、`原作：…`、`漫画：…`、`©`） | 不翻译，`mtui_delete_regions(restore_artwork=True)` | `cover_credit_translation` |
| 正文页天头 / 地脚跨页重复的书名刊头（如 `《作品名》 [卷号]`） | 不翻译，删除并还原原画 | `running_header_book_title` |
| 目录页 | 英文装饰标题 `CONTENTS` 保留，不要在译文框里重复写入；按原图每行建独立的左对齐框（`alignment="left"`），不要合并居中；每行的正字、振假名、引导点、右侧页码都要紧贴遮罩后用 `neural` 修净（引擎默认 `inpainting_size=2048`） | `toc_alignment_or_merge_error`、`toc_unmasked_raw_text` |
| 下卷预告 / 宣传页 | 只翻分镜格内封闭气泡里的台词，框外宣传语删除并还原 | `preview_ad_out_of_bubble_translation` |
| 独立封闭白泡 / 留白方框内的叙事拟声词 | 必须翻译，字体跟随 YuzuMarker 字系 | `untranslated_boxed_sfx` |
| 跨网点、速度线的手绘大音效字（描き文字） | 保留原画 | — |

---

## 5. 缺字（零轮廓伪码位）与引号
- `文鼎古印体.ttf`（`Arphic Tankuin Bold GB`）里的 `﹁﹂︻︼︵︶︽︾︿﹀︴︱`，以及 `SoukouMincho-SC.ttf` 里的 `—·‼⁉`，虽然在 `cmap` 里有，但字形轮廓数为 0。`方正粗宋_GBK.ttf` 和 `方正兰亭圆_GBK_粗.ttf` 缺 `︙` / `・`。
- `font_catalog.py` 会通过 `loca` 拦截这类伪码位，`project_store.py` 保存时会为缺失的标点自动注入同风格字库的内联 `fontFamily` 回退。复核时确认 `missing_font_glyphs == 0` 即可。
- 引号要忠实于原图：单引号 `「…」` 对应竖排 `﹁…﹂`，双空心框 `『…』` 对应 `﹃…﹄`。
