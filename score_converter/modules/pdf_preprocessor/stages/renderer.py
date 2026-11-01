"""
文件: renderer.py
用途: 将PDF页面渲染为灰度图像，支持DPI调整和尺寸限制，根据PDF类型（文字型/扫描型）选择不同的文字清理策略
调用方: pipeline.py 预处理管线
输入: PDF页面对象（fitz.Page）和渲染参数
输出: 灰度PNG图像（numpy数组或PIL Image）
依赖: PyMuPDF(fitz), numpy, PIL
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from typing import Optional

import fitz
import numpy as np
from PIL import Image

from .pdf_type_detector import PDFType

logger = logging.getLogger(__name__)


class PDFRenderer:
    """PDF页面渲染器"""
    
    def __init__(self, target_dpi: int = 300, max_pixels: int = 20_000_000,
                 clean_text: bool = True):
        """
        Args:
            target_dpi: 目标DPI（200-500）
            max_pixels: 最大像素数限制
            clean_text: 是否清理文字层（去除重叠/乱码文字）
        """
        if not (200 <= target_dpi <= 500):
            raise ValueError(f"DPI必须在200-500之间，当前值: {target_dpi}")
        
        self.target_dpi = target_dpi
        self.max_pixels = max_pixels
        self.clean_text = clean_text
        
        logger.info(f"初始化渲染器: DPI={target_dpi}, 最大像素={max_pixels:,}, 文字清理={clean_text}")
    
    def render(self, page: fitz.Page, page_type: PDFType = PDFType.VECTOR) -> Image.Image:
        """渲染PDF页面为灰度图像
        
        根据PDF类型选择不同的文字清理策略：
        - 文字型PDF：使用文字块信息精确清理
        - 扫描型PDF：跳过文字清理，使用谱线检测裁剪顶部区域
        
        Args:
            page: PyMuPDF页面对象
            page_type: PDF页面类型
            
        Returns:
            PIL.Image灰度图像
        """
        scale = self.target_dpi / 72
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert('L')
        
        # 自适应DPI：检测谱线间距，不足则重新渲染
        img, pix, scale = self._adaptive_dpi(page, img, pix, page.rect, scale)
        
        # 轻预增强：让淡谱线变黑，确保后续裁剪和Audiveris能检测到
        # 扫描型PDF谱线像素值常在100-120之间，Audiveris内部阈值约100
        img = self._light_pre_enhance(img)
        
        # 文字层清理（根据PDF类型选择策略）
        if self.clean_text:
            img = self._clean_text_by_type(page, img, pix, page.rect, scale, page_type)
        
        # 裁剪顶部文字区域
        img = self._crop_top_text(page, img, pix, page.rect, scale, page_type)
        
        img = self._resize_to_limit(img)
        
        logger.debug(f"渲染完成: {img.width}x{img.height}, 像素={img.width * img.height:,}")
        return img
    
    def _clean_text_by_type(self, page: fitz.Page, img: Image.Image,
                            pix: fitz.Pixmap, page_rect: fitz.Rect,
                            scale: float, page_type: PDFType) -> Image.Image:
        """根据PDF类型选择文字清理策略
        
        Args:
            page: PyMuPDF页面对象
            img: 已渲染的灰度图像
            pix: 渲染的pixmap
            page_rect: 页面原始尺寸
            scale: 渲染缩放比例
            page_type: PDF页面类型
            
        Returns:
            清理后的图像
        """
        if page_type == PDFType.SCANNED:
            # 扫描型PDF：跳过文字清理（图像中没有可识别的文字块）
            logger.debug("扫描型PDF，跳过文字层清理")
            return img
        
        # 文字型PDF：使用文字块信息精确清理
        return self._clean_overlapping_text(page, img, pix, page_rect, scale)
    
    def _crop_top_text(self, page: fitz.Page, img: Image.Image,
                       pix: fitz.Pixmap, page_rect: fitz.Rect,
                       scale: float, page_type: PDFType) -> Image.Image:
        """裁剪顶部文字区域
        
        根据PDF类型选择不同的裁剪策略：
        - 文字型PDF：使用文字块信息精确定位
        - 扫描型PDF：使用谱线检测定位
        
        Args:
            page: PyMuPDF页面对象
            img: 已渲染的灰度图像
            pix: 渲染的pixmap
            page_rect: 页面原始尺寸
            scale: 渲染缩放比例
            page_type: PDF页面类型
            
        Returns:
            裁剪后的图像
        """
        try:
            if page_type == PDFType.VECTOR:
                # 文字型PDF：使用文字块定位
                blocks = page.get_text("blocks")
                if blocks:
                    return self._crop_by_text_blocks(blocks, img, pix, page_rect)
            
            # 扫描型PDF 或 文字型PDF无文字块：使用谱线检测
            return self._crop_by_staff_detection(img)
            
        except Exception as e:
            logger.warning(f"文字区域裁剪失败: {e}")
        
        return img
    
    def _crop_by_text_blocks(self, blocks, img: Image.Image,
                              pix: fitz.Pixmap, page_rect: fitz.Rect) -> Image.Image:
        """基于PDF文字块裁剪（文字型PDF）"""
        page_h = page_rect.height
        lowest_text_y1 = page_h
        found_text = False
        
        for block in blocks:
            x0, y0, x1, y1 = block[:4]
            text = block[4] if len(block) > 4 else ""
            if not text.strip():
                continue
            # 只考虑页面上半部分的文字（PDF y > 55% 页面高度）
            if y0 > page_h * 0.55:
                if y1 < lowest_text_y1:
                    lowest_text_y1 = y1
                found_text = True
        
        if not found_text:
            return img
        
        # 转换为图像坐标（翻转y轴）+ 留15px边距
        sy = pix.height / page_h
        img_y = int((page_h - lowest_text_y1) * sy) + 15
        
        if img_y > 50 and img_y < pix.height * 0.5:
            # 裁剪掉顶部文字区域，保留 img_y 以下的乐谱内容
            cropped = img.crop((0, img_y, img.width, img.height))
            logger.info(f"裁剪顶部文字区域(文字块): 从 y={img_y} 开始保留")
            return cropped
        
        return img
    
    def _crop_by_staff_detection(self, img: Image.Image) -> Image.Image:
        """基于谱线检测裁剪（扫描图片PDF）
        
        扫描型PDF需要裁剪掉大段描述文字，只保留标题、速度标记和乐谱。
        原因：
        1. 大段描述文字会干扰Audiveris的布局分析
        2. 页面太高会导致Audiveris无法正确检测系统分隔
        
        策略：找到第一条谱线，往上只留五线谱本身+谱号/调号/拍号/速度标记的空间。
        标题和速度标记保留（Audiveris能正确处理），大段描述文字裁掉。
        """
        arr = np.array(img)
        h, w = arr.shape
        
        # 找所有谱线位置（用于计算间距）
        # 扫描型PDF谱线可能较淡（像素值100-120），使用阈值150
        staff_lines = []
        for y in range(0, h):
            row = arr[y, :]
            dark_pixels = row < 150
            max_run = 0
            current_run = 0
            for pixel in dark_pixels:
                if pixel:
                    current_run += 1
                    max_run = max(max_run, current_run)
                else:
                    current_run = 0
            if max_run > w * 0.4:
                # 检查是否和上一条线太近（可能是同一条线的厚度）
                # 最小间距15px：300DPI下谱线厚度约2-3px，15px足够排除重复
                if not staff_lines or y - staff_lines[-1] > 15:
                    staff_lines.append(y)
        
        if not staff_lines:
            logger.debug("扫描型PDF：未找到谱线，保留完整页面")
            return img
        
        first_staff_y = staff_lines[0]
        
        # 计算五线谱内间距（取众数，排除系统间隔）
        staff_spacing = self._calculate_staff_spacing(staff_lines, img_height=h)
        
        # 往上留的空间：必须保留谱号/调号/拍号区域
        # 策略：从第一条谱线往上保留足够空间（至少200px或3倍间距）
        # 谱号/调号/拍号通常在五线谱左侧，高度与五线谱相当
        if staff_spacing > 0:
            # 保留空间 = 3倍谱线间距 或 200px，取较大值
            preserve_space = max(int(staff_spacing * 3), 200)
            crop_start = max(0, first_staff_y - preserve_space)
        else:
            # 无法检测间距时，保守保留200px
            crop_start = max(0, first_staff_y - 200)
        
        if crop_start > 30:
            cropped = img.crop((0, crop_start, img.width, img.height))
            logger.info(f"裁剪顶部文字区域(谱线检测): 从 y={crop_start} 开始保留 "
                       f"(第一条谱线 y={first_staff_y}, 谱线间距={staff_spacing:.0f}px)")
            return cropped
        
        return img
    
    def _light_pre_enhance(self, img: Image.Image) -> Image.Image:
        """轻预增强：仅在谱线偏淡时才增强
        
        先检测谱线区域的平均像素值：
        - 平均值 > 100：谱线偏淡（像素值100-120），需要增强
        - 平均值 <= 100：谱线已经够黑，跳过增强（避免破坏干净PDF）
        
        增强策略（轻量级，不影响音符细节）：
        1. 分段线性映射：暗部（<120）压缩到0-60，让谱线变黑
        2. 整体对比度小幅提升（factor=1.2）
        3. 不做模糊（保护细小音符）
        
        Args:
            img: 输入灰度图像
            
        Returns:
            增强后的图像（或原图，如果谱线已经够黑）
        """
        arr = np.array(img)
        
        # 检测谱线是否偏淡：采样页面中部几条垂直线，找暗像素的平均值
        h, w = arr.shape
        sample_cols = [w // 4, w // 2, 3 * w // 4]
        dark_pixel_values = []
        for col in sample_cols:
            column = arr[:, col]
            dark_pixels = column[column < 150]  # 暗像素（可能是谱线）
            if len(dark_pixels) > 0:
                dark_pixel_values.extend(dark_pixels.tolist())
        
        if not dark_pixel_values:
            logger.debug("轻预增强：未检测到暗像素，跳过")
            return img
        
        avg_dark = np.mean(dark_pixel_values)
        
        # 只有谱线偏淡（平均值 > 100）时才增强
        if avg_dark <= 100:
            logger.debug(f"轻预增强：谱线已够黑（平均像素值={avg_dark:.0f}），跳过增强")
            return img
        
        logger.info(f"轻预增强：谱线偏淡（平均像素值={avg_dark:.0f}），执行增强")
        arr = arr.astype(np.float32)
        
        # 分段线性映射：暗部压暗，亮部保持
        threshold = 120
        dark_target = 60
        arr = np.where(
            arr < threshold,
            arr * (dark_target / threshold),
            arr
        )
        
        # 整体对比度小幅提升
        factor = 1.2
        arr = (arr - 128) * factor + 128
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        
        logger.debug(f"轻预增强完成: dark_threshold={threshold}, contrast={factor}")
        return Image.fromarray(arr)
    
    def _calculate_staff_spacing(self, staff_lines: list, img_height: int = 0) -> float:
        """计算五线谱内的谱线间距（取众数）
        
        五线谱由5条线组成，线间距取决于DPI和页面尺寸。
        标准A4在300DPI下约20px，但大页面PDF可能达到70-80px。
        系统之间的间隔远大于线间距。
        通过取间距的众数，可以排除系统间隔的干扰。
        
        Args:
            staff_lines: 所有谱线的Y坐标列表
            img_height: 图像高度（用于自适应间距上限）
            
        Returns:
            五线谱内间距（像素），无法计算时返回 0
        """
        if len(staff_lines) < 2:
            return 0.0
        
        # 计算所有相邻谱线的间距
        spacings = [staff_lines[i] - staff_lines[i-1] for i in range(1, len(staff_lines))]
        
        if not spacings:
            return 0.0
        
        # 自适应间距上限：标准A4/300DPI约20px，大页面可能到100px
        # 用图像高度的比例来估算：五线谱（5线4间距）约占页面高度的1/8~1/6
        # 所以单条间距约 img_height / (8*4) ~ img_height / (6*4)
        if img_height > 0:
            max_spacing = int(img_height / 32)  # 保守上限
            max_spacing = max(max_spacing, 50)   # 至少50px
            max_spacing = min(max_spacing, 150)  # 不超过150px
        else:
            max_spacing = 50  # 默认上限（兼容旧调用）
        
        # 过滤：间距必须 >= 5px（排除同一条线的重复检测），且 <= max_spacing（排除系统间隔）
        valid_spacings = [s for s in spacings if 5 <= s <= max_spacing]
        
        if not valid_spacings:
            return 0.0
        
        # 取众数（最常见的间距）
        from collections import Counter
        counter = Counter(valid_spacings)
        mode_spacing = counter.most_common(1)[0][0]
        
        return float(mode_spacing)
    
    def _measure_staff_spacing_near(self, arr: np.ndarray, start_y: int, w: int) -> float:
        """从指定位置开始测量谱线间距
        
        策略：先跳过第一条谱线的厚度，再找第二条谱线，计算两者间距。
        避免把同一条谱线的厚度误判为间距。
        
        Args:
            arr: 图像数组
            start_y: 起始Y坐标（第一条谱线位置）
            w: 图像宽度
            
        Returns:
            谱线间距（像素），无法测量时返回 0
        """
        h = arr.shape[0]
        
        # 步骤1：跳过第一条谱线的厚度
        # 从start_y开始，找到谱线结束的位置（连续暗像素行变成不连续）
        first_line_bottom = start_y
        for y in range(start_y, min(start_y + 30, h)):
            row = arr[y, :]
            dark_pixels = row < 100
            max_run = 0
            current_run = 0
            for pixel in dark_pixels:
                if pixel:
                    current_run += 1
                    max_run = max(max_run, current_run)
                else:
                    current_run = 0
            if max_run > w * 0.4:
                first_line_bottom = y
            else:
                # 如果已经连续5行不是谱线，说明已经跳过第一条线
                if y - first_line_bottom > 5:
                    break
        
        # 步骤2：从第一条线底部往下，找第二条谱线
        search_start = first_line_bottom + 3  # 留3px缓冲
        for y in range(search_start, min(search_start + 60, h)):
            row = arr[y, :]
            dark_pixels = row < 100
            max_run = 0
            current_run = 0
            for pixel in dark_pixels:
                if pixel:
                    current_run += 1
                    max_run = max(max_run, current_run)
                else:
                    current_run = 0
            if max_run > w * 0.4:
                spacing = float(y - start_y)
                # 合理性检查：谱线间距通常在10-50px范围（300DPI下约20px）
                if spacing >= 8:
                    return spacing
                else:
                    # 间距太小，可能是噪声，继续往下找
                    continue
        return 0.0
    
    def _measure_staff_spacing(self, img: Image.Image) -> float:
        """测量图像中的谱线间距
        
        通过检测水平方向的黑色像素周期性来估算谱线间距。
        
        Args:
            img: 灰度图像
            
        Returns:
            估算的谱线间距（像素），无法检测时返回 0
        """
        arr = np.array(img)
        h, w = arr.shape
        
        # 取多条垂直线采样，找黑色像素的周期性
        sample_cols = [w // 4, w // 2, 3 * w // 4]
        spacings = []
        
        for col in sample_cols:
            column = arr[:, col]
            # 找到黑色像素位置（谱线）
            dark_positions = np.where(column < 100)[0]
            if len(dark_positions) < 10:
                continue
            
            # 计算相邻黑色像素的间距
            gaps = np.diff(dark_positions)
            # 自适应间距范围：大页面PDF谱线间距可能到100px
            max_gap = max(50, int(h / 32))
            valid_gaps = gaps[(gaps >= 3) & (gaps <= max_gap)]
            if len(valid_gaps) > 0:
                # 取众数（最常见的间距）
                from scipy import stats
                mode_result = stats.mode(valid_gaps, keepdims=False)
                spacings.append(mode_result.mode)
        
        if spacings:
            return float(np.median(spacings))
        return 0.0
    
    def _adaptive_dpi(self, page: fitz.Page, img: Image.Image,
                       pix: fitz.Pixmap, page_rect: fitz.Rect,
                       scale: float):
        """自适应DPI：谱线间距不足时提高DPI重新渲染
        
        Audiveris 推荐谱线间距 ~20px。如果当前间距 < 15px，
        按比例提高DPI重新渲染。
        
        Args:
            page: PyMuPDF页面对象
            img: 当前渲染的图像
            pix: 当前pixmap
            page_rect: 页面原始尺寸
            scale: 当前缩放比例
            
        Returns:
            (img, pix, scale) 调整后的图像、pixmap和缩放比例
        """
        TARGET_SPACING = 20  # Audiveris 推荐谱线间距
        MIN_SPACING = 15     # 低于此值需要提高DPI
        
        spacing = self._measure_staff_spacing(img)
        
        if spacing > 0 and spacing < MIN_SPACING:
            # 计算需要的DPI倍数
            dpi_factor = TARGET_SPACING / spacing
            new_dpi = int(self.target_dpi * dpi_factor)
            # 限制最大DPI
            new_dpi = min(new_dpi, 600)
            
            if new_dpi > self.target_dpi:
                logger.info(f"谱线间距 {spacing:.1f}px 不足，提高DPI {self.target_dpi}→{new_dpi}")
                scale = new_dpi / 72
                mat = fitz.Matrix(scale, scale)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert('L')
                # 文字清理和裁剪在主流程中统一执行（使用最终分辨率）
        
        return img, pix, scale
    
    def _clean_overlapping_text(self, page: fitz.Page, img: Image.Image,
                                 pix: fitz.Pixmap, page_rect: fitz.Rect,
                                 scale: float) -> Image.Image:
        """清理文字层（为 OMR 优化）— 仅用于文字型PDF
        
        核心思路：OMR 只需要谱线和音符，所有文字都是噪声。
        
        策略：
        1. 获取所有文字块位置
        2. 检测重叠对（重叠面积 > 30%）→ 肯定是垃圾文字，直接清理
        3. 页面顶部 35% 和底部 5% 的文字 → 标题/页码/版权，直接清理
        4. 保护机制：如果文字块与乐谱符号重叠（黑色像素 > 5%），跳过
           （避免误删力度记号 p/f/mp 等）
        
        Args:
            page: PyMuPDF页面对象
            img: 已渲染的灰度图像
            pix: 渲染的pixmap（用于坐标转换）
            page_rect: 页面原始尺寸
            scale: 渲染缩放比例
            
        Returns:
            清理后的图像
        """
        try:
            blocks = page.get_text("blocks")
            if not blocks:
                return img
            
            # 转换为图像坐标系的文字块矩形
            sx = pix.width / page_rect.width
            sy = pix.height / page_rect.height
            
            text_rects = []
            for block in blocks:
                x0, y0, x1, y1 = block[:4]
                text = block[4] if len(block) > 4 else ""
                if not text.strip():
                    continue
                text_rects.append((
                    int(x0 * sx), int(y0 * sy),
                    int(x1 * sx), int(y1 * sy),
                    text
                ))
            
            if not text_rects:
                return img
            
            # 标记需要清理的文字块索引
            clean_indices = set()
            
            # 策略1: 检测重叠文字对（阈值 30%）
            for i in range(len(text_rects)):
                for j in range(i + 1, len(text_rects)):
                    ix0, iy0, ix1, iy1, _ = text_rects[i]
                    jx0, jy0, jx1, jy1, _ = text_rects[j]
                    
                    ox0, oy0 = max(ix0, jx0), max(iy0, jy0)
                    ox1, oy1 = min(ix1, jx1), min(iy1, jy1)
                    if ox1 <= ox0 or oy1 <= oy0:
                        continue
                    
                    overlap_area = (ox1 - ox0) * (oy1 - oy0)
                    area_i = (ix1 - ix0) * (iy1 - iy0)
                    area_j = (jx1 - jx0) * (jy1 - jy0)
                    min_area = min(area_i, area_j)
                    
                    if min_area > 0 and overlap_area > min_area * 0.3:
                        # 标记面积较小的
                        if area_i <= area_j:
                            clean_indices.add(i)
                        else:
                            clean_indices.add(j)
            
            # 策略2: 页面顶部 35% 和底部 5% 的文字 → 直接清理
            page_height = pix.height
            top_threshold = page_height * 0.35
            bottom_threshold = page_height * 0.95
            
            for idx, (ix0, iy0, ix1, iy1, _) in enumerate(text_rects):
                if iy0 < top_threshold or iy1 > bottom_threshold:
                    clean_indices.add(idx)
            
            if not clean_indices:
                return img
            
            # 执行清理
            arr = np.array(img)
            cleaned = 0
            skipped = 0
            
            for idx in clean_indices:
                ix0, iy0, ix1, iy1, _ = text_rects[idx]
                region = arr[iy0:iy1, ix0:ix1]
                if region.size == 0:
                    continue
                
                # 保护机制：检查是否与乐谱符号重叠
                black_ratio = np.sum(region < 50) / region.size
                if black_ratio > 0.05:
                    skipped += 1  # 与乐谱重叠，跳过
                    continue
                
                # 用周围背景色填充
                margin = 3
                samples = []
                for dy in range(-margin, margin + 1):
                    for dx in range(-margin, margin + 1):
                        if dx == 0 and dy == 0:
                            continue
                        ny, nx = iy0 + dy, ix0 + dx
                        if 0 <= ny < arr.shape[0] and 0 <= nx < arr.shape[1]:
                            samples.append(arr[ny, nx])
                
                fill = int(np.median(samples)) if samples else 255
                arr[iy0:iy1, ix0:ix1] = fill
                cleaned += 1
            
            if cleaned or skipped:
                logger.info(f"文字清理: 清理 {cleaned}, 保护 {skipped} (与乐谱重叠)")
            
            return Image.fromarray(arr)
            
        except Exception as e:
            logger.warning(f"文字清理失败: {e}")
            return img
    
    def _resize_to_limit(self, img: Image.Image) -> Image.Image:
        """调整尺寸以确保不超过像素限制
        
        Args:
            img: 输入图像
            
        Returns:
            调整后的图像
        """
        total_pixels = img.width * img.height
        
        if total_pixels > self.max_pixels:
            ratio = (self.max_pixels / total_pixels) ** 0.5
            new_width = int(img.width * ratio)
            new_height = int(img.height * ratio)
            
            # 确保是偶数
            new_width = new_width - (new_width % 2)
            new_height = new_height - (new_height % 2)
            
            img = img.resize((new_width, new_height), Image.LANCZOS)
            logger.info(f"尺寸调整: {new_width}x{new_height} (原{img.width}x{img.height})")
        
        return img
