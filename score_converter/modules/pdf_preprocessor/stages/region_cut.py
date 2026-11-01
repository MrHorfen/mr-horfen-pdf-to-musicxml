"""
文件: region_cut.py
用途: 对乐谱图像进行区域切割，提取特定部分（如钢琴部分），支持固定范围和文本检测两种策略
调用方: pipeline.py 预处理管线
输入: 乐谱图像和切割参数
输出: 切割后的区域图像
依赖: PyMuPDF(fitz), PIL
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from typing import List, Tuple, Optional

import fitz
from PIL import Image

logger = logging.getLogger(__name__)


class RegionCutter:
    """区域切割器"""
    
    def __init__(self, enabled: bool = False, strategy: str = "none"):
        """
        Args:
            enabled: 是否启用区域切割
            strategy: 切割策略（none/fixed_range/text_detection）
        """
        self.enabled = enabled
        self.strategy = strategy
        
        logger.info(f"初始化区域切割器: enabled={enabled}, strategy={strategy}")
    
    def cut(self, img: Image.Image, page_num: int) -> List[Image.Image]:
        """执行区域切割
        
        Args:
            img: 输入图像
            page_num: 页码（用于特殊处理）
            
        Returns:
            切割后的图像列表
        """
        if not self.enabled or self.strategy == "none":
            return [img]
        
        if self.strategy == "fixed_range":
            return self._cut_fixed_range(img, page_num)
        elif self.strategy == "text_detection":
            return self._cut_by_text_detection(img, page_num)
        else:
            logger.warning(f"未知切割策略: {self.strategy}")
            return [img]
    
    def _cut_fixed_range(self, img: Image.Image, page_num: int) -> List[Image.Image]:
        """固定范围切割
        
        根据预定义的Y坐标范围切割图像。
        适用于钢琴+合唱混合谱，需要提取钢琴部分。
        
        Args:
            img: 输入图像
            page_num: 页码
            
        Returns:
            切割后的图像列表
        """
        # 默认范围（需要根据实际谱子调整）
        if page_num == 0:
            # 第一页特殊处理
            ranges = [
                (0.05, 0.35),   # 上方区域
                (0.35, 0.60),   # 中间区域
                (0.75, 0.95),   # 下方区域
            ]
        else:
            # 其他页面
            ranges = [
                (0.20, 0.45),   # 上方区域
                (0.70, 0.95),   # 下方区域
            ]
        
        height = img.height
        width = img.width
        
        results = []
        for start_ratio, end_ratio in ranges:
            y_start = int(height * start_ratio)
            y_end = int(height * end_ratio)
            
            # 裁剪图像
            cropped = img.crop((0, y_start, width, y_end))
            results.append(cropped)
            logger.debug(f"切割区域: y={y_start}-{y_end} ({start_ratio:.0%}-{end_ratio:.0%})")
        
        return results
    
    def _cut_by_text_detection(self, img: Image.Image, page_num: int) -> List[Image.Image]:
        """基于文本检测的切割
        
        检测页面中的文本标记（如"Pno."），根据位置确定切割范围。
        
        Args:
            img: 输入图像
            page_num: 页码
            
        Returns:
            切割后的图像列表
        """
        # 这个方法需要访问原始PDF的文本信息
        # 在实际使用中，应该在pipeline中传递page对象
        # 这里暂时返回原图
        logger.warning("文本检测切割尚未实现，返回原图")
        return [img]


def extract_piano_region_from_pdf(pdf_path: str, output_path: str,
                                   piano_ranges: List[Tuple[int, int]] = None):
    """从PDF中提取钢琴区域的便捷函数
    
    Args:
        pdf_path: 输入PDF路径
        output_path: 输出PDF路径
        piano_ranges: 钢琴区域Y坐标范围列表 [(start, end), ...]
    """
    if piano_ranges is None:
        # 默认范围
        piano_ranges = [
            (300, 450),
            (700, 850),
        ]
    
    doc = fitz.open(pdf_path)
    output_doc = fitz.open()
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height
        
        # 检查是否有钢琴标识
        text = page.get_text()
        if 'Pno.' not in text and 'Piano' not in text:
            continue
        
        # 第一页特殊处理
        if page_num == 0:
            ranges = [(50, 280), (300, 450), (700, 850)]
        else:
            ranges = piano_ranges
        
        for y_start, y_end in ranges:
            y_start = max(0, y_start)
            y_end = min(page_height, y_end)
            
            if y_end <= y_start:
                continue
            
            new_page = output_doc.new_page(width=page_width, height=page_height)
            extract_area = fitz.Rect(0, y_start, page_width, y_end)
            new_page.show_pdf_page(new_page.rect, doc, page_num, clip=extract_area)
    
    output_doc.save(output_path)
    output_doc.close()
    doc.close()
    
    logger.info(f"钢琴区域提取完成: {output_path}")
