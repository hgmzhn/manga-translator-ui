# import math
import os
import re
import shutil
from typing import List

import cv2
import einops
import numpy as np
import torch
from PIL import Image

# 在导入 transformers 之前配置 HuggingFace 镜像
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
os.environ.setdefault('HF_HUB_ENDPOINT', 'https://hf-mirror.com')

# 禁用 SSL 验证（解决 hf-mirror.com 证书问题）
import ssl

# import urllib.request
ssl._create_default_https_context = ssl._create_unverified_context
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['HF_HUB_DISABLE_SSL_VERIFY'] = '1'

# 直接导入 transformers 组件，不依赖 manga_ocr 库
from transformers import AutoTokenizer, VisionEncoderDecoderModel, ViTImageProcessor

from ..config import OcrConfig
from ..utils import Quadrilateral, TextBlock, chunks, imwrite_unicode, open_pil_image
from ..utils.generic import AvgMeter
from .common import OfflineOCR
from .model_48px import OCR

# ============ 内置 MangaOCR 功能（不依赖 manga_ocr 库）============

class InternalMangaOcr:
    """
    内置的 MangaOCR 实现，不依赖外部 manga_ocr 库
    基于 transformers 的 VisionEncoderDecoderModel
    """
    def __init__(self, pretrained_model_name_or_path="kha-white/manga-ocr-base", device="cpu", logger=None):
        self.logger = logger

        # 加载模型组件
        self.processor = ViTImageProcessor.from_pretrained(pretrained_model_name_or_path)
        self.tokenizer = AutoTokenizer.from_pretrained(pretrained_model_name_or_path)
        self.model = VisionEncoderDecoderModel.from_pretrained(pretrained_model_name_or_path)
        
        # 移动到指定设备
        self.device = device
        self.torch_device = device
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        self.model.to(self.torch_device)
        self.model.eval()
    
    def __call__(self, img_or_path):
        """
        识别图像中的文本
        
        Args:
            img_or_path: PIL.Image 或图像路径
            
        Returns:
            str: 识别的文本
        """
        if isinstance(img_or_path, str):
            img = open_pil_image(img_or_path, eager=False)
        elif isinstance(img_or_path, Image.Image):
            img = img_or_path
        else:
            raise ValueError(f"img_or_path 必须是路径或 PIL.Image，得到: {type(img_or_path)}")
        
        # 转换为灰度再转回 RGB（manga_ocr 的预处理方式）
        img = img.convert("L").convert("RGB")
        
        # 预处理
        pixel_values = self.processor(img, return_tensors="pt").pixel_values
        pixel_values = pixel_values.to(self.torch_device)
        
        # 生成文本
        with torch.no_grad():
            generated_ids = self.model.generate(pixel_values, max_length=300)[0].cpu()
        
        # 解码
        text = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
        
        # 后处理
        text = self._post_process(text)
        
        return text
    
    def _post_process(self, text):
        """后处理识别的文本"""
        # 移除所有空格
        text = "".join(text.split())
        
        # 替换省略号
        text = text.replace("…", "...")
        
        # 处理连续的点
        text = re.sub("[・.]{2,}", lambda x: (x.end() - x.start()) * ".", text)
        
        # 半角转全角（ASCII 和数字）
        try:
            import jaconv
            text = jaconv.h2z(text, ascii=True, digit=True)
        except ImportError:
            # 如果没有 jaconv，使用简单的转换
            pass
        
        return text

class ModelMangaOCR(OfflineOCR):
    _MODEL_MAPPING = {
        'model': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/ocr_ar_48px.ckpt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/ocr_ar_48px.ckpt',
            ],
            'hash': '29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d',
        },
        'dict': {
            'url': [
                'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/alphabet-all-v7.txt',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/alphabet-all-v7.txt',
            ],
            'hash': 'f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a',
        },
        # MangaOCR 模型文件（从 GitHub Release 下载）
        'manga_ocr_model': {
            'url': [
                'https://github.com/hgmzhn/manga-translator-ui/releases/download/v1.9.5/manga_ocr_model.7z',
                'https://www.modelscope.cn/models/hgmzhn/manga-translator-ui/resolve/master/manga_ocr_model.7z',
            ],
            'hash': '5dc27bde275ad981818a06de92a77da18383c0db9b915d6faff2b8acbfe35475',
            'archive': {
                'manga_ocr_model/config.json': 'manga_ocr/config.json',
                'manga_ocr_model/preprocessor_config.json': 'manga_ocr/preprocessor_config.json',
                'manga_ocr_model/pytorch_model.bin': 'manga_ocr/pytorch_model.bin',
                'manga_ocr_model/special_tokens_map.json': 'manga_ocr/special_tokens_map.json',
                'manga_ocr_model/tokenizer_config.json': 'manga_ocr/tokenizer_config.json',
                'manga_ocr_model/vocab.txt': 'manga_ocr/vocab.txt',
            },
        },
    }

    def __init__(self, *args, **kwargs):
        os.makedirs(self.model_dir, exist_ok=True)
        if os.path.exists('ocr_ar_48px.ckpt'):
            shutil.move('ocr_ar_48px.ckpt', self._get_file_path('ocr_ar_48px.ckpt'))
        if os.path.exists('alphabet-all-v7.txt'):
            shutil.move('alphabet-all-v7.txt', self._get_file_path('alphabet-all-v7.txt'))
        
        super().__init__(*args, **kwargs)

    async def _load(self, device: str):
        with open(self._get_file_path('alphabet-all-v7.txt'), 'r', encoding = 'utf-8') as fp:
            dictionary = [s[:-1] for s in fp.readlines()]

        self.model = OCR(dictionary, 768)
        
        # 使用内置的 MangaOCR 实现（不依赖 manga_ocr 库）
        local_manga_ocr_path = os.path.join(self.model_dir, 'manga_ocr')
        model_path = None
        
        # 1. 优先使用本地下载的模型
        if os.path.exists(local_manga_ocr_path) and os.path.exists(os.path.join(local_manga_ocr_path, 'config.json')):
            model_path = local_manga_ocr_path
        else:
            # 2. 兼容旧版本：查找 HuggingFace 缓存
            hf_cache_dir = os.path.expanduser('~/.cache/huggingface/hub')
            if os.path.exists(hf_cache_dir):
                for item in os.listdir(hf_cache_dir):
                    if 'manga-ocr-base' in item.lower():
                        snapshot_dir = os.path.join(hf_cache_dir, item, 'snapshots')
                        if os.path.exists(snapshot_dir):
                            snapshots = os.listdir(snapshot_dir)
                            if snapshots:
                                hf_model_path = os.path.join(snapshot_dir, snapshots[0])
                                if os.path.exists(os.path.join(hf_model_path, 'config.json')):
                                    model_path = hf_model_path
                                    break
        
        # 3. 如果都没找到，使用在线模型
        if model_path is None:
            model_path = "kha-white/manga-ocr-base"
        
        # 使用内置实现
        manga_ocr_device = device if device in ['cuda', 'mps', 'dml'] else 'cpu'
        self.mocr = InternalMangaOcr(
            pretrained_model_name_or_path=model_path,
            device=manga_ocr_device,
            logger=self.logger
        )
        
        sd = torch.load(self._get_file_path('ocr_ar_48px.ckpt'))
        self.model.load_state_dict(sd)
        self.model.eval()
        self.device = device
        self.torch_device = device
        if device == 'dml':
            import torch_directml
            self.torch_device = torch_directml.device()
        if (device == 'cuda' or device == 'mps' or device == 'dml'):
            self.use_gpu = True
        else:
            self.use_gpu = False
        if self.use_gpu:
            self.model = self.model.to(self.torch_device)


    async def _unload(self):
        if hasattr(self, 'model'):
            del self.model
        if hasattr(self, 'mocr'):
            del self.mocr
    
    async def _infer(self, image: np.ndarray, textlines: List[Quadrilateral], config: OcrConfig, verbose: bool = False, ignore_bubble: int = 0) -> List[TextBlock]:
        text_height = 48
        max_chunk_size = 16
        ignore_bubble = config.ignore_bubble
        use_model_bubble_filter = bool(getattr(config, 'use_model_bubble_filter', False))

        quadrilaterals = list(self._generate_text_direction(textlines))
        region_imgs = [q.get_transformed_region(image, d, text_height) for q, d in quadrilaterals]

        perm = range(len(region_imgs))
        is_quadrilaterals = False
        if len(quadrilaterals) > 0 and isinstance(quadrilaterals[0][0], Quadrilateral):
            perm = sorted(range(len(region_imgs)), key = lambda x: region_imgs[x].shape[1])
            is_quadrilaterals = True
        
        texts = {}
        merged_idx = [[i] for i in range(len(region_imgs))]
        merged_quadrilaterals = quadrilaterals
        merged_region_imgs = []
        for q, d in merged_quadrilaterals:
            if d == 'h':
                merged_text_height = q.aabb.w
                merged_d = 'h'
            elif d == 'v':
                merged_text_height = q.aabb.h
                merged_d = 'h'
            merged_region_imgs.append(q.get_transformed_region(image, merged_d, merged_text_height))
        for idx in range(len(merged_region_imgs)):
            texts[idx] = self.mocr(Image.fromarray(merged_region_imgs[idx]))
        
        # ✅ 使用统一的清理方法清理合并后的 region 图像
        self._cleanup_batch_data(merged_region_imgs)
            
        ix = 0
        out_regions = {}
        for indices in chunks(perm, max_chunk_size):
            # 先过滤掉非气泡区域
            valid_indices = []
            valid_region_imgs = []
            valid_widths = []
            
            for idx in indices:
                # 使用基类的通用气泡过滤方法（支持高级检测）
                if ignore_bubble > 0 or use_model_bubble_filter:
                    textline = quadrilaterals[idx][0]
                    if self._should_ignore_region(region_imgs[idx], ignore_bubble, image, textline, config):
                        self.logger.info(f'[FILTERED] Region {ix} ignored - Non-bubble area detected (ignore_bubble={ignore_bubble}, model_filter={use_model_bubble_filter})')
                        ix += 1
                        continue
                valid_indices.append(idx)
                valid_region_imgs.append(region_imgs[idx])
                valid_widths.append(region_imgs[idx].shape[1])
                ix += 1
            
            # 如果所有区域都被过滤了，跳过这个 chunk
            if len(valid_indices) == 0:
                continue
            
            N = len(valid_indices)
            max_width = self._get_ocr_canvas_width(valid_widths, base_align=4)
            region = np.zeros((N, text_height, max_width, 3), dtype = np.uint8)
            idx_keys = []
            for i, idx in enumerate(valid_indices):
                idx_keys.append(idx)
                W = valid_region_imgs[i].shape[1]
                region[i, :, : W, :] = valid_region_imgs[i]
                if verbose:
                    ocr_result_dir = os.environ.get('MANGA_OCR_RESULT_DIR', 'result/ocrs/')
                    os.makedirs(ocr_result_dir, exist_ok=True)
                    if quadrilaterals[idx][1] == 'v':
                        imwrite_unicode(os.path.join(ocr_result_dir, f'{ix-N+i}.png'), cv2.rotate(cv2.cvtColor(region[i, :, :, :], cv2.COLOR_RGB2BGR), cv2.ROTATE_90_CLOCKWISE), self.logger)
                    else:
                        imwrite_unicode(os.path.join(ocr_result_dir, f'{ix-N+i}.png'), cv2.cvtColor(region[i, :, :, :], cv2.COLOR_RGB2BGR), self.logger)
            image_tensor = (torch.from_numpy(region).float() - 127.5) / 127.5
            image_tensor = einops.rearrange(image_tensor, 'N H W C -> N C H W')
            if self.use_gpu:
                image_tensor = image_tensor.to(self.torch_device)
            with torch.no_grad():
                ret = self.model.infer_beam_batch(image_tensor, valid_widths, beams_k = 5, max_seq_length = 255)
            
            for i, (pred_chars_index, prob, fg_pred, bg_pred, fg_ind_pred, bg_ind_pred) in enumerate(ret):
                if prob < 0.2:
                    # Decode text first to log it
                    seq = []
                    for chid in pred_chars_index:
                        ch = self.model.dictionary[chid]
                        if ch == '<S>':
                            continue
                        if ch == '</S>':
                            break
                        if ch == '<SP>':
                            ch = ' '
                        seq.append(ch)
                    txt = ''.join(seq)
                    self.logger.info(f'[FILTERED] prob: {prob:.4f} < threshold: 0.2 - Text: "{txt}"')
                    # Keep the textline with empty text for hybrid OCR to retry
                    cur_region = quadrilaterals[valid_indices[i]][0]
                    if isinstance(cur_region, Quadrilateral):
                        cur_region.text = ''  # Empty text for hybrid OCR
                        cur_region.prob = prob
                        cur_region.fg_r = 0
                        cur_region.fg_g = 0
                        cur_region.fg_b = 0
                        cur_region.bg_r = 255
                        cur_region.bg_g = 255
                        cur_region.bg_b = 255
                    else:
                        cur_region.update_font_colors(np.array([0, 0, 0]), np.array([255, 255, 255]))
                    out_regions[idx_keys[i]] = cur_region
                    continue
                has_fg = (fg_ind_pred[:, 1] > fg_ind_pred[:, 0])
                has_bg = (bg_ind_pred[:, 1] > bg_ind_pred[:, 0])
                fr = AvgMeter()
                fg = AvgMeter()
                fb = AvgMeter()
                br = AvgMeter()
                bg = AvgMeter()
                bb = AvgMeter()
                for chid, c_fg, c_bg, h_fg, h_bg in zip(pred_chars_index, fg_pred, bg_pred, has_fg, has_bg) :
                    ch = self.model.dictionary[chid]
                    if ch == '<S>':
                        continue
                    if ch == '</S>':
                        break
                    if h_fg.item() :
                        fr(int(c_fg[0] * 255))
                        fg(int(c_fg[1] * 255))
                        fb(int(c_fg[2] * 255))
                    if h_bg.item() :
                        br(int(c_bg[0] * 255))
                        bg(int(c_bg[1] * 255))
                        bb(int(c_bg[2] * 255))
                    else :
                        br(int(c_fg[0] * 255))
                        bg(int(c_fg[1] * 255))
                        bb(int(c_fg[2] * 255))
                fr = min(max(int(fr()), 0), 255)
                fg = min(max(int(fg()), 0), 255)
                fb = min(max(int(fb()), 0), 255)
                br = min(max(int(br()), 0), 255)
                bg = min(max(int(bg()), 0), 255)
                bb = min(max(int(bb()), 0), 255)
                cur_region = quadrilaterals[indices[i]][0]
                if isinstance(cur_region, Quadrilateral):
                    cur_region.prob = prob
                    cur_region.fg_r = fr
                    cur_region.fg_g = fg
                    cur_region.fg_b = fb
                    cur_region.bg_r = br
                    cur_region.bg_g = bg
                    cur_region.bg_b = bb
                else:
                    cur_region.update_font_colors(np.array([fr, fg, fb]), np.array([br, bg, bb]))

                out_regions[idx_keys[i]] = cur_region
            
            # ✅ 使用统一的清理方法清理 chunk 数据
            self._cleanup_ocr_memory(ret, region, image_tensor, force_gpu_cleanup=True)
                
        output_regions = []
        for i, nodes in enumerate(merged_idx):
            total_logprobs = 0
            total_area = 0
            fg_r = []
            fg_g = []
            fg_b = []
            bg_r = []
            bg_g = []
            bg_b = []
            
            for idx in nodes:
                if idx not in out_regions:
                    continue
                    
                region_out = out_regions[idx]
                total_logprobs += np.log(region_out.prob) * region_out.area
                total_area += region_out.area
                fg_r.append(region_out.fg_r)
                fg_g.append(region_out.fg_g)
                fg_b.append(region_out.fg_b)
                bg_r.append(region_out.bg_r)
                bg_g.append(region_out.bg_g)
                bg_b.append(region_out.bg_b)
                
            if total_area > 0:
                total_logprobs /= total_area
                prob = np.exp(total_logprobs)
            else:
                prob = 0.0
            fr = round(np.mean(fg_r)) if fg_r else 0
            fg = round(np.mean(fg_g)) if fg_g else 0
            fb = round(np.mean(fg_b)) if fg_b else 0
            br = round(np.mean(bg_r)) if bg_r else 0
            bg = round(np.mean(bg_g)) if bg_g else 0
            bb = round(np.mean(bg_b)) if bg_b else 0
            
            txt = texts[i]
            self.logger.info(f'prob: {prob} {txt} fg: ({fr}, {fg}, {fb}) bg: ({br}, {bg}, {bb})')
            cur_region = merged_quadrilaterals[i][0]
            if isinstance(cur_region, Quadrilateral):
                cur_region.text = txt
                cur_region.prob = prob
                cur_region.fg_r = fr
                cur_region.fg_g = fg
                cur_region.fg_b = fb
                cur_region.bg_r = br
                cur_region.bg_g = bg
                cur_region.bg_b = bb
            else: # TextBlock
                cur_region.text.append(txt)
                cur_region.update_font_colors(np.array([fr, fg, fb]), np.array([br, bg, bb]))
            output_regions.append(cur_region)
        
        # ✅ 使用统一的清理方法清理最终数据
        self._cleanup_batch_data(region_imgs, quadrilaterals, merged_quadrilaterals, out_regions, force_gpu_cleanup=True)

        if is_quadrilaterals:
            return output_regions
        return textlines
