"""
替换翻译模块 - 将翻译图的OCR结果应用到生肉图上

功能说明：
1. 对生肉图执行检测+OCR，过滤低置信度区域
2. 对翻译图执行检测+OCR，保留子框信息用于断句
3. 区域匹配：根据尺寸缩放对齐坐标，计算重叠区域
4. 过滤：移除生肉独有和翻译独有的区域
5. 合并：将翻译图的OCR结果作为translation字段
6. 可选：模板匹配对齐 - 自动计算中日文图的偏移量并调整文字位置

使用场景：
- 同一本漫画的不同版本（如修复版/原版）
- 不同分辨率版本
- 无JSON数据时的翻译迁移

作者: manga-translator-ui
日期: 2026-01-01
"""

import asyncio
import logging
import os
import traceback
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..image_formats import SUPPORTED_IMAGE_EXTENSIONS
from .generic import Context, dump_image, imwrite_unicode, open_pil_image, save_pil_image
from .path_manager import TRANSLATED_IMAGES_SUBDIR, get_work_dir
from .textblock import TextBlock

logger = logging.getLogger(__name__)


def get_text_to_img_solid_ink(mask_final, cn_text_img, origin_img, mengban=8, pan=150):
    """
    超清锐利版文字合成函数
    核心逻辑：使用 Lanczos4 插值 + 色阶压缩 (Levels)，最大程度还原源图的锐利度。
    
    Args:
        mask_final: 蒙版图像
        cn_text_img: 翻译图（要提取文字的图）
        origin_img: 生肉图（底图）
        mengban: 蒙版膨胀核大小
        pan: 白场阈值偏移
    
    Returns:
        合成后的图像
    """
    # --- 1. 基础校验 ---
    if mask_final is None or cn_text_img is None or origin_img is None:
        raise ValueError("输入图片为空")

    h, w = origin_img.shape[:2]

    # --- 2. 缩放 ---
    # 蒙版使用最近邻插值，保持边缘清晰
    if mask_final.shape[:2] != (h, w):
        mask_final = cv2.resize(mask_final, (w, h), interpolation=cv2.INTER_NEAREST)
    
    # 文字使用线性插值，边缘更柔和自然
    if cn_text_img.shape[:2] != (h, w):
        cn_text_img = cv2.resize(cn_text_img, (w, h), interpolation=cv2.INTER_LINEAR)
    
    # --- 3. 准备蒙版区域 ---
    if len(mask_final.shape) == 3:
        mask_gray = cv2.cvtColor(mask_final, cv2.COLOR_BGR2GRAY)
    else:
        mask_gray = mask_final
    _, mask_binary = cv2.threshold(mask_gray, 127, 255, cv2.THRESH_BINARY)
    
    # 安全区 (限制文字范围) - 直接使用原始蒙版，不额外膨胀
    mask_safe_zone = mask_binary

    # --- 4. 文字图色阶增强 ---
    # 像 PS 的色阶工具：设定黑场和白场，中间线性拉伸
    if len(cn_text_img.shape) == 3:
        text_gray = cv2.cvtColor(cn_text_img, cv2.COLOR_BGR2GRAY)
    else:
        text_gray = cn_text_img

    # 参数设置 - 降低对比度，保留更多灰度过渡
    in_black = 50   # 黑场阈值：降低以保留边缘灰度
    in_white = max(in_black + 80, pan + 50) # 白场阈值：增大范围让过渡更柔和
    if in_white > 255: 
        in_white = 255
    
    # 构建查找表 (LUT) 进行快速色阶映射
    lut = np.zeros(256, dtype=np.uint8)
    for i in range(256):
        if i <= in_black:
            val = 0 # 纯黑
        elif i >= in_white:
            val = 255 # 纯白
        else:
            # 线性拉伸中间的灰度
            val = int((i - in_black) / (in_white - in_black) * 255)
        lut[i] = val
        
    # 应用色阶调整
    text_enhanced_gray = cv2.LUT(text_gray, lut)
    
    # 轻微高斯模糊，进一步柔化边缘
    text_enhanced_gray = cv2.GaussianBlur(text_enhanced_gray, (3, 3), 0.5)
    
    # 转回 BGR 方便合并
    text_enhanced_bgr = cv2.cvtColor(text_enhanced_gray, cv2.COLOR_GRAY2BGR)

    # --- 5. 执行合成 ---
    result_img = origin_img.copy()
    
    # 5.1 直接在安全区内进行正片叠底 (Bitwise And)
    # 因为 origin_img (img_inpainted) 已经被 inpainting 擦除过了，不需要再涂白
    roi_bg = result_img[mask_safe_zone == 255]
    roi_text = text_enhanced_bgr[mask_safe_zone == 255]
    
    # 融合：白底(255) & 文字 -> 文字
    fused = cv2.bitwise_and(roi_bg, roi_text)
    
    result_img[mask_safe_zone == 255] = fused

    return result_img


async def translate_batch_replace_translation(translator, images_with_configs: List[tuple], save_info: dict = None, global_offset: int = 0, global_total: int = None) -> List[Context]:
    """
    替换翻译模式：从翻译图提取OCR结果并应用到生肉图
    
    流程：
    1. 对生肉图执行检测+OCR，过滤低置信度区域
    2. 查找对应的翻译图，执行检测+OCR
    3. 区域匹配（考虑尺寸缩放）
    4. 使用匹配的区域执行修复和渲染
    
    Args:
        translator: MangaTranslator实例
        images_with_configs: List of (image, config) tuples
        save_info: 保存配置
        global_offset: 全局偏移量
        global_total: 全局总图片数
    """
    logger.info(f"Starting replace translation mode with {len(images_with_configs)} images")
    results = []
    
    display_total = global_total if global_total is not None else len(images_with_configs)
    failed_count = 0
    
    for idx, (image, config) in enumerate(images_with_configs):
        # ✅ 检查停止标志
        await asyncio.sleep(0)
        translator._check_cancelled()

        raw_ctx = None
        translated_ctx = None
        translated_image = None
        image_name = image if isinstance(image, str) else (image.name if hasattr(image, 'name') else f"image_{idx}")
        global_idx = global_offset + idx + 1
        loaded_source_image = False

        if isinstance(image, str):
            try:
                with open(image, 'rb') as f:
                    loaded_image = open_pil_image(f, eager=True)
                loaded_image.name = image
                image = loaded_image
                loaded_source_image = True
            except Exception as e:
                logger.error(f"  加载原图失败: {image_name} - {e}")
                ctx = Context()
                ctx.image_name = image_name
                ctx.text_regions = []
                ctx.success = False
                ctx.translation_error = str(e)
                results.append(ctx)
                failed_count += 1
                await translator._report_progress(f"batch:{global_idx}:{global_idx}:{display_total}:{failed_count}")
                continue
        
        # 强制启用 AI断句 和 严格边框模式
        if config and hasattr(config, 'render'):
            config.render.disable_auto_wrap = True
            config.render.layout_mode = 'strict'
            
            # 标记为替换翻译模式，以便渲染器识别并应用特殊逻辑（如强制单行不换行）
            if hasattr(config, 'cli'):
                config.cli.replace_translation = True
            
            logger.info("Replace translation mode: Forced disable_auto_wrap=True, layout_mode='strict', replace_translation=True")
        
        logger.info(f"[{global_idx}/{display_total}] Processing: {os.path.basename(image_name)}")
        
        try:
            translator._set_image_context(config, image)
            
            # === 步骤1: 查找翻译图 ===
            translated_path = find_translated_image(image_name)
            if not translated_path:
                logger.warning(f"  [跳过] 未找到对应的翻译图: {os.path.basename(image_name)}")
                ctx = Context()
                ctx.input = image
                ctx.image_name = image_name
                ctx.text_regions = []
                ctx.success = False
                results.append(ctx)
                failed_count += 1
                await translator._report_progress(f"batch:{global_idx}:{global_idx}:{display_total}:{failed_count}")
                continue
            
            logger.info(f"  找到翻译图: {os.path.basename(translated_path)}")
            
            # === 步骤2: 对生肉图执行检测+OCR ===
            logger.info("  [1/4] 生肉图检测+OCR...")
            # ✅ 检查停止标志
            await asyncio.sleep(0)
            translator._check_cancelled()
            
            raw_ctx = await translator._translate_until_translation(image, config)
            raw_ctx.image_name = image_name
            # 保存原始图片尺寸（用于保存JSON）
            if hasattr(image, 'size'):
                raw_ctx.original_size = image.size

            if not raw_ctx.text_regions:
                logger.warning("  [跳过] 生肉图未检测到文本区域，直接输出原图")
                # 设置result为原图
                raw_ctx.result = image
                raw_ctx.text_regions = []
                # 跳转到保存步骤（不使用continue）
                skip_to_save = True
            else:
                skip_to_save = False
            
            if not skip_to_save:
                # 过滤低置信度区域
                min_prob = config.ocr.prob if hasattr(config.ocr, 'prob') and config.ocr.prob else 0.1
                raw_regions_filtered = [r for r in raw_ctx.text_regions if getattr(r, 'prob', 1.0) >= min_prob]
                logger.info(f"    生肉图区域: {len(raw_ctx.text_regions)} -> 过滤后: {len(raw_regions_filtered)}")
                
                if not raw_regions_filtered:
                    logger.warning("  [跳过] 过滤后无有效区域，直接输出原图")
                    # 设置result为原图
                    raw_ctx.result = image
                    raw_ctx.text_regions = []
                    skip_to_save = True
            
            if not skip_to_save:
                # 记录生肉图尺寸
                raw_size = (raw_ctx.img_rgb.shape[1], raw_ctx.img_rgb.shape[0]) if raw_ctx.img_rgb is not None else (image.width, image.height)
                
                # === 步骤3: 对翻译图执行检测+OCR ===
                logger.info("  [2/4] 翻译图检测+OCR...")
                # ✅ 检查停止标志
                await asyncio.sleep(0)
                translator._check_cancelled()
                
                translated_image = open_pil_image(translated_path, eager=False)
                translated_image.name = translated_path
                
                translated_ctx = await translator._translate_until_translation(translated_image, config)
                translated_ctx.image_name = translated_path
                
                if not translated_ctx.text_regions:
                    logger.warning("  [跳过] 翻译图未检测到文本区域，直接输出原图")
                    # 设置result为原图
                    raw_ctx.result = image
                    raw_ctx.text_regions = []
                    skip_to_save = True
            
            if not skip_to_save:
                # 过滤低置信度区域
                trans_regions_filtered = [r for r in translated_ctx.text_regions if getattr(r, 'prob', 1.0) >= min_prob]
                logger.info(f"    翻译图区域: {len(translated_ctx.text_regions)} -> 过滤后: {len(trans_regions_filtered)}")
                
                # 记录翻译图尺寸
                trans_size = (translated_ctx.img_rgb.shape[1], translated_ctx.img_rgb.shape[0]) if translated_ctx.img_rgb is not None else (translated_image.width, translated_image.height)
                
                # === 步骤4: 区域匹配 ===
                logger.info("  [3/4] 区域匹配...")
                # ✅ 检查停止标志
                await asyncio.sleep(0)
                translator._check_cancelled()
                
                logger.info(f"    生肉图尺寸: {raw_size[0]}x{raw_size[1]}")
                logger.info(f"    翻译图尺寸: {trans_size[0]}x{trans_size[1]}")
                logger.info(f"    缩放比例: x={raw_size[0]/trans_size[0]:.3f}, y={raw_size[1]/trans_size[1]:.3f}")
                
                # 将翻译图区域缩放到生肉图尺寸
                scaled_trans_regions = scale_regions_to_target(trans_regions_filtered, trans_size, raw_size)
                
                # 执行匹配（使用以小框为基准的重叠率）
                matches = match_regions(raw_regions_filtered, scaled_trans_regions, iou_threshold=0.3)
                logger.info(f"    匹配结果: {len(matches)} 对区域 (重叠率 >= 0.3, 以小框为基准)")
                
                # 创建匹配后的区域（直接使用翻译框用于渲染）
                matched_regions, matched_raw_indices = create_matched_regions(
                    raw_regions_filtered, scaled_trans_regions, matches
                )
                
                # 用于修复的区域：只使用翻译框对应的生肉框（去重）
                # 这样可以避免修复那些在生肉图中存在但翻译图中不存在的区域
                inpaint_raw_indices = set()
                for raw_idx, trans_idx, overlap in matches:
                    inpaint_raw_indices.add(raw_idx)
                inpaint_regions = [raw_regions_filtered[i] for i in sorted(inpaint_raw_indices)]
                
                # 找出未被匹配的生肉区域（这些不应该被修复）
                all_raw_indices = set(range(len(raw_regions_filtered)))
                unmatched_raw_indices = all_raw_indices - inpaint_raw_indices
                if unmatched_raw_indices:
                    logger.info(f"    [未匹配] {len(unmatched_raw_indices)} 个生肉区域未匹配，不会被修复: {sorted(unmatched_raw_indices)}")
                
                logger.info(f"    最终区域: {len(matched_regions)} 个 (用于渲染), {len(inpaint_regions)} 个 (用于修复)")

                # === DEBUG: 生成匹配调试图 ===
                if translator.verbose:
                    try:
                        # 复制生肉图作为画布
                        debug_img = raw_ctx.img_rgb.copy()
                        if len(debug_img.shape) == 2: # 灰度图转RGB
                            debug_img = cv2.cvtColor(debug_img, cv2.COLOR_GRAY2BGR)
                        elif debug_img.shape[2] == 4: # RGBA转RGB
                            debug_img = cv2.cvtColor(debug_img, cv2.COLOR_RGBA2BGR)
                        else:
                            debug_img = debug_img.copy() # BGR/RGB
                        
                        logger.info(f"    [DEBUG] 生肉框数量: {len(raw_regions_filtered)}, 翻译框数量: {len(scaled_trans_regions)}, 匹配对数量: {len(matches)}")
                        
                        # 1. 画生肉框 (红色) - 分别绘制每个子框
                        for i, region in enumerate(raw_regions_filtered):
                            # lines 包含多个子框，每个子框是4个点，需要分别绘制
                            # 将 lines reshape 为 (n_boxes, 4, 2)
                            lines_reshaped = region.lines.reshape(-1, 4, 2)
                            for box in lines_reshaped:
                                pts = box.reshape((-1, 1, 2)).astype(np.int32)
                                cv2.polylines(debug_img, [pts], True, (0, 0, 255), 2)
                            # 使用TextBlock的center属性（整个区域的中心）
                            center = region.center.astype(int)
                            cv2.putText(debug_img, f"R{i}", tuple(center), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

                        # 2. 画翻译框 (绿色) - 分别绘制每个子框
                        for i, region in enumerate(scaled_trans_regions):
                            # 将 lines reshape 为 (n_boxes, 4, 2)
                            lines_reshaped = region.lines.reshape(-1, 4, 2)
                            for box in lines_reshaped:
                                pts = box.reshape((-1, 1, 2)).astype(np.int32)
                                cv2.polylines(debug_img, [pts], True, (0, 255, 0), 2)
                            # 使用TextBlock的center属性，稍微偏移避免重叠
                            center = region.center.astype(int)
                            center[1] += 20  # Y轴偏移
                            cv2.putText(debug_img, f"T{i}", tuple(center), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                        # 3. 画匹配线和重叠率 (黄色) - 使用区域中心
                        for raw_idx, trans_idx, overlap in matches:
                            raw_center = raw_regions_filtered[raw_idx].center.astype(int)
                            trans_center = scaled_trans_regions[trans_idx].center.astype(int)
                            
                            cv2.line(debug_img, tuple(raw_center), tuple(trans_center), (0, 255, 255), 2)
                            
                            mid_point = ((raw_center + trans_center) / 2).astype(int)
                            # 显示重叠率（以小框为基准）
                            cv2.putText(debug_img, f"{overlap:.2f}", tuple(mid_point), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                        # 保存调试图
                        debug_path = translator._result_path('replace_debug_match.jpg')
                        imwrite_unicode(debug_path, debug_img, logger)
                        logger.info(f"    [DEBUG] Saved match debug image to: {debug_path}")
                        
                    except Exception as e:
                        logger.warning(f"    [DEBUG] Failed to generate debug image: {e}")
                        traceback.print_exc()
                
                # === 步骤5: 修复生肉图 ===
                logger.info("  [4/4] 修复生肉图...")
                # ✅ 检查停止标志
                await asyncio.sleep(0)
                translator._check_cancelled()
                
                # 检查是否有需要修复的区域
                if not inpaint_regions:
                    logger.warning("  [跳过] 没有需要修复的区域，将保存原图")
                    # 设置result为原图，而不是标记为失败
                    raw_ctx.result = image
                    raw_ctx.text_regions = []
                    skip_to_save = True

            if not skip_to_save:
                # 临时替换 text_regions 为修复区域
                original_regions = raw_ctx.text_regions
                raw_ctx.text_regions = inpaint_regions
                
                # 检查修复模型是否为 none
                inpainter_model = config.inpainter.inpainter if hasattr(config, 'inpainter') and hasattr(config.inpainter, 'inpainter') else None
                logger.info(f"    [调试] 修复模型配置: {inpainter_model} (类型: {type(inpainter_model)})")
                
                # 判断是否为 none（只判断明确设置为 'none' 的情况，不包括 None）
                is_none_inpainter = (inpainter_model == 'none' or 
                                    (hasattr(inpainter_model, 'value') and inpainter_model.value == 'none') or
                                    (inpainter_model is not None and str(inpainter_model) == 'none'))
                
                if is_none_inpainter:
                    # 修复模型为 none，使用替换翻译专用的检测模块
                    # 重新获取原始蒙版并用 REFINEMASK_INPAINT 精炼（和 win.py 一致）
                    logger.info("    [修复模型=none] 使用替换翻译专用检测模块...")
                    
                    try:
                        from ..config import Detector
                        from ..detection import get_detector
                        from .ctd_replace import detect_for_replace_translation
                        
                        # 获取 CTD 检测器实例
                        detector = get_detector(Detector.ctd)
                        if not detector.is_loaded():
                            await detector.load(translator.device)
                        
                        # 使用新模块重新检测，获取原始蒙版和 REFINEMASK_INPAINT 精炼后的蒙版
                        _, mask_raw, mask_refined = await detect_for_replace_translation(
                            detector,
                            raw_ctx.img_rgb,
                            detect_size=config.detector.detection_size if hasattr(config.detector, 'detection_size') else 1536,
                            verbose=translator.verbose
                        )
                        
                        raw_ctx.mask_raw = mask_raw  # 更新为真正的原始蒙版
                        raw_ctx.mask = mask_refined   # 使用 REFINEMASK_INPAINT 精炼后的蒙版
                        logger.info(f"    检测完成，原始蒙版像素: {np.count_nonzero(mask_raw)}, 精炼后像素: {np.count_nonzero(mask_refined) if mask_refined is not None else 0}")
                        
                    except Exception as e:
                        logger.warning(f"    [警告] 替换翻译专用检测失败: {e}，回退到简单膨胀")
                        # 回退方案：简单膨胀
                        if raw_ctx.mask_raw is not None:
                            kernel = np.ones((5, 5), np.uint8)
                            raw_ctx.mask = cv2.dilate(raw_ctx.mask_raw, kernel, iterations=1)
                            raw_ctx.mask[raw_ctx.mask > 0] = 255
                        else:
                            raw_ctx.mask = None
                    
                    raw_ctx.mask_is_refined = True  # 标记为已精炼
                else:
                    # 正常流程：生成优化蒙版
                    logger.info("    Generating mask for inpainting...")
                    raw_ctx.mask = await translator._run_mask_refinement(config, raw_ctx)
                    
                    # 保存优化后的蒙版到 mask_raw（用于后续加载时跳过优化）
                    raw_ctx.mask_raw = raw_ctx.mask
                    
                    # 标记蒙版已优化，保存JSON时会设置 mask_is_refined=True
                    raw_ctx.mask_is_refined = True
                
                # 蒙版修复后的额外膨胀（使用配置参数）
                if raw_ctx.mask is not None:
                    kernel_size = config.kernel_size if hasattr(config, 'kernel_size') else 5
                    mask_dilation_offset = config.mask_dilation_offset if hasattr(config, 'mask_dilation_offset') else 0
                    
                    if mask_dilation_offset > 0:
                        # 根据像素数计算迭代次数：offset / (kernel_size - 1)
                        # 例如：offset=10, kernel_size=5 -> iterations=10/4=2.5 -> 3次
                        iterations = max(int(mask_dilation_offset / (kernel_size - 1) + 0.5), 1)
                        kernel = np.ones((kernel_size, kernel_size), np.uint8)
                        raw_ctx.mask = cv2.dilate(raw_ctx.mask, kernel, iterations=iterations)
                        logger.info(f"    蒙版额外膨胀: kernel_size={kernel_size}, offset={mask_dilation_offset}像素, iterations={iterations}")
                    else:
                        logger.info(f"    跳过蒙版额外膨胀 (offset={mask_dilation_offset})")
                
                # 根据修复模型配置决定是否执行修复
                if is_none_inpainter:
                    # 修复模型为 none，使用智能涂白（直接用蒙版涂白）
                    logger.info("    [修复模型=none] 使用智能涂白，跳过修复模型")
                    # 直接用白色填充蒙版区域
                    raw_ctx.img_inpainted = raw_ctx.img_rgb.copy()
                    if raw_ctx.mask is not None:
                        raw_ctx.img_inpainted[raw_ctx.mask > 0] = 255
                else:
                    # 使用修复模型进行修复
                    logger.info(f"    使用修复模型进行修复: {inpainter_model}")
                    raw_ctx.img_inpainted = await translator._run_inpainting(config, raw_ctx)
                raw_ctx.text_regions = original_regions  # 恢复区域列表
            
                # 保存修复后的调试图（如果启用verbose）
                if translator.verbose:
                    try:
                        inpainted_path = translator._result_path('inpainted.png')
                        imwrite_unicode(inpainted_path, cv2.cvtColor(raw_ctx.img_inpainted, cv2.COLOR_RGB2BGR), logger)
                        logger.info(f"    [DEBUG] Saved inpainted debug image to: {inpainted_path}")
                    except Exception as e:
                        logger.warning(f"    [DEBUG] Failed to save inpainted debug image: {e}")
            
                # === 步骤6: 渲染或粘贴 ===
                # 检查是否启用直接粘贴模式
                if config.render.enable_template_alignment:
                    logger.info("  [5/5] 直接粘贴模式 - 使用 darken_blend2 合成算法")

                    # 获取图像尺寸
                    h, w = raw_ctx.img_inpainted.shape[:2]

                    # 检查翻译图是否有原始蒙版
                    if not hasattr(translated_ctx, 'mask_raw') or translated_ctx.mask_raw is None:
                        logger.warning("  [警告] 翻译图没有原始蒙版，使用生肉图的蒙版")
                        translated_mask = raw_ctx.mask
                    else:
                        logger.info("    使用翻译图的蒙版...")
                        # 确保蒙版不为空
                        if translated_ctx.mask_raw is not None and translated_ctx.mask_raw.size > 0:
                            translated_mask = translated_ctx.mask_raw.copy()
                        else:
                            logger.warning("  [警告] 翻译图蒙版为空，使用生肉图的蒙版")
                            translated_mask = raw_ctx.mask

                    # 使用直接覆盖方式（在蒙版区域内用翻译图覆盖修复图）
                    result_img = raw_ctx.img_inpainted.copy()
                
                    # 确保翻译图和修复图尺寸一致
                    h, w = result_img.shape[:2]
                    trans_img = translated_ctx.img_rgb
                    if trans_img.shape[:2] != (h, w):
                        trans_img = cv2.resize(trans_img, (w, h), interpolation=cv2.INTER_LINEAR)
                
                    # 缩放蒙版到目标尺寸（如果不同）
                    if translated_mask.shape[:2] != (h, w):
                        translated_mask = cv2.resize(translated_mask, (w, h), interpolation=cv2.INTER_NEAREST)
                
                    # 确保蒙版是单通道
                    if len(translated_mask.shape) == 3:
                        translated_mask = cv2.cvtColor(translated_mask, cv2.COLOR_BGR2GRAY)
                
                    # === 使用配置的膨胀参数处理蒙版 ===
                    # 二值化
                    _, thres = cv2.threshold(translated_mask, 127, 255, cv2.THRESH_BINARY)
                
                    # 膨胀（使用配置参数）
                    dilation_pixels = config.render.paste_mask_dilation_pixels
                    if dilation_pixels > 0:
                        # 根据配置计算迭代次数：pixels // 3
                        iterations = max(dilation_pixels // 3, 1)
                        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
                        translated_mask = cv2.dilate(thres, kernel, iterations=iterations)
                        logger.info(f"    蒙版处理：二值化 + 膨胀(3x3椭圆核, {iterations}次迭代, 配置={dilation_pixels}像素)")
                    else:
                        translated_mask = thres
                        logger.info("    蒙版处理：仅二值化（膨胀已禁用）")
                
                    # === 使用 darken_blend2 的合成逻辑 ===
                    # 1. 从翻译图中提取文字（使用蒙版）
                    text = cv2.bitwise_and(trans_img, trans_img, mask=translated_mask)
                
                    # 2. 从修复图中清除对应区域（准备放置文字）
                    result_img = cv2.bitwise_and(result_img, result_img, mask=cv2.bitwise_not(translated_mask))
                
                    # 3. 合并：将提取的文字叠加到修复图上
                    result_img = cv2.add(result_img, text)
                
                    logger.info("    使用 darken_blend2 合成逻辑：提取文字 -> 清除区域 -> 叠加合并")
                
                    # 保存调试图（如果启用verbose）
                    if translator.verbose:
                        try:
                            # 保存提取的文字
                            debug_text_path = translator._result_path('debug_extracted_text.png')
                            imwrite_unicode(debug_text_path, cv2.cvtColor(text, cv2.COLOR_RGB2BGR), logger)
                            logger.info(f"    [DEBUG] 保存提取的文字: {debug_text_path}")
                        except Exception as e:
                            logger.warning(f"    [DEBUG] 保存调试图失败: {e}")
                
                    # 使用 dump_image 将结果转换为 PIL Image
                    raw_ctx.result = dump_image(
                        raw_ctx.input,
                        result_img,
                        getattr(raw_ctx, 'img_alpha', None),
                        mask=translated_mask,
                    )
                
                else:
                    # 原有逻辑：OCR + 重新渲染
                    logger.info("  [5/5] 渲染模式 - 使用 OCR 结果重新渲染文字")
                
                    # 更新 context 的 text_regions 为匹配后的区域
                    raw_ctx.text_regions = matched_regions
                
                    # 执行渲染
                    img_rendered = await translator._run_text_rendering(config, raw_ctx)
                
                    # 使用 dump_image 将渲染后的 numpy 数组转换为 PIL Image
                    raw_ctx.result = dump_image(
                        raw_ctx.input,
                        img_rendered,
                        getattr(raw_ctx, 'img_alpha', None),
                        mask=getattr(raw_ctx, 'mask', None),
                        render_alpha=getattr(raw_ctx, 'img_render_alpha', None),
                    )
            
            # === 步骤6: 保存结果 ===
            if save_info:
                try:
                    # 使用 translator._calculate_output_path 计算输出路径
                    final_output_path = translator._calculate_output_path(image_name, save_info)
                    final_output_dir = os.path.dirname(final_output_path)
                    
                    if hasattr(raw_ctx, 'result') and raw_ctx.result is not None:
                        os.makedirs(final_output_dir, exist_ok=True)
                        
                        save_pil_image(
                            raw_ctx.result,
                            final_output_path,
                            source_image=raw_ctx.input,
                            quality=translator.save_quality,
                        )
                        logger.info(f"  -> 已保存: {os.path.basename(final_output_path)}")
                        
                        # 标记成功
                        raw_ctx.success = True
                        
                        # ✅ 保存后清理result以释放内存
                        raw_ctx.result = None
                        
                        # 只有在非直接粘贴模式下才保存 inpainted 和 JSON
                        if not config.render.enable_template_alignment:
                            # 保存修复后的图片（inpainted）到新目录结构
                            # 与正常翻译流程保持一致
                            if translator.save_text and hasattr(raw_ctx, 'img_inpainted') and raw_ctx.img_inpainted is not None:
                                translator._save_inpainted_image(image_name, raw_ctx.img_inpainted)
                            
                            # 保存翻译数据JSON
                            if translator.save_text:
                                translator._save_text_to_file(image_name, raw_ctx, config)
                        else:
                            logger.info("  -> [直接粘贴模式] 跳过保存 JSON 和 inpainted 图片")
                        
                        # 导出可编辑PSD（如果启用）
                        if hasattr(config, 'cli') and hasattr(config.cli, 'export_editable_psd') and config.cli.export_editable_psd:
                            # 直接粘贴模式下也不导出 PSD（因为没有文本区域数据）
                            if not config.render.enable_template_alignment:
                                try:
                                    from .photoshop_export import (
                                        get_psd_output_path,
                                        photoshop_export,
                                        resolve_photoshop_font,
                                    )
                                    psd_path = get_psd_output_path(image_name)
                                    cli_cfg = getattr(config, 'cli', None)
                                    default_font = resolve_photoshop_font(config)
                                    line_spacing = getattr(config.render, 'line_spacing', None) if hasattr(config, 'render') else None
                                    script_only = getattr(cli_cfg, 'psd_script_only', False)
                                    photoshop_export(psd_path, raw_ctx, default_font, image_name, translator.verbose, translator._result_path, line_spacing, script_only)
                                    logger.info(f"  -> ✅ [PSD] 已导出可编辑PSD: {os.path.basename(psd_path)}")
                                except Exception as psd_err:
                                    logger.error(f"  导出PSD失败: {psd_err}")
                            else:
                                logger.info("  -> [直接粘贴模式] 跳过导出 PSD")
                        
                except Exception as save_err:
                    logger.error(f"  保存失败: {save_err}")
                    raw_ctx.success = False
            else:
                # 没有 save_info 也标记成功（可能是预览模式）
                raw_ctx.success = True
            
            results.append(raw_ctx)
            if not getattr(raw_ctx, 'success', False):
                failed_count += 1
            await translator._report_progress(f"batch:{global_idx}:{global_idx}:{display_total}:{failed_count}")
            
            # ✅ 每处理完一张图片后立即清理内存
            translator._cleanup_context_memory(raw_ctx, keep_result=True)
            
            # 如果有翻译图的上下文，也清理
            if translated_ctx:
                translator._cleanup_context_memory(translated_ctx, keep_result=False)
            
        except Exception as e:
            logger.error(f"  处理失败: {e}")
            traceback.print_exc()
            ctx = Context()
            ctx.input = image
            ctx.image_name = image_name
            ctx.text_regions = []
            ctx.success = False
            results.append(ctx)
            failed_count += 1
            await translator._report_progress(f"batch:{global_idx}:{global_idx}:{display_total}:{failed_count}")
        finally:
            if translated_image is not None and hasattr(translated_image, 'close'):
                try:
                    translated_image.close()
                except Exception:
                    pass
            if loaded_source_image and hasattr(image, 'close'):
                try:
                    image.close()
                except Exception:
                    pass
    
    logger.info(f"Replace translation completed: {len(results)} images processed")
    return results


class ReplaceTranslationResult:
    """替换翻译结果"""
    
    def __init__(self, 
                 success: bool = False,
                 message: str = "",
                 matched_regions: List[TextBlock] = None,
                 raw_regions: List[TextBlock] = None,
                 translated_regions: List[TextBlock] = None,
                 source_path: str = "",
                 target_path: str = "",
                 source_size: Tuple[int, int] = None,
                 target_size: Tuple[int, int] = None):
        self.success = success
        self.message = message
        self.matched_regions = matched_regions or []  # 匹配成功的区域（用于渲染）
        self.raw_regions = raw_regions or []          # 生肉图过滤后的区域（用于修复）
        self.translated_regions = translated_regions or []  # 翻译图的区域
        self.source_path = source_path  # 翻译图路径
        self.target_path = target_path  # 生肉图路径
        self.source_size = source_size  # 翻译图尺寸
        self.target_size = target_size  # 生肉图尺寸
    
    def __repr__(self):
        return f"ReplaceTranslationResult(success={self.success}, message='{self.message}', " \
               f"matched={len(self.matched_regions)}, raw={len(self.raw_regions)})"


def find_translated_image(raw_image_path: str) -> Optional[str]:
    """
    查找生肉图对应的翻译图
    
    在 manga_translator_work/translated_images/ 目录下查找同名图片
    
    Args:
        raw_image_path: 生肉图路径
        
    Returns:
        翻译图路径，如果不存在返回None
    """
    work_dir = get_work_dir(raw_image_path)
    translated_dir = os.path.join(work_dir, TRANSLATED_IMAGES_SUBDIR)
    
    if not os.path.isdir(translated_dir):
        logger.warning(f"翻译图目录不存在: {translated_dir}")
        return None
    
    # 获取生肉图的基础文件名
    raw_basename = os.path.splitext(os.path.basename(raw_image_path))[0]
    raw_ext = os.path.splitext(raw_image_path)[1].lower()
    
    # 首先尝试同扩展名
    same_ext_path = os.path.join(translated_dir, f"{raw_basename}{raw_ext}")
    if os.path.exists(same_ext_path):
        return same_ext_path
    
    # 尝试其他受支持的图片扩展名
    for ext in SUPPORTED_IMAGE_EXTENSIONS:
        translated_path = os.path.join(translated_dir, f"{raw_basename}{ext}")
        if os.path.exists(translated_path):
            return translated_path
    
    # 列出目录中的所有文件（帮助用户排查问题）
    try:
        files_in_dir = os.listdir(translated_dir)
        logger.warning(f"未找到匹配的翻译图 '{raw_basename}.*'，目录中的文件: {files_in_dir[:5]}{'...' if len(files_in_dir) > 5 else ''}")
    except Exception as e:
        logger.error(f"无法列出目录文件: {e}")
    
    return None


def filter_masks(mask_img: np.ndarray, textlines: List[Tuple[int, int, int, int]], keep_threshold: float = 1e-2) -> Tuple[List[np.ndarray], List[int]]:
    """
    过滤蒙版，只保留与文本行相关的连通组件
    
    Args:
        mask_img: 蒙版图像（二值图）
        textlines: 文本行列表 [(x, y, w, h), ...]
        keep_threshold: 保留阈值（重叠率）
        
    Returns:
        (保留的连通组件列表, 组件到文本行的分配列表)
    """
    mask_img = mask_img.copy()
    
    # 在蒙版上画出文本行边框（1像素黑线），用于分割连通区域
    for (x, y, w, h) in textlines:
        cv2.rectangle(mask_img, (x, y), (x + w, y + h), (0), 1)
    
    if len(textlines) == 0:
        return [], []
    
    # 连通组件分析
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_img)
    
    cc2textline_assignment = []
    result = []
    M = len(textlines)
    ratio_mat = np.zeros(shape=(num_labels, M), dtype=np.float32)
    dist_mat = np.zeros(shape=(num_labels, M), dtype=np.float32)
    
    for i in range(1, num_labels):  # 跳过背景(0)
        # 过滤太小的区域
        if stats[i, cv2.CC_STAT_AREA] <= 9:
            continue
        
        # 提取当前连通组件
        cc = np.zeros_like(mask_img)
        cc[labels == i] = 255
        x1, y1, w1, h1 = cv2.boundingRect(cc)
        area1 = w1 * h1
        
        # 计算与每个文本行的重叠率和距离
        for j in range(M):
            x2, y2, w2, h2 = textlines[j]
            area2 = w2 * h2
            
            # 计算重叠面积
            overlapping_area = area_overlap(x1, y1, w1, h1, x2, y2, w2, h2)
            ratio_mat[i, j] = overlapping_area / min(area1, area2) if min(area1, area2) > 0 else 0
            
            # 计算距离
            dist_mat[i, j] = rect_distance(x1, y1, x1 + w1, y1 + h1, x2, y2, x2 + w2, y2 + h2)
        
        # 找到重叠率最大的文本行
        j = np.argmax(ratio_mat[i])
        
        if ratio_mat[i, j] > keep_threshold:
            # 重叠率足够，保留
            cc2textline_assignment.append(j)
            result.append(np.copy(cc))
        else:
            # 重叠率不够，检查距离
            j = np.argmin(dist_mat[i])
            x2, y2, w2, h2 = textlines[j]
            area2 = w2 * h2
            unit = min([h1, w1, h2, w2])
            
            # 如果是小的蒙版片段且距离文本行很近，也保留
            if dist_mat[i, j] < 0.5 * unit and area1 < area2:
                cc2textline_assignment.append(j)
                result.append(np.copy(cc))
            # else: 丢弃
    
    return result, cc2textline_assignment


def area_overlap(x1, y1, w1, h1, x2, y2, w2, h2) -> float:
    """计算两个矩形的重叠面积"""
    x_overlap = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    y_overlap = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    return x_overlap * y_overlap


def rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b) -> float:
    """计算两个矩形之间的距离"""
    def dist(x1, y1, x2, y2):
        return np.sqrt((x1 - x2) * (x1 - x2) + (y1 - y2) * (y1 - y2))
    
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:  # rectangles intersect
        return 0


def get_bounding_rect(region: TextBlock) -> Tuple[float, float, float, float]:
    """
    获取TextBlock的最小外接矩形 (x, y, w, h)
    """
    if region.lines is None or len(region.lines) == 0:
        return (0, 0, 0, 0)
    
    all_points = region.lines.reshape(-1, 2)
    x_min = np.min(all_points[:, 0])
    y_min = np.min(all_points[:, 1])
    x_max = np.max(all_points[:, 0])
    y_max = np.max(all_points[:, 1])
    
    return (x_min, y_min, x_max - x_min, y_max - y_min)


def calculate_iou(rect1: Tuple[float, float, float, float], 
                  rect2: Tuple[float, float, float, float]) -> float:
    """
    计算两个矩形的重叠率（以较小框为基准）
    
    Args:
        rect1, rect2: (x, y, w, h) 格式的矩形
        
    Returns:
        重叠率 (0-1)，计算方式：交集面积 / min(area1, area2)
        这样可以更好地判断小框是否被大框包含
    """
    x1, y1, w1, h1 = rect1
    x2, y2, w2, h2 = rect2
    
    # 计算交集
    inter_x1 = max(x1, x2)
    inter_y1 = max(y1, y2)
    inter_x2 = min(x1 + w1, x2 + w2)
    inter_y2 = min(y1 + h1, y2 + h2)
    
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    
    inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    
    # 计算两个矩形的面积
    area1 = w1 * h1
    area2 = w2 * h2
    
    # 以较小的框为基准计算重叠率
    min_area = min(area1, area2)
    
    if min_area <= 0:
        return 0.0
    
    return inter_area / min_area


def scale_regions_to_target(regions: List[TextBlock], 
                            source_size: Tuple[int, int], 
                            target_size: Tuple[int, int]) -> List[TextBlock]:
    """
    将区域坐标从源图尺寸缩放到目标图尺寸
    
    Args:
        regions: 原始区域列表
        source_size: 源图尺寸 (width, height)
        target_size: 目标图尺寸 (width, height)
        
    Returns:
        缩放后的区域列表（新对象）
    """
    if source_size == target_size:
        return regions
    
    scale_x = target_size[0] / source_size[0] if source_size[0] > 0 else 1.0
    scale_y = target_size[1] / source_size[1] if source_size[1] > 0 else 1.0
    
    scaled_regions = []
    for region in regions:
        import copy
        new_region = copy.deepcopy(region)
        
        # 缩放lines坐标
        if new_region.lines is not None:
            new_region.lines[:, :, 0] *= scale_x
            new_region.lines[:, :, 1] *= scale_y
        
        # 清除缓存的属性，强制重新计算（因为lines已经改变）
        # cached_property 会在 __dict__ 中缓存结果
        for attr in ['xyxy', 'xywh', 'center', 'unrotated_polygons', 'unrotated_min_rect', 'min_rect']:
            if attr in new_region.__dict__:
                delattr(new_region, attr)
        
        # 缩放字体大小
        if new_region.font_size > 0:
            avg_scale = (scale_x + scale_y) / 2
            new_region.font_size = int(new_region.font_size * avg_scale)
        
        scaled_regions.append(new_region)
    
    return scaled_regions


def match_regions(raw_regions: List[TextBlock], 
                  translated_regions: List[TextBlock],
                  iou_threshold: float = 0.3) -> List[Tuple[int, int, float]]:
    """
    匹配生肉图和翻译图的区域 - 简化版
    
    逻辑：
    1. 计算所有生肉框和翻译框的重叠率
    2. 只要重叠率 >= 阈值，就保留翻译框
    3. 一个翻译框可以被多个生肉框匹配（多对一）
    
    Args:
        raw_regions: 生肉图区域列表
        translated_regions: 翻译图区域列表（已缩放到生肉图尺寸）
        iou_threshold: 重叠率阈值（以小框为基准）
        
    Returns:
        匹配结果列表 [(raw_idx, trans_idx, overlap_ratio), ...]
    """
    # 计算所有区域的外接矩形
    raw_rects = [get_bounding_rect(r) for r in raw_regions]
    trans_rects = [get_bounding_rect(r) for r in translated_regions]
    
    # 计算所有可能的重叠
    matches = []
    for trans_idx, trans_rect in enumerate(trans_rects):
        matched_raws = []
        for raw_idx, raw_rect in enumerate(raw_rects):
            overlap = calculate_iou(raw_rect, trans_rect)
            if overlap >= iou_threshold:
                matched_raws.append((raw_idx, overlap))
        
        # 如果这个翻译框有匹配的生肉框，保留所有匹配
        if matched_raws:
            # 为每个匹配的生肉框都创建一个匹配记录
            # 这样可以确保所有匹配的生肉框都会被修复
            for raw_idx, overlap in matched_raws:
                matches.append((raw_idx, trans_idx, overlap))
            
            # 如果有多个生肉框匹配到这个翻译框，记录日志
            if len(matched_raws) > 1:
                raw_indices = [r for r, _ in matched_raws]
                trans_text = translated_regions[trans_idx].text if hasattr(translated_regions[trans_idx], 'text') else ''
                logger.info(f"    [多对一] T{trans_idx} (文本=\"{trans_text[:20] if trans_text else ''}...\") "
                          f"匹配了 {len(matched_raws)} 个生肉框: {raw_indices}，都会被修复")
    
    # 统计未匹配的区域
    matched_trans = set(t for _, t, _ in matches)
    unmatched_trans = set(range(len(translated_regions))) - matched_trans
    
    if unmatched_trans:
        logger.warning(f"    [警告] {len(unmatched_trans)} 个翻译区域未找到匹配:")
        for trans_idx in sorted(unmatched_trans):
            trans_rect = trans_rects[trans_idx]
            trans_text = translated_regions[trans_idx].text if hasattr(translated_regions[trans_idx], 'text') else ''
            logger.warning(f"      T{trans_idx}: 位置=({trans_rect[0]:.0f},{trans_rect[1]:.0f}), "
                         f"尺寸={trans_rect[2]:.0f}x{trans_rect[3]:.0f}, "
                         f"文本=\"{trans_text[:20] if trans_text else ''}...\"")
    
    logger.info(f"    匹配结果: {len(matches)} 个翻译区域被保留 (重叠率 >= {iou_threshold}, 以小框为基准)")
    
    return matches


def merge_rects(rect1: Tuple[float, float, float, float],
                rect2: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    """
    合并两个矩形，返回包含两者的最小外接矩形
    
    Args:
        rect1, rect2: (x, y, w, h) 格式的矩形
        
    Returns:
        合并后的矩形 (x, y, w, h)
    """
    x1, y1, w1, h1 = rect1
    x2, y2, w2, h2 = rect2
    
    min_x = min(x1, x2)
    min_y = min(y1, y2)
    max_x = max(x1 + w1, x2 + w2)
    max_y = max(y1 + h1, y2 + h2)
    
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def create_matched_regions(raw_regions: List[TextBlock],
                           translated_regions: List[TextBlock],
                           matches: List[Tuple[int, int, float]]) -> Tuple[List[TextBlock], set]:
    """
    创建匹配后的区域列表 - 简化版
    
    直接使用翻译框的所有数据（框、文本、样式）
    注意：一个翻译框可能匹配多个生肉框，但只添加一次到渲染列表
    
    Args:
        raw_regions: 生肉图区域（用于记录哪些被匹配了）
        translated_regions: 翻译图区域
        matches: 匹配结果 [(raw_idx, trans_idx, overlap), ...]
        
    Returns:
        (匹配后的区域列表, 已匹配的生肉区域索引集合)
    """
    import copy
    
    matched_regions = []
    matched_raw_indices = set()
    added_trans_indices = set()  # 记录已添加的翻译框，避免重复
    
    for raw_idx, trans_idx, overlap in matches:
        # 记录生肉框索引（用于修复）
        matched_raw_indices.add(raw_idx)
        
        # 只添加翻译框一次（避免重复渲染）
        if trans_idx in added_trans_indices:
            continue
        added_trans_indices.add(trans_idx)
        
        # 直接使用翻译框的数据
        region = copy.deepcopy(translated_regions[trans_idx])
        
        # 关键修复：从 texts 数组重建带 [BR] 的 translation 字段
        # 因为渲染器使用 translation 字段来渲染文本，并且需要 [BR] 来换行
        if region.texts and len(region.texts) > 0:
            # 有 texts 数组，用 [BR] 连接（即使只有一行也保持一致）
            region.translation = "[BR]".join(region.texts)
        elif region.text:
            # 没有 texts 数组，使用 text 字段
            region.translation = region.text
        else:
            # 都没有，使用空字符串
            region.translation = ""
        
        matched_regions.append(region)
    
    return matched_regions, matched_raw_indices


def filter_raw_regions_for_inpainting(raw_regions: List[TextBlock],
                                      matched_indices: set) -> List[TextBlock]:
    """
    获取用于修复的生肉区域（只保留匹配成功的）
    
    Args:
        raw_regions: 所有生肉区域
        matched_indices: 匹配成功的索引集合
        
    Returns:
        用于修复的区域列表
    """
    return [raw_regions[i] for i in sorted(matched_indices)]


def calculate_template_alignment_offset(raw_img: np.ndarray, 
                                        translated_img: np.ndarray,
                                        template_size: int = 440) -> Tuple[int, int]:
    """
    使用模板匹配计算中日文图的对齐偏移量
    
    从中文图（翻译图）中心提取模板，在日文图（生肉图）中匹配，
    计算需要移动的水平和垂直偏移量
    
    Args:
        raw_img: 生肉图（BGR格式）
        translated_img: 翻译图（BGR格式）
        template_size: 模板大小（像素），如果为0则自动计算
        
    Returns:
        (horizontal_offset, vertical_offset)
        - horizontal_offset: 水平偏移，>0 向左移，<0 向右移
        - vertical_offset: 垂直偏移，>0 向上移，<0 向下移
    """
    try:
        # 确保图像是BGR格式
        if len(translated_img.shape) == 2:
            translated_img = cv2.cvtColor(translated_img, cv2.COLOR_GRAY2BGR)
        if len(raw_img.shape) == 2:
            raw_img = cv2.cvtColor(raw_img, cv2.COLOR_GRAY2BGR)
        
        zh, zw = translated_img.shape[:2]
        
        # 自动计算模板大小（图像短边的1/3到1/2之间）
        if template_size <= 0:
            min_side = min(zh, zw)
            template_size = min(max(min_side // 3, 200), min_side // 2)
            logger.info(f"    [对齐] 自动计算模板大小: {template_size} 像素 (图像尺寸: {zw}x{zh})")
        
        # 从翻译图中心提取模板
        cenx = zw // 2 - template_size // 2
        ceny = zh // 2 - template_size // 2
        
        # 确保模板不超出边界
        if cenx < 0 or ceny < 0 or cenx + template_size > zw or ceny + template_size > zh:
            # 自动调整模板大小
            max_template_size = min(zw, zh) - 20  # 留10像素边距
            if max_template_size < 100:
                logger.warning(f"图像尺寸 {zw}x{zh} 太小，无法进行模板匹配，使用默认偏移 (0, 0)")
                return (0, 0)
            template_size = max_template_size
            cenx = zw // 2 - template_size // 2
            ceny = zh // 2 - template_size // 2
            logger.info(f"    [对齐] 自动调整模板大小为: {template_size} 像素")
        
        muban = translated_img[ceny:ceny + template_size, cenx:cenx + template_size]
        
        # 在生肉图中匹配
        res = cv2.matchTemplate(raw_img, muban, cv2.TM_CCOEFF)
        _, _, _, max_loc = cv2.minMaxLoc(res)
        
        # 获得匹配位置
        xdist, ydist = max_loc
        
        # 计算偏移量
        horizontal_offset = cenx - xdist
        vertical_offset = ceny - ydist
        
        # 微调偏移量（与原版逻辑一致）
        if horizontal_offset < 0:
            horizontal_offset -= 3
        elif horizontal_offset > 0:
            horizontal_offset += 3
        
        if vertical_offset < 0:
            vertical_offset -= 3
        elif vertical_offset > 0:
            vertical_offset += 3
        
        logger.info(f"    [对齐] 模板匹配完成: 水平偏移={horizontal_offset}, 垂直偏移={vertical_offset}")
        
        return (horizontal_offset, vertical_offset)
        
    except Exception as e:
        logger.error(f"    [对齐] 模板匹配失败: {e}")
        traceback.print_exc()
        return (0, 0)


# 导出的函数和类
__all__ = [
    'ReplaceTranslationResult',
    'find_translated_image',
    'get_bounding_rect',
    'calculate_iou',
    'scale_regions_to_target',
    'match_regions',
    'create_matched_regions',
    'filter_raw_regions_for_inpainting',
    'get_text_to_img_solid_ink',
]


# ============================================================================
# 以下是 win.py 版本的蒙版精炼函数（用于 inpainter=none 模式）
# ============================================================================

def _refine_mask_winpy(rgbimg, rawmask):
    """
    win.py 版本的 refine_mask 函数
    使用 DenseCRF 优化蒙版边缘
    """
    try:
        import pydensecrf.densecrf as dcrf
        from pydensecrf.utils import unary_from_softmax
        
        if len(rawmask.shape) == 2:
            rawmask = rawmask[:, :, None]
        mask_softmax = np.concatenate([cv2.bitwise_not(rawmask)[:, :, None], rawmask], axis=2)
        mask_softmax = mask_softmax.astype(np.float32) / 255.0
        n_classes = 2
        feat_first = mask_softmax.transpose((2, 0, 1)).reshape((n_classes, -1))
        unary = unary_from_softmax(feat_first)
        unary = np.ascontiguousarray(unary)

        d = dcrf.DenseCRF2D(rgbimg.shape[1], rgbimg.shape[0], n_classes)

        d.setUnaryEnergy(unary)
        d.addPairwiseGaussian(sxy=1, compat=3, kernel=dcrf.DIAG_KERNEL,
                              normalization=dcrf.NO_NORMALIZATION)

        d.addPairwiseBilateral(sxy=23, srgb=7, rgbim=rgbimg,
                               compat=20,
                               kernel=dcrf.DIAG_KERNEL,
                               normalization=dcrf.NO_NORMALIZATION)
        Q = d.inference(5)
        res = np.argmax(Q, axis=0).reshape((rgbimg.shape[0], rgbimg.shape[1]))
        crf_mask = np.array(res * 255, dtype=np.uint8)
        return crf_mask
    except ImportError:
        logger.warning("pydensecrf 未安装，跳过 CRF 优化")
        return rawmask


def _complete_mask_winpy(img_np: np.ndarray, ccs: List[np.ndarray], textlines: List[Tuple[int, int, int, int]], cc2textline_assignment):
    """
    win.py 版本的 complete_mask 函数
    完成蒙版精炼
    """
    from tqdm import tqdm
    
    if len(ccs) == 0:
        return None
    textline_ccs = [np.zeros_like(ccs[0]) for _ in range(len(textlines))]
    for i, cc in enumerate(ccs):
        txtline = cc2textline_assignment[i]
        textline_ccs[txtline] = cv2.bitwise_or(textline_ccs[txtline], cc)
    final_mask = np.zeros_like(ccs[0])
    img_np = cv2.bilateralFilter(img_np, 17, 80, 80)
    for i, cc in enumerate(tqdm(textline_ccs, '[mask]', disable=True)):
        x1, y1, w1, h1 = cv2.boundingRect(cc)
        text_size = min(w1, h1)
        extend_size = int(text_size * 0.1)
        x1 = max(x1 - extend_size, 0)
        y1 = max(y1 - extend_size, 0)
        w1 += extend_size * 2
        h1 += extend_size * 2
        w1 = min(w1, img_np.shape[1] - x1 - 1)
        h1 = min(h1, img_np.shape[0] - y1 - 1)
        dilate_size = max((int(text_size * 0.3) // 2) * 2 + 1, 3)
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_size, dilate_size))
        cc_region = np.ascontiguousarray(cc[y1: y1 + h1, x1: x1 + w1])
        if cc_region.size == 0:
            continue
        img_region = np.ascontiguousarray(img_np[y1: y1 + h1, x1: x1 + w1])
        cc_region = _refine_mask_winpy(img_region, cc_region)
        cc[y1: y1 + h1, x1: x1 + w1] = cc_region
        cc = cv2.dilate(cc, kern)
        final_mask = cv2.bitwise_or(final_mask, cc)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.dilate(final_mask, kern)
