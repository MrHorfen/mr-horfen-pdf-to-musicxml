"""
文件: pdf_type_detector.py
用途: 判断PDF是文字型（vector-based）还是扫描型（image-based），为后续预处理策略提供依据
调用方: pipeline.py 预处理管线（renderer阶段）
输入: PDF页面对象（fitz.Page）
输出: PDFType枚举值（VECTOR或SCANNED）
依赖: PyMuPDF(fitz)
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from enum import Enum
from typing import List

import fitz

logger = logging.getLogger(__name__)


class PDFType(Enum):
    """PDF类型枚举"""
    VECTOR = "vector"      # 文字型PDF（包含矢量文字和图形）
    SCANNED = "scanned"    # 扫描型PDF（纯图片）
    MIXED = "mixed"        # 混合型（部分页面有文字，部分是扫描）


class PDFTypeDetector:
    """PDF类型检测器"""
    
    def __init__(self, text_threshold: int = 10):
        """
        Args:
            text_threshold: 判定为文字型PDF的最小文字块数量阈值
        """
        self.text_threshold = text_threshold
        self._page_types: List[PDFType] = []
        
        logger.info(f"初始化PDF类型检测器: 文字阈值={text_threshold}")
    
    def detect(self, doc: fitz.Document) -> PDFType:
        """检测整个PDF的类型
        
        Args:
            doc: PyMuPDF文档对象
            
        Returns:
            PDFType 枚举值
        """
        self._page_types = []
        total_pages = len(doc)
        
        vector_count = 0
        scanned_count = 0
        
        for page_num in range(total_pages):
            page_type = self._detect_page_type(doc[page_num])
            self._page_types.append(page_type)
            
            if page_type == PDFType.VECTOR:
                vector_count += 1
            elif page_type == PDFType.SCANNED:
                scanned_count += 1
        
        # 判断整体类型
        if vector_count == total_pages:
            pdf_type = PDFType.VECTOR
        elif scanned_count == total_pages:
            pdf_type = PDFType.SCANNED
        else:
            pdf_type = PDFType.MIXED
        
        logger.info(f"PDF类型检测完成: {pdf_type.value} "
                   f"(文字型={vector_count}页, 扫描型={scanned_count}页, 总页数={total_pages})")
        
        return pdf_type
    
    def _detect_page_type(self, page: fitz.Page) -> PDFType:
        """检测单个页面的类型
        
        Args:
            page: PyMuPDF页面对象
            
        Returns:
            PDFType 枚举值
        """
        # 获取文字块
        blocks = page.get_text("blocks")
        text_block_count = len([b for b in blocks if b[4].strip()]) if blocks else 0
        
        # 获取图像列表
        images = page.get_images()
        image_count = len(images)
        
        # 判断逻辑
        if text_block_count >= self.text_threshold:
            # 有足够多的文字块 → 文字型
            page_type = PDFType.VECTOR
            logger.debug(f"第{page.number + 1}页: 文字型 (文字块={text_block_count}, 图像={image_count})")
        elif image_count > 0 and text_block_count < self.text_threshold:
            # 有图像但文字很少 → 扫描型
            page_type = PDFType.SCANNED
            logger.debug(f"第{page.number + 1}页: 扫描型 (文字块={text_block_count}, 图像={image_count})")
        else:
            # 默认按文字型处理（可能是空白页或特殊PDF）
            page_type = PDFType.VECTOR
            logger.debug(f"第{page.number + 1}页: 默认文字型 (文字块={text_block_count}, 图像={image_count})")
        
        return page_type
    
    def get_page_type(self, page_num: int) -> PDFType:
        """获取指定页面的类型
        
        Args:
            page_num: 页码（从0开始）
            
        Returns:
            PDFType 枚举值
        """
        if page_num < 0 or page_num >= len(self._page_types):
            return PDFType.VECTOR  # 默认
        
        return self._page_types[page_num]
