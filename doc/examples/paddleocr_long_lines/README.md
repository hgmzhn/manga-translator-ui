# PaddleOCR long-line regression / 长行识别回归

## Review fixes / 审查修复

Dynamic inputs are sorted by required width and grouped with at most 16 regions,
at most a 2:1 width ratio and a padded-width budget of 16 × 320. A line wider
than the budget runs alone, preserving its aspect ratio. Original indices map
recognition and color results back to the correct text regions. Fixed-width
models retain their original order and chunk size. Batch allocation fills one
output array sequentially instead of retaining all padded arrays for concatenation.

动态输入按所需宽度排序分组，每批最多 16 行，最长与最短宽度之比不超过 2，
补齐后的总宽度预算为 16 × 320。超过预算的单行独立推理，保持原始比例。
使用原始索引将文字与颜色结果写回对应区域。固定宽度模型保持原有顺序和批次大小。
批次逐行填入预分配数组，避免拼接时同时持有所有补齐副本。

For 15 short lines and one 9600-pixel line, the largest input tensor decreases
from 88.47 MB to 5.53 MB (float32, 3 channels, height 48). These numbers exclude
model activations and temporary preprocessing arrays.

15 条短行加 1 条 9600 像素长行时，最大输入张量从 88.47 MB 降至 5.53 MB。
数值按 float32、3 通道、高度 48 计算，不包括模型中间结果和预处理临时数组。

## Automated validation / 自动验证

```sh
python test/download_paddleocr_test_models.py --model-dir models/ocr
```

Set `PADDLEOCR_TEST_MODEL_DIR` to this directory, then run:

将环境变量 `PADDLEOCR_TEST_MODEL_DIR` 设置为该目录，然后运行：

```sh
python -m unittest discover -s test -p 'test_paddleocr*.py' -v
python test/reproduce_paddleocr_long_lines.py --model-dir models/ocr
```

The downloader uses URLs and SHA-256 hashes from the production model registry.
The dedicated PaddleOCR ONNX workflow runs these tests with all four released
recognizers: Latin, Korean, Thai and multilingual PP-OCRv6. With an explicit
model directory, missing models fail the tests instead of being skipped.
Ordinary offline runs skip individual models that are not installed.

下载器复用生产模型注册表中的 URL 和 SHA-256。专用工作流使用拉丁文、韩文、泰文及
多语种 PP-OCRv6 四个真实识别模型执行测试。显式指定模型目录后，缺少模型会失败；
普通离线运行仅跳过尚未安装的模型。

Local CPU validation passed all 14 tests: nine preprocessing/grouping cases,
four real-model shape/finite-output checks and one test checking all three
public dialogue images. The image test restores original order and normalizes
only the known curly/straight apostrophe difference. The synthetic short line
now runs in its own width group; its confidence is 0.9976. Other recorded text
and confidence results remain unchanged.

本地 CPU 验证 14 项全部通过：9 项预处理与分组测试、4 项真实模型输入输出验证、
1 项覆盖三张公开图片的文字断言。图片测试恢复原始顺序，仅归一化已知的弯/直撇号差异。
合成图短句现独立分组，置信度为 0.9976；其余已记录的文字及置信度结果保持一致。
