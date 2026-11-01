"""
文件: enhancement.py
用途: 对乐谱图像执行增强操作（官方推荐方法和高级增强），提升OMR识别准确率
调用方: pipeline.py 预处理管线
输入: 乐谱图像（numpy数组或PIL Image）
输出: 增强后的乐谱图像
依赖: numpy, PIL
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from typing import Dict, Optional, Any

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)


class ImageEnhancer:
    """乐谱图像增强器"""
    
    def __init__(self, method: str = "official", config: Optional[Dict[str, Any]] = None):
        """
        Args:
            method: 增强方法（official/advanced/none）
            config: 增强参数配置
        """
        self.method = method
        self.config = config or {}
        
        logger.info(f"初始化图像增强器: method={method}")
    
    def enhance(self, img: Image.Image) -> Image.Image:
        """执行图像增强
        
        Args:
            img: 输入灰度图像
            
        Returns:
            增强后的图像
        """
        if self.method == "none":
            logger.debug("图像增强已禁用")
            return img
        
        if self.method == "official":
            return self._enhance_official(img)
        elif self.method == "guitar":
            return self._enhance_guitar(img)
        elif self.method == "advanced":
            return self._enhance_advanced(img)
        else:
            logger.warning(f"未知增强方法: {self.method}，使用默认方法")
            return self._enhance_official(img)
    
    def _enhance_guitar(self, img: Image.Image) -> Image.Image:
        """吉他谱专用增强 — 谱线压暗 + 对比度提升
        
        吉他谱扫描PDF的谱线像素值常在100-120之间（偏淡），
        Audiveris内部阈值约100，导致谱线检测失败。
        
        策略：
        1. 分段线性映射：暗部（<120）压缩到0-60，让谱线变黑
        2. 整体对比度提升（factor=1.3）
        3. 不做模糊（保护细小音符）
        
        Args:
            img: 输入灰度图像
            
        Returns:
            增强后的图像
        """
        arr = np.array(img).astype(np.float32)
        
        # 分段线性映射：暗部压暗，亮部保持
        threshold = self.config.get("dark_threshold", 120)
        dark_target = self.config.get("dark_target", 60)
        arr = np.where(
            arr < threshold,
            arr * (dark_target / threshold),
            arr
        )
        
        # 整体对比度提升
        factor = self.config.get("contrast_factor", 1.3)
        arr = (arr - 128) * factor + 128
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        
        img = Image.fromarray(arr)
        
        logger.debug(f"吉他增强完成: dark_threshold={threshold}, contrast={factor}")
        return img
    
    def _enhance_official(self, img: Image.Image) -> Image.Image:
        """Audiveris官方推荐的增强方法
        
        步骤：
        1. 对比度增强
        2. 亮度微调
        3. 高斯模糊去噪
        4. 非锐化掩模重新锐化
        
        Args:
            img: 输入灰度图像
            
        Returns:
            增强后的图像
        """
        # 从配置获取参数，有默认值
        contrast = self.config.get("contrast", 1.5)
        brightness = self.config.get("brightness", 1.05)
        blur_radius = self.config.get("blur_radius", 0.8)
        unsharp_radius = self.config.get("unsharp_radius", 0.8)
        unsharp_percent = self.config.get("unsharp_percent", 120)
        
        # 1. 对比度增强
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(contrast)
        
        # 2. 亮度微调
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(brightness)
        
        # 3. 高斯模糊去噪
        img = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        
        # 4. 非锐化掩模重新锐化
        img = img.filter(ImageFilter.UnsharpMask(
            radius=unsharp_radius, 
            percent=unsharp_percent, 
            threshold=0
        ))
        
        logger.debug(f"官方增强完成: contrast={contrast}, brightness={brightness}")
        return img
    
    def _enhance_advanced(self, img: Image.Image) -> Image.Image:
        """高级增强方法（使用OpenCV）
        
        步骤：
        1. CLAHE自适应直方图均衡化
        2. 双边滤波去噪（保边）
        3. 锐化
        4. 形态学去噪
        
        Args:
            img: 输入灰度图像
            
        Returns:
            增强后的图像
        """
        try:
            import cv2
        except ImportError:
            logger.warning("OpenCV未安装，回退到官方增强方法")
            return self._enhance_official(img)
        
        img_array = np.array(img)
        
        # 1. CLAHE自适应直方图均衡化
        clip_limit = self.config.get("clahe_clip_limit", 2.0)
        tile_size = self.config.get("clahe_tile_size", 8)
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
        result = clahe.apply(img_array)
        
        # 2. 双边滤波去噪
        d = self.config.get("bilateral_d", 9)
        sigma_color = self.config.get("bilateral_sigma_color", 75)
        sigma_space = self.config.get("bilateral_sigma_space", 75)
        result = cv2.bilateralFilter(result, d, sigma_color, sigma_space)
        
        # 3. 锐化
        strength = self.config.get("sharpen_strength", 1.0)
        kernel = np.array([
            [0, -strength, 0],
            [-strength, 1 + 4 * strength, -strength],
            [0, -strength, 0],
        ])
        result = cv2.filter2D(result, -1, kernel)
        result = np.clip(result, 0, 255).astype(np.uint8)
        
        # 4. 形态学去噪
        kernel_size = self.config.get("morph_kernel_size", 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        _, binary = cv2.threshold(result, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        denoised = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        _, binary_inv = cv2.threshold(denoised, 128, 255, cv2.THRESH_BINARY_INV)
        result = cv2.addWeighted(result, 0.7, cv2.bitwise_not(binary_inv), 0.3, 0)
        
        return Image.fromarray(result.astype(np.uint8))
