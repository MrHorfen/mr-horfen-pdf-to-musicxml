"""
文件: profiles.py
用途: 定义和管理PDF预处理配置（profile），支持快速切换不同的预处理策略和参数
调用方: pipeline.py 或 pdf_preprocessor 模块
输入: profile名称字符串
输出: PreprocessingProfile配置对象
依赖: dataclasses
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional


@dataclass
class PreprocessingProfile:
    """预处理配置"""
    name: str
    dpi: int = 300
    max_pixels: int = 20_000_000
    
    # 歪斜校正
    deskew_enabled: bool = True
    deskew_angle_range: float = 5.0
    
    # 图像增强
    enhancement_enabled: bool = True
    enhancement_method: str = "official"  # official/advanced/none
    enhancement_config: Dict[str, Any] = field(default_factory=dict)
    
    # 区域切割
    region_cut_enabled: bool = False
    region_cut_strategy: str = "none"  # none/fixed_range/text_detection


# 标准配置（适用于大多数PDF）
# 对干净印刷版PDF：只做歪斜校正，不做图像增强（Audiveris自带自适应二值化）
# 对扫描版PDF：renderer阶段已做差异化处理，增强留给guitar等专用profile
STANDARD_PROFILE = PreprocessingProfile(
    name="standard",
    dpi=300,
    max_pixels=20_000_000,
    deskew_enabled=True,
    deskew_angle_range=5.0,
    enhancement_enabled=False,  # 默认关闭：干净PDF不需要增强，避免破坏谱线
    enhancement_method="official",
    enhancement_config={
        "contrast": 1.2,
        "brightness": 1.0,
        "blur_radius": 1.5,
        "unsharp_radius": 1.0,
        "unsharp_percent": 100,
    },
    region_cut_enabled=False,
)

# 高质量配置（适用于低质量扫描PDF）
HIGH_QUALITY_PROFILE = PreprocessingProfile(
    name="high_quality",
    dpi=400,
    max_pixels=20_000_000,
    deskew_enabled=True,
    deskew_angle_range=5.0,
    enhancement_enabled=True,
    enhancement_method="advanced",  # 使用OpenCV高级增强
    enhancement_config={
        "clahe_clip_limit": 2.0,
        "clahe_tile_size": 8,
        "bilateral_d": 9,
        "bilateral_sigma_color": 75,
        "bilateral_sigma_space": 75,
        "sharpen_strength": 1.0,
        "morph_kernel_size": 2,
    },
    region_cut_enabled=False,
)

# 低质量配置（适用于质量较差的PDF，更激进的增强）
LOW_QUALITY_PROFILE = PreprocessingProfile(
    name="low_quality",
    dpi=400,
    max_pixels=20_000_000,
    deskew_enabled=True,
    deskew_angle_range=5.0,
    enhancement_enabled=True,
    enhancement_method="official",
    enhancement_config={
        "contrast": 1.8,  # 更强的对比度
        "brightness": 1.1,
        "blur_radius": 1.0,
        "unsharp_radius": 1.0,
        "unsharp_percent": 150,
    },
    region_cut_enabled=False,
)

# 合唱谱配置（需要区域切割提取钢琴部分）
CHOIR_PROFILE = PreprocessingProfile(
    name="choir",
    dpi=300,
    max_pixels=20_000_000,
    deskew_enabled=True,
    deskew_angle_range=5.0,
    enhancement_enabled=True,
    enhancement_method="official",
    enhancement_config={
        "contrast": 1.5,
        "brightness": 1.05,
        "blur_radius": 0.8,
        "unsharp_radius": 0.8,
        "unsharp_percent": 120,
    },
    region_cut_enabled=True,
    region_cut_strategy="fixed_range",
)


def get_profile(name: str, custom_config: Optional[Dict] = None) -> PreprocessingProfile:
    """获取预处理配置
    
    Args:
        name: 配置名称（standard/high_quality/low_quality/choir）
        custom_config: 自定义配置覆盖
        
    Returns:
        PreprocessingProfile 对象
    """
    profiles = {
        "standard": STANDARD_PROFILE,
        "high_quality": HIGH_QUALITY_PROFILE,
        "low_quality": LOW_QUALITY_PROFILE,
        "choir": CHOIR_PROFILE,
    }
    
    if name not in profiles:
        raise ValueError(f"未知配置: {name}，可选: {list(profiles.keys())}")
    
    profile = profiles[name]
    
    # 应用自定义配置覆盖
    if custom_config:
        # 创建新的profile对象，避免修改原始对象
        profile = PreprocessingProfile(
            name=profile.name,
            dpi=custom_config.get("dpi", profile.dpi),
            max_pixels=custom_config.get("max_pixels", profile.max_pixels),
            deskew_enabled=custom_config.get("deskew_enabled", profile.deskew_enabled),
            deskew_angle_range=custom_config.get("deskew_angle_range", profile.deskew_angle_range),
            enhancement_enabled=custom_config.get("enhancement_enabled", profile.enhancement_enabled),
            enhancement_method=custom_config.get("enhancement_method", profile.enhancement_method),
            enhancement_config=custom_config.get("enhancement_config", profile.enhancement_config),
            region_cut_enabled=custom_config.get("region_cut_enabled", profile.region_cut_enabled),
            region_cut_strategy=custom_config.get("region_cut_strategy", profile.region_cut_strategy),
        )
    
    return profile
