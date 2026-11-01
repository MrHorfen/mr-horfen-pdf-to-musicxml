"""
文件: __init__.py
用途: PDF预处理器模块包初始化，导出Pipeline和Profile等核心接口
调用方: 外部模块导入 pdf_preprocessor 包时自动调用
输入: 无
输出: 导出 PDFPreprocessingPipeline, PreprocessingResult, quick_preprocess, get_profile, PreprocessingProfile
依赖: 无
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

from .pipeline import PDFPreprocessingPipeline, PreprocessingResult, quick_preprocess
from .profiles import get_profile, PreprocessingProfile

__all__ = [
    "PDFPreprocessingPipeline",
    "PreprocessingResult",
    "quick_preprocess",
    "get_profile",
    "PreprocessingProfile",
]
