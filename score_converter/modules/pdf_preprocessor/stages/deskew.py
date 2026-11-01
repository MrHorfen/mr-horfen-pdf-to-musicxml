"""
文件: deskew.py
用途: 检测并校正PDF/乐谱图像的歪斜角度，确保谱线水平对齐
调用方: pipeline.py 预处理管线
输入: 乐谱图像（numpy数组或PIL Image）
输出: 歪斜校正后的乐谱图像
依赖: numpy, PIL, scipy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from typing import Optional

import numpy as np
from PIL import Image
from scipy.ndimage import rotate

logger = logging.getLogger(__name__)


class DeskewCorrector:
    """歪斜校正器"""
    
    def __init__(self, enabled: bool = True, angle_range: float = 5.0):
        """
        Args:
            enabled: 是否启用歪斜校正
            angle_range: 检测角度范围（-angle_range 到 +angle_range）
        """
        self.enabled = enabled
        self.angle_range = angle_range
        self.last_angle: Optional[float] = None
        
        logger.info(f"初始化歪斜校正器: enabled={enabled}, range=±{angle_range}°")
    
    def correct(self, img: Image.Image) -> Image.Image:
        """校正图像歪斜
        
        Args:
            img: 输入灰度图像
            
        Returns:
            校正后的图像
        """
        if not self.enabled:
            logger.debug("歪斜校正已禁用")
            return img
        
        img_array = np.array(img)
        
        # 检测歪斜角度
        best_angle = self._detect_skew(img_array)
        self.last_angle = best_angle
        
        # 应用校正
        if abs(best_angle) > 0.1:  # 只校正明显歪斜
            logger.info(f"检测到歪斜: {best_angle:.2f}°，执行校正")
            corrected = rotate(img_array, -best_angle, reshape=False, order=1)
            return Image.fromarray(corrected)
        else:
            logger.debug(f"歪斜角度过小: {best_angle:.2f}°，跳过校正")
            return img
    
    def _detect_skew(self, img_array: np.ndarray) -> float:
        """检测图像歪斜角度
        
        通过尝试不同角度旋转，计算水平投影方差，
        方差最大的角度即为最可能的歪斜角度。
        
        Args:
            img_array: 灰度图像数组
            
        Returns:
            检测到的歪斜角度（度）
        """
        best_angle = 0
        best_score = 0
        
        # 尝试范围内的所有整数角度
        for angle in range(-int(self.angle_range), int(self.angle_range) + 1):
            rotated = rotate(img_array, angle, reshape=False, order=0)
            
            # 计算中心区域的水平投影方差
            height = rotated.shape[0]
            mid_y = height // 2
            sample_region = rotated[max(0, mid_y - 500):min(height, mid_y + 500), :]
            
            horizontal_projection = np.sum(sample_region < 128, axis=1)
            score = np.var(horizontal_projection)
            
            if score > best_score:
                best_score = score
                best_angle = angle
        
        return float(best_angle)
