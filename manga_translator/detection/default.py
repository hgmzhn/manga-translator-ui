import os
import shutil
import numpy as np
import torch
import cv2
import einops
from typing import List, Tuple

from .default_utils.DBNet_resnet34 import TextDetection as TextDetectionDefault
from .default_utils import imgproc, dbnet_utils, craft_utils
from .common import OfflineDetector
from ..utils import TextBlock, Quadrilateral, det_rearrange_forward, imwrite_unicode
from ..utils.generic import BASE_PATH

MODEL = None
def det_batch_forward_default(batch: np.ndarray, device: str):
    global MODEL
    if isinstance(batch, list):
        batch = np.array(batch)
    batch = einops.rearrange(batch.astype(np.float32) / 127.5 - 1.0, 'n h w c -> n c h w')
    batch = torch.from_numpy(batch).to(device)
    with torch.no_grad():
        db, mask = MODEL(batch)
        db = db.sigmoid().cpu().numpy()
        mask = mask.cpu().numpy()
    return db, mask

class DefaultDetector(OfflineDetector):
    _MODEL_MAPPING = {
        'model': {
            'url': 'https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/detect-20241225.ckpt',
            'hash': '67ce1c4ed4793860f038c71189ba9630a7756f7683b1ee5afb69ca0687dc502e',
            'file': '.',
        }
    }

    def __init__(self, *args, **kwargs):
        os.makedirs(self.model_dir, exist_ok=True)
        if os.path.exists('detect-20241225.ckpt'):
            shutil.move('detect-20241225.ckpt', self._get_file_path('detect-20241225.ckpt'))
        super().__init__(*args, **kwargs)

    async def _load(self, device: str):
        self.model = TextDetectionDefault()
        sd = torch.load(self._get_file_path('detect-20241225.ckpt'), map_location='cpu')
        self.model.load_state_dict(sd['model'] if 'model' in sd else sd)
        self.model.eval()
        self.device = device
        if device == 'cuda' or device == 'mps':
            self.model = self.model.to(self.device)
        global MODEL
        MODEL = self.model

    async def _unload(self):
        del self.model

    async def _infer(self, image: np.ndarray, detect_size: int, text_threshold: float, box_threshold: float,
                     unclip_ratio: float, verbose: bool = False):
        """
        Returns:
            textlines: List of detected text lines
            raw_mask: Raw detection mask
            bbox_debug_img: Debug image with all bboxes and scores (only if verbose=True), otherwise None
        """
        # TODO: Move det_rearrange_forward to common.py and refactor
        db, mask = det_rearrange_forward(image, det_batch_forward_default, detect_size, 4, device=self.device, verbose=verbose)

        if db is None:
            # rearrangement is not required, fallback to default forward
            img_resized, target_ratio, _, pad_w, pad_h = imgproc.resize_aspect_ratio(cv2.bilateralFilter(image, 17, 80, 80), detect_size, cv2.INTER_LINEAR, mag_ratio = 1)
            img_resized_h, img_resized_w = img_resized.shape[:2]
            ratio_h = ratio_w = 1 / target_ratio
            db, mask = det_batch_forward_default([img_resized], self.device)
        else:
            img_resized_h, img_resized_w = image.shape[:2]
            ratio_w = ratio_h = 1
            pad_h = pad_w = 0
        self.logger.info(f'Detection resolution: {img_resized_w}x{img_resized_h}')

        mask = mask[0, 0, :, :]
        
        # 在verbose模式下，从mask直接提取所有连通区域用于调试图
        bbox_debug_img = None
        if verbose:
            try:
                self.logger.info(f'[DEBUG] mask shape: {mask.shape}, image shape: {image.shape}, text_threshold: {text_threshold}')
                # 诊断mask和db的数值分布
                mid_values_ratio_mask = np.sum((mask > 0.1) & (mask < 0.9)) / mask.size * 100
                self.logger.info(f'[DEBUG] mask数值分布: min={mask.min():.3f}, max={mask.max():.3f}, mean={mask.mean():.3f}')
                self.logger.info(f'[DEBUG] mask中间值(0.1-0.9)占比: {mid_values_ratio_mask:.2f}%')
                
                # 检查db的分布（用于对比）
                db_slice = db[0, 0, :, :]
                mid_values_ratio_db = np.sum((db_slice > 0.1) & (db_slice < 0.9)) / db_slice.size * 100
                self.logger.info(f'[DEBUG] db数值分布: min={db_slice.min():.3f}, max={db_slice.max():.3f}, mean={db_slice.mean():.3f}')
                self.logger.info(f'[DEBUG] db中间值(0.1-0.9)占比: {mid_values_ratio_db:.2f}%')
                
                # resize mask和db到和原图相同的尺寸（用于调试图）
                mask_resized_debug = cv2.resize(mask, (mask.shape[1] * 2, mask.shape[0] * 2), interpolation=cv2.INTER_LINEAR)
                db_resized_debug = cv2.resize(db_slice, (db_slice.shape[1] * 2, db_slice.shape[0] * 2), interpolation=cv2.INTER_LINEAR)
                if pad_h > 0:
                    mask_resized_debug = mask_resized_debug[:-pad_h, :]
                    db_resized_debug = db_resized_debug[:-pad_h, :]
                elif pad_w > 0:
                    mask_resized_debug = mask_resized_debug[:, :-pad_w]
                    db_resized_debug = db_resized_debug[:, :-pad_w]
                
                self.logger.info(f'[DEBUG] mask_resized_debug shape: {mask_resized_debug.shape}')
                
                # 对mask进行二值化（只使用text_threshold，不使用box_threshold）
                binary_mask = (mask_resized_debug > text_threshold).astype(np.uint8)
                num_white_pixels = np.sum(binary_mask)
                self.logger.info(f'[DEBUG] binary_mask has {num_white_pixels} white pixels out of {binary_mask.size} total')
                
                # 找到所有连通区域
                contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                self.logger.info(f'[DEBUG] Found {len(contours)} contours from mask')
                
                if len(contours) > 0:
                    all_textlines = []
                    all_scores = []
                    
                    for contour in contours:
                        # 获取最小外接矩形
                        rect = cv2.minAreaRect(contour)
                        box_points = cv2.boxPoints(rect).astype(np.float64)
                        
                        # 调整坐标到原图尺寸（和正常流程一样）
                        box_points = craft_utils.adjustResultCoordinates(np.array([box_points]), ratio_w, ratio_h, ratio_net=1)
                        box_points = box_points[0].astype(np.int64)
                        
                        # 计算该区域的平均置信度作为得分（使用db）
                        # 创建与db_resized_debug相同尺寸的mask
                        contour_mask = np.zeros(db_resized_debug.shape, dtype=np.uint8)
                        cv2.drawContours(contour_mask, [contour], 0, 1, -1)
                        region_score = float(np.mean(db_resized_debug[contour_mask > 0]))
                        
                        # 创建Quadrilateral并检查面积
                        quad = Quadrilateral(box_points, '', region_score)
                        # 应用和最终结果相同的面积过滤（area > 16）
                        if quad.area > 16:
                            all_textlines.append(quad)
                            all_scores.append(region_score)
                    
                    self.logger.info(f'[DEBUG] Found {len(all_textlines)} regions from mask (before box_threshold filtering)')
                    
                    # 创建调试图像（使用原图）
                    debug_img = image.copy()
                    
                    # 生成不同的颜色
                    np.random.seed(42)
                    colors = [(np.random.randint(0, 255), np.random.randint(0, 255), np.random.randint(0, 255)) 
                             for _ in range(len(all_textlines))]
                    
                    # 绘制每个边框和得分
                    for txtln, color in zip(all_textlines, colors):
                        cv2.polylines(debug_img, [txtln.pts], True, color=color, thickness=2)
                        
                        center_x = int(np.mean(txtln.pts[:, 0]))
                        center_y = int(np.mean(txtln.pts[:, 1]))
                        score_text = f'{txtln.prob:.3f}'
                        
                        font = cv2.FONT_HERSHEY_SIMPLEX
                        font_scale = 0.6
                        font_thickness = 2
                        text_size = cv2.getTextSize(score_text, font, font_scale, font_thickness)[0]
                        
                        bg_x1 = max(0, center_x - 5)
                        bg_y1 = max(0, center_y - text_size[1] - 5)
                        bg_x2 = min(debug_img.shape[1], center_x + text_size[0] + 5)
                        bg_y2 = min(debug_img.shape[0], center_y + 5)
                        
                        overlay = debug_img.copy()
                        cv2.rectangle(overlay, (bg_x1, bg_y1), (bg_x2, bg_y2), (0, 0, 0), -1)
                        cv2.addWeighted(overlay, 0.5, debug_img, 0.5, 0, debug_img)
                        
                        cv2.putText(debug_img, score_text, (center_x, center_y), 
                                   font, font_scale, (255, 255, 255), font_thickness)
                    
                    bbox_debug_img = cv2.cvtColor(debug_img, cv2.COLOR_RGB2BGR)
                    self.logger.info(f'Generated bbox debug image with {len(all_textlines)} regions from mask')
                    
                    # 同时生成经过text_threshold筛选后的二值化mask
                    binary_mask_bgr = cv2.cvtColor(binary_mask * 255, cv2.COLOR_GRAY2BGR)
                    # 返回tuple: (bbox_debug_img, binary_mask_img)
                    bbox_debug_img = (bbox_debug_img, binary_mask_bgr)
            except Exception as e:
                self.logger.error(f'Failed to create bbox debug image from mask: {e}')
        
        # 正常的检测流程（使用box_threshold）
        det = dbnet_utils.SegDetectorRepresenter(text_threshold, box_threshold, unclip_ratio=unclip_ratio)
        boxes, scores = det({'shape':[(img_resized_h, img_resized_w)]}, db)
        boxes, scores = boxes[0], scores[0]
        
        # 过滤boxes
        if boxes.size == 0:
            polys = []
            filtered_scores = []
        else:
            idx = boxes.reshape(boxes.shape[0], -1).sum(axis=1) > 0
            polys, filtered_scores = boxes[idx], scores[idx]
            polys = polys.astype(np.float64)
            polys = craft_utils.adjustResultCoordinates(polys, ratio_w, ratio_h, ratio_net=1)
            polys = polys.astype(np.int64)

        textlines = [Quadrilateral(pts.astype(int), '', score) for pts, score in zip(polys, filtered_scores)]
        
        # 使用mask生成raw_mask（用于inpainting修复）
        mask_resized = cv2.resize(mask, (mask.shape[1] * 2, mask.shape[0] * 2), interpolation=cv2.INTER_LINEAR)
        if pad_h > 0:
            mask_resized = mask_resized[:-pad_h, :]
        elif pad_w > 0:
            mask_resized = mask_resized[:, :-pad_w]
        raw_mask = np.clip(mask_resized * 255, 0, 255).astype(np.uint8)
        
        # 在verbose模式下，同时生成db版本用于对比
        if verbose:
            db_slice = db[0, 0, :, :]
            db_resized = cv2.resize(db_slice, (db_slice.shape[1] * 2, db_slice.shape[0] * 2), interpolation=cv2.INTER_LINEAR)
            if pad_h > 0:
                db_resized = db_resized[:-pad_h, :]
            elif pad_w > 0:
                db_resized = db_resized[:, :-pad_w]
            raw_mask_db = np.clip(db_resized * 255, 0, 255).astype(np.uint8)
            
            # 在bbox_debug_img中添加db热力图用于对比
            if bbox_debug_img and isinstance(bbox_debug_img, tuple):
                bbox_debug_img = (*bbox_debug_img, raw_mask_db)
        
        # ✅ Detection完成后立即清理GPU内存
        if (self.device.startswith('cuda') or self.device == 'mps'):
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        
        return textlines, raw_mask, bbox_debug_img
