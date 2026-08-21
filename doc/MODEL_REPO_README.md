---
frameworks:
- ""
tasks: []
license: CC-BY-NC-4.0
---
# Manga Translator UI - 模型文件托管仓库

<div align="center">

[![主项目](https://img.shields.io/badge/%E4%B8%BB%E9%A1%B9%E7%9B%AE-manga--translator--ui-green)](https://github.com/hgmzhn/manga-translator-ui)
[![基于](https://img.shields.io/badge/%E5%9F%BA%E4%BA%8E-manga--image--translator-blue)](https://github.com/zyddnys/manga-image-translator)
[![模型](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B-Real--CUGAN-orange)](https://github.com/bilibili/ailab)
[![模型](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B-MangaJaNai-orange)](https://github.com/the-database/MangaJaNai)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR-blue)](https://github.com/PaddlePaddle/PaddleOCR)
[![OCR](https://img.shields.io/badge/OCR-MangaOCR-blue)](https://github.com/kha-white/manga-ocr)
[![OCR](https://img.shields.io/badge/OCR-PaddleOCR--VL--1.5-blue)](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5)
[![Hugging Face](https://img.shields.io/badge/HuggingFace-manga109--segmentation--bubble-yellow?logo=huggingface)](https://huggingface.co/huyvux3005/manga109-segmentation-bubble)
[![许可证](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-CC--BY--NC--4.0-red)](LICENSE)

</div>

## 📦 仓库说明

这是 [Manga Translator UI](https://github.com/hgmzhn/manga-translator-ui) 项目的**模型文件托管仓库**。

本仓库托管了漫画翻译软件运行所需的所有 AI 模型文件，包括：
- 文字检测模型
- OCR 识别模型
- 图像修复模型
- 图像超分辨率模型
- 图像上色模型
- 中文语义断句模型

## 🎯 使用说明

**用户无需手动下载本仓库的文件！**

当你运行 Manga Translator UI 软件时，程序会**自动检测缺失的模型**并从本仓库下载所需文件。

#

### 文字检测模型 (Detection)
- `detect-20241225.ckpt` - 默认文字检测器
- `comictextdetector.pt` / `comictextdetector.pt.onnx` - 漫画文字检测器
- `craft_mlt_25k.pth` / `craft_refiner_CTW1500.pth` - CRAFT 检测器
- `yolo26obb.onnx` - YOLO OBB 检测器

### 气泡检测模型 (Bubble Detection)
- `mangalens.pt` - MangaLens PyTorch 漫画气泡检测模型（原 `best.pt` 已更名）
- `mangalens.onnx` - MangaLens ONNX 漫画气泡检测模型

### OCR 识别模型
- `ocr.zip` - 32px OCR 模型
- `ocr_ar_48px.ckpt` + `alphabet-all-v7.txt` - 48px OCR 模型
- `ocr-ctc.zip` - CTC OCR 模型
- `manga_ocr_model.7z` - MangaOCR 模型（日文专用）
- `ch_PP-OCRv5_rec_server_infer.onnx` + `ppocrv5_dict.txt` - PaddleOCR 中文模型
- `korean_PP-OCRv5_rec_mobile_infer.onnx` + `ppocrv5_korean_dict.txt` - PaddleOCR 韩文模型
- `latin_PP-OCRv5_rec_mobile_infer.onnx` + `ppocrv5_latin_dict.txt` - PaddleOCR 拉丁文模型
- `PaddleOCR-VL-1.5.7z` → 解压为 `PaddleOCR-VL-1.5/`，对应官方 PaddleOCR-VL-1.5 模型

### 图像修复模型 (Inpainting)
- `inpainting.ckpt` - AOT 修复器
- `inpainting_lama_mpe.ckpt` - LAMA MPE 修复器
- `lama_large_512px.ckpt` - LAMA Large 修复器
- `lama_mpe_inpainting.onnx` - LAMA MPE ONNX 版本
- `lama_large_512px_inpainting.onnx` - LAMA Large ONNX 版本

#### FLUX.2 Klein 修复器

`flux2-klein/` 是 FLUX.2 Klein 4B 修复器的完整模型目录，必须保留以下 7 个文件：

```text
flux2-klein/
├─ model_index.json
├─ scheduler/scheduler_config.json
├─ transformer/config.json
├─ transformer/flux-2-klein-4b-Q4_K_M.gguf
├─ vae/config.json
├─ vae/diffusion_pytorch_model.safetensors
└─ flux2_inpaint_prompt.safetensors
```

整套文件约 **2.8 GB（约 2.6 GiB）**。其中 GGUF Transformer 约 2.60 GB，VAE 约 168 MB，提示词嵌入约 15 MB，其余为配置文件。程序首次选择 `flux2-klein` 时会下载到 `models/inpainting/flux2-klein/`。

模型来源：[FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)、[FLUX.2 Klein GGUF](https://huggingface.co/unsloth/FLUX.2-klein-4B-GGUF)、[FLUX2 修复提示词嵌入](https://huggingface.co/dreMaz/flux2-klein-inpaint)。使用和再分发时遵守各上游模型的许可证与条款。


### 图像超分辨率模型 (Upscaling)

#### Real-ESRGAN
- `4xESRGAN.pth` - 4倍超分模型
- `realesrgan-ncnn-vulkan` - NCNN 版本（Windows/macOS/Ubuntu）

#### Real-CUGAN (17 个模型)
- SE 系列：`up2x/3x/4x-latest-conservative/denoise1x/denoise2x/denoise3x/no-denoise.pth`
- PRO 系列：`pro-conservative/denoise3x/no-denoise-up2x/3x.pth`

#### MangaJaNai (17 个模型)
- MangaJaNai 2x 系列：`2x_MangaJaNai_1200p/1300p/1400p/1500p/1600p/1920p/2048p_V1_ESRGAN.pth`
- MangaJaNai 4x 系列：`4x_MangaJaNai_1200p/1300p/1400p/1500p/1600p/1920p/2048p_V1_ESRGAN.pth`
- IllustrationJaNai 系列：`2x/4x_IllustrationJaNai_V1_ESRGAN.pth`、`4x_IllustrationJaNai_V1_DAT2.pth`

#### Waifu2x
- `waifu2x-ncnn-vulkan` - NCNN 版本（Windows/macOS/Ubuntu）

### 图像上色模型 (Colorization)
- `manga-colorization-v2-generator.zip` - 上色生成器
- `manga-colorization-v2-net_rgb.pth` - RGB 网络

### 中文语义断句模型 (Chinese Semantic Line Break)
- `coarse_electra_small_20220616_012050.zip` - HanLP 中文粗粒度分词模型
- `ctb9_con_electra_small_20220215_230116.zip` - HanLP CTB9 成分句法分析模型

该功能用于中文译文的短语级自动断句，目前仅支持中文目标文本。程序会自动下载并解压到 `models/rendering/hanlp/`。

## 📊 统计信息

- **模型总数**：73 个文件（包含 FLUX.2 Klein 修复器的 7 个文件）
- **总大小**：约 5-8 GB（取决于选择的模型；FLUX.2 Klein 单套约 2.8 GB）
- **来源**：GitHub Release + HuggingFace + HanLP + ModelScope

## 🔗 相关链接

- **主项目地址**：https://github.com/hgmzhn/manga-translator-ui
- **原始项目**：https://github.com/zyddnys/manga-image-translator
- **问题反馈**：https://github.com/hgmzhn/manga-translator-ui/issues
- **ModelScope 模型仓库**：https://www.modelscope.cn/models/hgmzhn/manga-translator-ui
- **PaddleOCR-VL-1.5 官方模型页**：https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5
- **PaddleOCR-VL 官方文档**：https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html

## 📝 模型来源与协议

本仓库的模型文件来自以下开源项目，**各模型遵守其原始项目的开源协议**：

- [manga-image-translator](https://github.com/zyddnys/manga-image-translator) - 主要模型来源
- [manga-ocr](https://github.com/kha-white/manga-ocr) - 日文 OCR 模型
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - 多语言 OCR 模型
- [PaddlePaddle/PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) - 官方 PaddleOCR-VL-1.5 模型
- [manga109-segmentation-bubble](https://huggingface.co/huyvux3005/manga109-segmentation-bubble) - 漫画气泡检测模型
- [HanLP](https://github.com/hankcs/HanLP) - 中文分词与成分句法分析模型
- [Real-CUGAN](https://github.com/bilibili/ailab) - B站 AI Lab 超分模型
- [MangaJaNai](https://github.com/the-database/MangaJaNai) - 漫画专用超分模型 **(CC BY-NC 4.0，仅限非商业用途)**
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) - 通用超分模型
- [waifu2x](https://github.com/nihui/waifu2x-ncnn-vulkan) - 动漫图像超分模型

## ⚠️ 免责声明与使用限制

本仓库仅用于模型文件分发与技术学习交流，不构成任何法律、商业或合规建议。  
使用者在下载、部署、调用、再分发本仓库模型文件时，应自行确认并持续遵守所在地法律法规、平台规则、数据来源许可及第三方模型协议。

### 免责与责任限制

- 模型文件的实际授权范围、商用限制、署名要求、衍生分发要求，以各上游项目/模型发布页的原始协议为准。
- 使用者应自行确保输入数据、处理流程与输出内容具备合法授权，不得用于侵犯著作权、隐私权、肖像权、商标权等合法权益的场景。
- 严禁将本仓库模型用于任何违法违规用途，包括但不限于盗版传播、未授权批量抓取、绕过平台限制、诈骗、诽谤等行为。
- 对于因使用或无法使用本仓库模型文件导致的任何直接或间接损失（含数据损失、业务中断、收益损失、第三方索赔等），仓库维护者与贡献者在适用法律允许范围内不承担责任。
- 若你将模型用于团队或组织环境，应自行完成权限控制、日志审计、内容审核与合规评估，并建立必要的人审流程。

继续使用本仓库即视为你已阅读、理解并同意上述条款。

## 🙏 致谢

感谢所有开源项目的作者和贡献者，让这个项目得以实现！

- [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) - 核心翻译引擎
- [bilibili/ailab](https://github.com/bilibili/ailab) - Real-CUGAN 超分辨率模型
- [the-database/MangaJaNai](https://github.com/the-database/MangaJaNai) - MangaJaNai/IllustrationJaNai 超分辨率模型
- [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) - PaddleOCR 模型支持
- [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) - MangaOCR 模型支持
- [PaddlePaddle/PaddleOCR-VL-1.5](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5) - 提供 PaddleOCR-VL-1.5 模型支持
- [huyvux3005/manga109-segmentation-bubble](https://huggingface.co/huyvux3005/manga109-segmentation-bubble) - 漫画气泡检测模型支持
- [hankcs/HanLP](https://github.com/hankcs/HanLP) - 中文语义断句模型支持
- [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) - Real-ESRGAN 超分模型
- [nihui/waifu2x-ncnn-vulkan](https://github.com/nihui/waifu2x-ncnn-vulkan) - Waifu2x 超分模型

---

**最后更新时间**：2026-08-22
