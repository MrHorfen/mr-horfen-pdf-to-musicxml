"""
文件: image_enhancer.py
用途: 对预处理后的乐谱图像执行针对性增强操作（自适应对比度增强、锐化、形态学去噪、谱线增强/抑制），提升OMR识别准确率
调用方: pdf_preprocessor pipeline 或 OMR识别流程
输入: 预处理后的乐谱图像（numpy数组或图像路径）
输出: 增强后的乐谱图像
依赖: cv2, numpy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import cv2
import numpy as np
from typing import List, Dict, Optional, Tuple


class ImageEnhancer:
    """乐谱图像增强器

    提供多种图像增强技术，专为乐谱图像优化。
    """

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

    def enhance_all(self, pages: List[np.ndarray]) -> List[np.ndarray]:
        """批量增强所有页面

        Args:
            pages: 页面图像列表

        Returns:
            增强后的图像列表
        """
        return [self.enhance(page) for page in pages]

    def enhance(self, image: np.ndarray) -> np.ndarray:
        """单页图像增强流水线

        Args:
            image: 输入灰度图像

        Returns:
            增强后的图像
        """
        if image is None or image.size == 0:
            return image

        result = image.copy()

        # 1. 自适应直方图均衡化 (CLAHE)
        result = self._apply_clahe(result)

        # 2. 双边滤波去噪（保边）
        result = self._apply_bilateral_filter(result)

        # 3. 锐化
        result = self._sharpen(result)

        # 4. 形态学去噪
        result = self._morphological_denoise(result)

        return result

    def _apply_clahe(self, image: np.ndarray) -> np.ndarray:
        """自适应直方图均衡化

        CLAHE限制对比度放大，适用于乐谱这种细节丰富的图像。

        Args:
            image: 输入图像

        Returns:
            CLAHE处理后图像
        """
        clip_limit = self.config.get("clahe_clip_limit", 2.0)
        tile_size = self.config.get("clahe_tile_size", 8)

        clahe = cv2.createCLAHE(
            clipLimit=clip_limit,
            tileGridSize=(tile_size, tile_size),
        )
        return clahe.apply(image)

    def _apply_bilateral_filter(self, image: np.ndarray) -> np.ndarray:
        """双边滤波去噪（保持边缘）

        Args:
            image: 输入图像

        Returns:
            滤波后图像
        """
        d = self.config.get("bilateral_d", 9)
        sigma_color = self.config.get("bilateral_sigma_color", 75)
        sigma_space = self.config.get("bilateral_sigma_space", 75)

        return cv2.bilateralFilter(image, d, sigma_color, sigma_space)

    def _sharpen(self, image: np.ndarray) -> np.ndarray:
        """图像锐化

        使用自定义拉普拉斯锐化核增强乐谱符号边缘。

        Args:
            image: 输入图像

        Returns:
            锐化后图像
        """
        strength = self.config.get("sharpen_strength", 1.0)

        kernel = np.array([
            [0, -strength, 0],
            [-strength, 1 + 4 * strength, -strength],
            [0, -strength, 0],
        ])

        sharpened = cv2.filter2D(image, -1, kernel)
        return np.clip(sharpened, 0, 255).astype(np.uint8)

    def _morphological_denoise(self, image: np.ndarray) -> np.ndarray:
        """形态学去噪：开运算去除小噪点

        Args:
            image: 输入图像

        Returns:
            去噪后图像
        """
        kernel_size = self.config.get("morph_kernel_size", 2)
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)
        )

        _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        denoised = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        _, binary_inv = cv2.threshold(denoised, 128, 255, cv2.THRESH_BINARY_INV)

        result = cv2.addWeighted(image, 0.7, cv2.bitwise_not(binary_inv), 0.3, 0)
        return result.astype(np.uint8)

    def enhance_staff_lines(self, image: np.ndarray) -> np.ndarray:
        """增强五线谱线（用于谱线检测）

        Args:
            image: 输入图像

        Returns:
            谱线增强图像
        """
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 1))
        lines = cv2.morphologyEx(image, cv2.MORPH_OPEN, horizontal_kernel)
        return cv2.addWeighted(image, 0.5, lines, 0.5, 0)

    def suppress_staff_lines(self, image: np.ndarray) -> np.ndarray:
        """抑制五线谱线（用于符号检测）

        Args:
            image: 输入图像

        Returns:
            谱线抑制图像
        """
        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 1))
        lines = cv2.morphologyEx(image, cv2.MORPH_OPEN, horizontal_kernel)
        return cv2.subtract(image, lines)

    def resize_to_dpi(self, image: np.ndarray, target_dpi: int,
                      current_dpi: int = 300) -> np.ndarray:
        """调整图像DPI

        Args:
            image: 输入图像
            target_dpi: 目标DPI
            current_dpi: 当前DPI

        Returns:
            调整后的图像
        """
        if current_dpi == target_dpi:
            return image

        scale = target_dpi / current_dpi
        new_width = int(image.shape[1] * scale)
        new_height = int(image.shape[0] * scale)

        return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_CUBIC)