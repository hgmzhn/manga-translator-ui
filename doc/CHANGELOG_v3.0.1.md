# v3.0.1 更新日志

发布日期：2026-08-15

## 🐛 修复与改进

### Windows AMD ROCm 依赖检测修复
- 修复 Windows AMD ROCm PyTorch 已正常安装时，安装/更新程序仍提示“未检测到可用的 PyTorch”并重新进入依赖方案选择的问题。
- 兼容 PyTorch 检测结果中的 `AMD` 与 `ROCm` 两种类型标识，并统一映射到 `rocm7.2.1` 依赖方案。
- 修复后，安装器可正确识别类似 `ROCm 7.2.53211-158bd99533` 的运行时版本，避免误选 CPU、CUDA 或重复安装依赖。

### 文件夹修改时间排序修复
- 修复编辑器打开文件夹并按修改时间排序时，使用格式化日期字符串比较导致顺序错误的问题。
- 修改时间现按文件系统原始时间戳排序，例如 8 月 13 日会正确排在 8 月 2 日之后。

### curl-cffi 网络传输升级
- 将 `curl-cffi` 从 `0.15.0` 升级并固定到 `0.16.0`，同步使用新版 curl 8.21 与 curl-impersonate 2.0 的修复和改进。
- OpenAI、Gemini、OCR、AI 渲染、AI 上色与 API 测试统一改用 `impersonate="chrome"`，自动跟随当前 Chrome 指纹，不再固定旧版 Chrome 110 指纹。
- 移除与 TLS 指纹版本不一致的手写 `User-Agent`、`Sec-CH-UA` 等浏览器身份请求头，避免请求头声明与底层 TLS/HTTP2 指纹冲突。
- curl-cffi 会话统一关闭环境变量代理自动读取，系统代理仅由应用内“使用系统代理”开关按请求地址解析，避免开关关闭后仍意外使用 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。

### API 密钥异常字符提示修复
- 修复 API 密钥混入中文、全角符号或不可见字符时，API 测试和正式请求直接显示 `latin-1 codec can't encode characters` 底层异常的问题。
- OpenAI 兼容接口与 Gemini 请求现在会在创建网络连接前检查密钥，并提示异常字符在密钥中的位置；“测试”和“获取模型”均返回本地化说明。
