"""
文件: quality_check.py
用途: 评估PDF页面质量，识别可能影响OMR识别的问题（如分辨率不足、噪点过多等）
调用方: pipeline.py 预处理管线
输入: PDF页面对象（fitz.Page）
输出: QualityReport质量评估报告
依赖: PyMuPDF(fitz), dataclasses
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from dataclasses import dataclass
from typing import Optional

import fitz

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """质量检测报告"""
    page_number: int
    has_text: bool = False
    has_images: bool = False
    text_content: str = ""
    estimated_dpi: Optional[int] = None
    issues: list = None
    
    def __post_init__(self):
        if self.issues is None:
            self.issues = []
    
    @property
    def summary(self) -> str:
        """生成质量摘要"""
        status = "良好" if not self.issues else f"发现 {len(self.issues)} 个问题"
        return f"页{self.page_number}: {status}"


class QualityChecker:
    """PDF页面质量检测器"""
    
    def check(self, page: fitz.Page) -> QualityReport:
        """检测页面质量
        
        Args:
            page: PyMuPDF页面对象
            
        Returns:
            QualityReport 对象
        """
        page_num = page.number + 1
        report = QualityReport(page_number=page_num)
        
        # 检查文本内容
        text = page.get_text().strip()
        report.has_text = len(text) > 0
        report.text_content = text[:200]  # 只保留前200字符
        
        # 检查图像
        images = page.get_images()
        report.has_images = len(images) > 0
        
        # 估算DPI（基于页面尺寸和图像）
        if report.has_images:
            report.estimated_dpi = self._estimate_dpi(page, images[0])
        
        # 识别问题
        if not report.has_text and not report.has_images:
            report.issues.append("页面可能为空")
        
        return report
    
    def _estimate_dpi(self, page: fitz.Page, image_info: tuple) -> Optional[int]:
        """估算页面DPI
        
        Args:
            page: 页面对象
            image_info: 图像信息元组
            
        Returns:
            估算的DPI值
        """
        try:
            # 获取页面物理尺寸（英寸）
            page_rect = page.rect
            page_width_inch = page_rect.width / 72  # 72 points = 1 inch
            
            # 获取图像像素尺寸
            xref = image_info[0]
            pix = fitz.Pixmap(fitz.BaseDocument, xref)
            image_width_px = pix.width
            
            # 计算DPI
            dpi = int(image_width_px / page_width_inch)
            return dpi
        except Exception as e:
            logger.debug(f"DPI估算失败: {e}")
            return None
