# v3.0.4 更新日志

发布日期：2026-08-29

## 🐛 修复与改进

### Renderer、Colorizer 与 OCR 错误分类
- 修复在线 Renderer、Colorizer 和 OCR 请求失败时被归类为通用 API、模型或多模态错误的问题。
- 根据实际生产日志识别候选耗尽、模型不支持和图片输出能力不兼容等错误，保留对应功能的专用提示。
- 支持 `exhausting 1 API candidate`、`exhausting 1 API candidate(s)` 和 `no available API candidates` 等候选耗尽格式。

### OCR 错误提示
- 新增 OCR 专用错误文案，区分本地 OCR 与在线 OCR 的处理建议。
- 使用本地 OCR 时提示不要选择 `OpenAI OCR` 或 `Gemini OCR`；使用在线 OCR 时提示检查 API 密钥、地址、通道和模型配置。

### 多语言错误文案
- 同步更新简体中文、繁体中文、英文、日文、韩文和西班牙文的 Renderer、Colorizer、多模态及 OCR 错误提示。
- 在线选项文案只提示不要选择对应在线功能，不固定指定某个本地模型，避免后续新增本地模型后提示失效。

### 跨区域文字描边
- 修复相邻或重叠文本区域中，后绘制文字的字体描边覆盖前面文字正文的问题。
- 将特效层、描边层和正文层分阶段绘制，确保所有正文位于描边之上。
