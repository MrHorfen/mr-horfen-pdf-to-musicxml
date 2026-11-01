"""
文件: pipeline.py
用途: PDF预处理统一管线，整合质量检测、PDF渲染、歪斜校正、图像增强、区域切割等阶段，支持多种预处理策略
调用方: score_converter 主流程或 pdf_preprocessor 模块
输入: 原始PDF文件路径和输出目录
输出: 预处理后的标准化图像文件
依赖: PIL, numpy, scipy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

import fitz
from PIL import Image
import numpy as np

from .stages.quality_check import QualityChecker, QualityReport
from .stages.renderer import PDFRenderer
from .stages.deskew import DeskewCorrector
from .stages.enhancement import ImageEnhancer
from .stages.region_cut import RegionCutter
from .stages.pdf_type_detector import PDFTypeDetector, PDFType
from .profiles import get_profile, PreprocessingProfile

logger = logging.getLogger(__name__)


@dataclass
class PreprocessingResult:
    """预处理结果"""
    input_file: str
    output_dir: str
    profile_name: str
    pages_processed: int = 0
    output_files: List[str] = field(default_factory=list)
    quality_reports: List[QualityReport] = field(default_factory=list)
    processing_time: float = 0.0
    errors: List[str] = field(default_factory=list)
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and len(self.output_files) > 0


class PDFPreprocessingPipeline:
    """PDF预处理统一管线
    
    支持多种预处理策略（standard/high_quality/low_quality），
    每个阶段可独立配置或跳过。
    """
    
    def __init__(self, profile: str = "standard", custom_config: Optional[Dict] = None):
        """
        Args:
            profile: 预设配置名称（standard/high_quality/low_quality）
            custom_config: 自定义配置覆盖
        """
        self.profile = get_profile(profile, custom_config)
        
        # 初始化各阶段处理器
        self.pdf_type_detector = PDFTypeDetector()
        self.quality_checker = QualityChecker()
        self.renderer = PDFRenderer(
            target_dpi=self.profile.dpi,
            max_pixels=self.profile.max_pixels
        )
        self.deskew_corrector = DeskewCorrector(
            enabled=self.profile.deskew_enabled,
            angle_range=self.profile.deskew_angle_range
        )
        self.enhancer = ImageEnhancer(
            method=self.profile.enhancement_method,
            config=self.profile.enhancement_config
        )
        self.region_cutter = RegionCutter(
            enabled=self.profile.region_cut_enabled,
            strategy=self.profile.region_cut_strategy
        )
        
        logger.info(f"初始化预处理管线: profile={profile}")
    
    def process(self, input_path: str, output_dir: str, 
                page_range: Optional[List[int]] = None) -> PreprocessingResult:
        """执行完整预处理流程
        
        Args:
            input_path: 输入PDF路径
            output_dir: 输出目录
            page_range: 要处理的页面范围（None表示全部）
            
        Returns:
            PreprocessingResult 对象
        """
        import time
        start_time = time.time()
        
        result = PreprocessingResult(
            input_file=input_path,
            output_dir=output_dir,
            profile_name=self.profile.name
        )
        
        logger.info(f"开始预处理: {input_path}")
        logger.info(f"输出目录: {output_dir}")
        logger.info(f"配置: {self.profile.name}")
        
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            # 加载PDF
            doc = fitz.open(input_path)
            total_pages = len(doc)
            
            # 阶段0: PDF类型检测（在渲染之前，决定后续策略）
            pdf_type = self.pdf_type_detector.detect(doc)
            logger.info(f"PDF类型: {pdf_type.value}")
            
            # 确定要处理的页面
            if page_range is None:
                pages_to_process = list(range(total_pages))
            else:
                pages_to_process = [p for p in page_range if p < total_pages]
            
            logger.info(f"总页数: {total_pages}, 待处理: {len(pages_to_process)}")
            
            # 逐页处理
            for page_num in pages_to_process:
                page_type = self.pdf_type_detector.get_page_type(page_num)
                logger.info(f"\n处理第 {page_num + 1}/{total_pages} 页 (类型: {page_type.value})...")
                
                try:
                    # 阶段1: 质量检测
                    quality_report = self.quality_checker.check(doc[page_num])
                    result.quality_reports.append(quality_report)
                    logger.info(f"  质量检测: {quality_report.summary}")
                    
                    # 阶段2: PDF渲染（根据页面类型选择不同策略）
                    img = self.renderer.render(doc[page_num], page_type=page_type)
                    logger.info(f"  渲染完成: {img.width}x{img.height}")
                    
                    # 阶段3: 歪斜校正
                    if self.profile.deskew_enabled:
                        img = self.deskew_corrector.correct(img)
                        logger.info(f"  歪斜校正: {self.deskew_corrector.last_angle}°")
                    
                    # 阶段4: 图像增强
                    if self.profile.enhancement_enabled:
                        img = self.enhancer.enhance(img)
                        logger.info(f"  图像增强: {self.profile.enhancement_method}")
                    
                    # 阶段5: 区域切割（可选）
                    if self.profile.region_cut_enabled:
                        images = self.region_cutter.cut(img, page_num)
                        logger.info(f"  区域切割: {len(images)} 个区域")
                    else:
                        images = [img]
                    
                    # 保存输出
                    for idx, final_img in enumerate(images):
                        output_path = self._save_image(
                            final_img, output_dir, page_num, idx
                        )
                        result.output_files.append(output_path)
                        logger.info(f"  已保存: {Path(output_path).name}")
                    
                    result.pages_processed += 1
                    
                except Exception as e:
                    error_msg = f"第 {page_num + 1} 页处理失败: {e}"
                    logger.error(error_msg)
                    result.errors.append(error_msg)
            
            doc.close()
            
        except Exception as e:
            error_msg = f"PDF加载失败: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
        
        result.processing_time = time.time() - start_time
        logger.info(f"\n预处理完成: {result.pages_processed} 页, "
                   f"耗时 {result.processing_time:.2f}s")
        
        return result
    
    def _save_image(self, img: Image.Image, output_dir: str, 
                    page_num: int, region_idx: int) -> str:
        """保存图像到输出目录
        
        Args:
            img: PIL Image对象
            output_dir: 输出目录
            page_num: 页码
            region_idx: 区域索引
            
        Returns:
            输出文件路径
        """
        if region_idx == 0:
            filename = f"page_{page_num + 1:03d}_normalized.png"
        else:
            filename = f"page_{page_num + 1:03d}_region_{region_idx}.png"
        
        output_path = os.path.join(output_dir, filename)
        img.save(output_path)
        return output_path


def quick_preprocess(input_path: str, output_dir: str, 
                     profile: str = "standard",
                     custom_config: Optional[Dict] = None) -> List[str]:
    """快速预处理（便捷函数）
    
    Args:
        input_path: 输入PDF路径
        output_dir: 输出目录
        profile: 预设配置
        custom_config: 自定义配置覆盖
        
    Returns:
        输出文件路径列表
    """
    pipeline = PDFPreprocessingPipeline(profile=profile, custom_config=custom_config)
    result = pipeline.process(input_path, output_dir)
    
    if not result.success:
        raise RuntimeError(f"预处理失败: {result.errors}")
    
    return result.output_files
