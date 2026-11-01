"""
文件: __init__.py
用途: 预处理阶段模块包初始化，导出QualityChecker、PDFRenderer、DeskewCorrector、ImageEnhancer、RegionCutter等核心接口
调用方: 外部模块导入 stages 包时自动调用
输入: 无
输出: 导出各预处理阶段类
依赖: 无
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

from .quality_check import QualityChecker, QualityReport
from .renderer import PDFRenderer
from .deskew import DeskewCorrector
from .enhancement import ImageEnhancer
from .region_cut import RegionCutter

__all__ = [
    "QualityChecker",
    "QualityReport",
    "PDFRenderer",
    "DeskewCorrector",
    "ImageEnhancer",
    "RegionCutter",
]
