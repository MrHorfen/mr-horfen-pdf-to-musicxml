"""
文件: pdf_parser.py
用途: PDF预处理与图像提取模块，自动判别扫描版/数字版类型，输出标准化乐谱图像矩阵
调用方: main.py
输入: PDF乐谱文件
输出: 标准化乐谱图像矩阵
依赖: PyMuPDF(fitz), cv2, numpy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import logging
import tempfile
from typing import List, Tuple, Dict, Optional, Any
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageEnhance

from config import (
    get_config,
    setup_logging,
    ImageProcessingConfig,
    INSTRUMENTS,
)

logger = setup_logging(__name__)


@dataclass
class StaffRegion:
    """五线谱区域描述"""
    bbox: Tuple[int, int, int, int]  # (x, y, w, h)
    staff_lines: List[int] = field(default_factory=list)
    confidence: float = 1.0
    page_index: int = 0
    region_index: int = 0


@dataclass
class ProcessedPage:
    """处理后页面数据"""
    page_index: int
    pdf_type: str
    image: np.ndarray
    width: int
    height: int
    dpi: int
    staff_regions: List[StaffRegion] = field(default_factory=list)
    deskew_angle: float = 0.0


class PDFParser:
    """PDF乐谱解析器 - 支持扫描版与数字版双模PDF输入"""

    def __init__(self, config: Optional[ImageProcessingConfig] = None):
        self.config = config or get_config().image
        self.pages: List[ProcessedPage] = []
        self.pdf_type: str = "unknown"

    def detect_pdf_type(self, pdf_bytes: bytes) -> str:
        """检测PDF类型：扫描版(仅含图像) vs 数字版(含文本/矢量图形)

        Args:
            pdf_bytes: PDF文件的原始字节数据

        Returns:
            字符串："scanned" 或 "digital"
        """
        text_marker = b"/Type/Page" in pdf_bytes
        image_only = pdf_bytes.count(b"/Subtype/Image") / max(pdf_bytes.count(b"/Type/Page"), 1)

        if image_only > 0.8 and text_marker:
            logger.info("PDF类型检测：扫描版（主要包含图像数据）")
            return "scanned"
        else:
            logger.info("PDF类型检测：数字版（包含文本或矢量图形数据）")
            return "digital"

    def load_pdf(self, pdf_path: str, dpi: Optional[int] = None) -> List[ProcessedPage]:
        """加载PDF并执行预处理Pipeline

        Args:
            pdf_path: PDF文件路径
            dpi: 扫描版渲染DPI（默认300）

        Returns:
            处理后页面列表
        """
        dpi = dpi or self.config.default_dpi
        self.pages = []

        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF文件不存在: {pdf_path}")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        self.pdf_type = self.detect_pdf_type(pdf_bytes)

        if self.pdf_type == "scanned":
            self.pages = self._process_scanned_pdf(pdf_path, dpi)
        else:
            self.pages = self._process_digital_pdf(pdf_path, dpi)

        logger.info(f"PDF加载完成，共 {len(self.pages)} 页，类型: {self.pdf_type}")
        return self.pages

    def _process_scanned_pdf(self, pdf_path: str, dpi: int) -> List[ProcessedPage]:
        """处理扫描版PDF：栅格化→去噪→二值化→倾斜校正→五线谱检测

        Args:
            pdf_path: PDF文件路径
            dpi: 渲染分辨率

        Returns:
            处理后页面列表
        """
        pages = []
        try:
            from pdf2image import convert_from_path

            pil_images = convert_from_path(pdf_path, dpi=dpi, grayscale=True)

            for idx, pil_img in enumerate(pil_images):
                img_array = np.array(pil_img)
                logger.debug(f"  第{idx+1}页: 原始尺寸 {img_array.shape}")

                denoised = self._denoise_image(img_array)
                binary = self._binarize_image(denoised)
                corrected, angle = self._deskew_image(binary)
                staff_regions = self._detect_staff_regions(corrected)

                page = ProcessedPage(
                    page_index=idx,
                    pdf_type="scanned",
                    image=corrected,
                    width=corrected.shape[1],
                    height=corrected.shape[0],
                    dpi=dpi,
                    staff_regions=staff_regions,
                    deskew_angle=angle,
                )
                pages.append(page)
                logger.info(f"  第{idx+1}页处理完成: 检测到 {len(staff_regions)} 个五线谱区域")

        except ImportError:
            logger.error("pdf2image 库未安装，请执行: pip install pdf2image")
            logger.error("还需要安装 Poppler: https://github.com/oschwartz10612/poppler-windows/releases/")
            raise
        except Exception as e:
            logger.error(f"扫描版PDF处理异常: {e}")
            raise

        return pages

    def _process_digital_pdf(self, pdf_path: str, dpi: int) -> List[ProcessedPage]:
        """处理数字版PDF：提取页面坐标→定位五线谱行→区域分割

        Args:
            pdf_path: PDF文件路径
            dpi: 备用渲染分辨率

        Returns:
            处理后页面列表
        """
        pages = []
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    pil_img = page.to_image(resolution=dpi)
                    img = pil_img.original
                    img_array = np.array(img.convert("L"))

                    binary = self._binarize_image(img_array)
                    staff_regions = self._detect_staff_regions(binary)

                    processed = ProcessedPage(
                        page_index=page_idx,
                        pdf_type="digital",
                        image=binary,
                        width=binary.shape[1],
                        height=binary.shape[0],
                        dpi=dpi,
                        staff_regions=staff_regions,
                    )
                    pages.append(processed)
                    logger.info(f"  第{page_idx+1}页: {len(staff_regions)} 个五线谱区域")

        except ImportError:
            logger.warning("pdfplumber 不可用，回退到扫描版处理模式")
            return self._process_scanned_pdf(pdf_path, dpi)
        except Exception as e:
            logger.warning(f"数字版PDF处理异常 ({e})，回退到扫描版处理模式")
            return self._process_scanned_pdf(pdf_path, dpi)

        return pages

    def _denoise_image(self, image: np.ndarray) -> np.ndarray:
        """图像去噪：使用非局部均值去噪算法去除噪点

        Args:
            image: 输入灰度图像

        Returns:
            去噪后的图像
        """
        try:
            import cv2
            strength = self.config.denoise_strength
            denoised = cv2.fastNlMeansDenoising(image, None, strength, 7, 21)
            logger.debug(f"  去噪完成 (强度={strength})")
            return denoised
        except ImportError:
            return image

    def _binarize_image(self, image: np.ndarray) -> np.ndarray:
        """图像二值化：自适应阈值 + OTSU双策略

        Args:
            image: 输入灰度图像

        Returns:
            二值化图像
        """
        try:
            import cv2
            block_size = self.config.adaptive_block_size
            if block_size % 2 == 0:
                block_size += 1

            binary = cv2.adaptiveThreshold(
                image,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                block_size,
                self.config.adaptive_c,
            )
            logger.debug("  二值化完成 (自适应阈值)")
            return binary
        except ImportError:
            threshold = self.config.binary_threshold
            binary = np.where(image > threshold, 255, 0).astype(np.uint8)
            return binary

    def _deskew_image(self, image: np.ndarray) -> Tuple[np.ndarray, float]:
        """倾斜校正：检测乐谱图像倾斜角度并旋转校正

        Args:
            image: 输入二值化图像

        Returns:
            (校正后图像, 检测到的倾斜角度)
        """
        try:
            import cv2

            coords = np.column_stack(np.where(image == 0))
            if len(coords) < 100:
                return image, 0.0

            angle_range = self.config.deskew_angle_range
            center = (image.shape[1] // 2, image.shape[0] // 2)

            min_angle = -angle_range
            max_angle = angle_range
            best_angle = 0.0
            best_score = float("inf")

            for angle in np.linspace(min_angle, max_angle, 20):
                rot_mat = cv2.getRotationMatrix2D(center, angle, 1.0)
                rotated = cv2.warpAffine(image, rot_mat, (image.shape[1], image.shape[0]))
                proj = np.sum(rotated == 0, axis=1)
                score = -np.max(proj)
                if score < best_score:
                    best_score = score
                    best_angle = angle

            if abs(best_angle) > 0.1:
                rot_mat = cv2.getRotationMatrix2D(center, best_angle, 1.0)
                corrected = cv2.warpAffine(
                    image, rot_mat, (image.shape[1], image.shape[0]),
                    borderValue=255
                )
                logger.info(f"  倾斜校正: {best_angle:.2f}°")
                return corrected, best_angle

            return image, 0.0
        except ImportError:
            return image, 0.0

    def _detect_staff_regions(self, image: np.ndarray) -> List[StaffRegion]:
        """检测五线谱区域：基于水平投影的五线谱行定位

        Args:
            image: 二值化后的乐谱图像

        Returns:
            五线谱区域列表
        """
        try:
            import cv2

            horizontal_projection = np.sum(image == 0, axis=1)
            height = image.shape[0]
            staff_lines = self._find_staff_line_positions(horizontal_projection)

            regions = []
            for i, lines in enumerate(staff_lines):
                if len(lines) >= 5:
                    top = max(0, lines[0] - 20)
                    bottom = min(height, lines[-1] + 20)
                    region = StaffRegion(
                        bbox=(0, top, image.shape[1], bottom - top),
                        staff_lines=lines[:5],
                        region_index=i,
                    )
                    regions.append(region)

            logger.debug(f"  检测到 {len(regions)} 个五线谱区域")
            return regions
        except ImportError:
            return []

    def _find_staff_line_positions(self, projection: np.ndarray) -> List[List[int]]:
        """从水平投影中找到五线谱行位置

        Args:
            projection: 水平投影数组（每行的黑色像素数）

        Returns:
            五线谱行位置列表，每个元素为5条线的y坐标
        """
        threshold = np.mean(projection) * 1.5
        peaks = []
        line_positions = []

        for i in range(1, len(projection) - 1):
            if (projection[i] > threshold and
                    projection[i] >= projection[i - 1] and
                    projection[i] >= projection[i + 1]):
                peaks.append(i)

        spacing = self.config.staff_spacing_max

        current_group = [peaks[0]] if peaks else []
        for i in range(1, len(peaks)):
            if peaks[i] - peaks[i - 1] < spacing:
                current_group.append(peaks[i])
            else:
                if 4 <= len(current_group) <= 8:
                    sorted_group = sorted(current_group)
                    if len(sorted_group) >= 5:
                        line_positions.append(sorted_group[:5])
                current_group = [peaks[i]]

        if 4 <= len(current_group) <= 8:
            sorted_group = sorted(current_group)
            if len(sorted_group) >= 5:
                line_positions.append(sorted_group[:5])

        return line_positions

    def get_page_images(self) -> List[np.ndarray]:
        """获取所有已处理页面的图像数据

        Returns:
            页面图像列表
        """
        return [page.image for page in self.pages]

    def get_staff_regions(self, page_index: int) -> List[StaffRegion]:
        """获取指定页面的五线谱区域

        Args:
            page_index: 页面索引（从0开始）

        Returns:
            五线谱区域列表
        """
        if 0 <= page_index < len(self.pages):
            return self.pages[page_index].staff_regions
        return []

    def enhance_sharpness(self, image: np.ndarray, factor: float = 1.5) -> np.ndarray:
        """增强图像清晰度（锐化处理）

        Args:
            image: 输入图像
            factor: 锐化强度因子

        Returns:
            锐化后的图像
        """
        try:
            import cv2
            kernel = np.array([[-1, -1, -1],
                               [-1, 9 * factor, -1],
                               [-1, -1, -1]]) / (factor - 1) if factor > 1 else np.array([[0, 0, 0], [0, 1, 0], [0, 0, 0]])
            return cv2.filter2D(image, -1, kernel)
        except ImportError:
            return image


def extract_staff_region(page: ProcessedPage, region: StaffRegion) -> np.ndarray:
    """提取单个五线谱区域的图像子矩阵

    Args:
        page: 已处理的页面对象
        region: 五线谱区域描述

    Returns:
        五线谱区域图像
    """
    x, y, w, h = region.bbox
    return page.image[y:y + h, x:x + w]