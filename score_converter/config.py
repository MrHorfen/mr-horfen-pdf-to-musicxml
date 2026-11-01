"""
文件: config.py
用途: 全局配置模块，统一管理PDF乐谱转MusicXML系统的所有配置参数（乐器音域/图像处理/识别阈值/文件路径）
调用方: 所有 score_converter/ 模块
输入: 无（被 import 时自动初始化）
输出: 全局配置常量
依赖: pathlib, json
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Dict, Tuple, List, Optional
from enum import Enum


class ClefType(Enum):
    """谱号类型枚举"""
    TREBLE = ("G", 2)
    BASS = ("F", 4)
    ALTO = ("C", 3)
    TENOR = ("C", 4)
    PERCUSSION = ("percussion", 2)

    @property
    def sign(self) -> str:
        return self.value[0]

    @property
    def line(self) -> int:
        return self.value[1]


class NoteName(Enum):
    """音名枚举（C-B）"""
    C = 0
    D = 1
    E = 2
    F = 3
    G = 4
    A = 5
    B = 6


class AccidentalType(Enum):
    """变音记号类型"""
    SHARP = "sharp"
    FLAT = "flat"
    NATURAL = "natural"
    DOUBLE_SHARP = "double-sharp"
    DOUBLE_FLAT = "double-flat"


@dataclass
class InstrumentConfig:
    """乐器声部配置"""
    name: str
    part_id: str
    clef: ClefType
    pitch_range: Tuple[int, int]
    transposition: Optional[Dict[str, int]] = None
    is_transposing: bool = False


INSTRUMENTS: Dict[str, InstrumentConfig] = {
    "erhu": InstrumentConfig(
        name="Erhu",
        part_id="P1",
        clef=ClefType.TREBLE,
        pitch_range=(62, 81),  # D4-A5 标准二胡音域 [出处: Benward & Saker 2015]
        is_transposing=False,
    ),
    "dongxiao": InstrumentConfig(
        name="Dongxiao",
        part_id="P2",
        clef=ClefType.TREBLE,
        pitch_range=(62, 76),  # D4-E5 标准洞箫音域
        transposition={"diatonic": 3, "chromatic": 5},
        is_transposing=True,
    ),
    "pipa": InstrumentConfig(
        name="Pipa",
        part_id="P3",
        clef=ClefType.BASS,
        pitch_range=(40, 76),  # E2-E5 标准琵琶音域
        is_transposing=False,
    ),
    "yangqin": InstrumentConfig(
        name="Yangqin",
        part_id="P4",
        clef=ClefType.TREBLE,
        pitch_range=(43, 81),  # G2-A5 标准扬琴音域
        is_transposing=False,
    ),
    "guitar": InstrumentConfig(
        name="Guitar",
        part_id="P1",
        clef=ClefType.TREBLE,
        pitch_range=(40, 81),  # E2-A5 吉他标准音域
        is_transposing=False,
    ),
    "violin": InstrumentConfig(
        name="Violin",
        part_id="P1",
        clef=ClefType.TREBLE,
        pitch_range=(55, 94),  # G3-D7 小提琴标准音域
        is_transposing=False,
    ),
}


@dataclass
class ImageProcessingConfig:
    """图像预处理参数配置"""
    default_dpi: int = 300
    binary_threshold: int = 127
    adaptive_block_size: int = 11
    adaptive_c: float = 2.0
    denoise_strength: int = 10
    deskew_angle_range: float = 5.0
    staff_line_thickness: int = 2
    staff_spacing_min: int = 8
    staff_spacing_max: int = 20
    min_contour_area: int = 50
    max_aspect_ratio: float = 5.0


@dataclass
class OMRConfig:
    """光学乐谱识别参数配置"""
    notehead_radius_min: int = 3
    notehead_radius_max: int = 8
    notehead_solidity_min: float = 0.7
    notehead_circularity_min: float = 0.6
    stem_length_min: int = 20
    stem_length_max: int = 60
    stem_width_max: int = 5
    beam_thickness_min: int = 3
    slur_curvature_min: float = 0.01
    tie_gap_max: int = 20
    barline_thickness_min: int = 2
    barline_length_min: int = 30
    accidental_roi_size: int = 40
    dot_radius_max: int = 4
    staff_spacing_min: int = 8
    staff_spacing_max: int = 20
    min_contour_area: int = 50


@dataclass
class ErrorCorrectionConfig:
    """智能纠错参数配置"""
    enable_rhythm_check: bool = True
    enable_pitch_range_check: bool = True
    enable_accidental_scope_check: bool = True
    enable_voice_alignment_check: bool = True
    enable_barline_alignment_check: bool = True
    rhythm_tolerance_ratio: float = 0.05
    max_pitch_extension: int = 3


@dataclass
class SystemConfig:
    """系统全局配置"""
    image: ImageProcessingConfig = field(default_factory=ImageProcessingConfig)
    omr: OMRConfig = field(default_factory=OMRConfig)
    error_correction: ErrorCorrectionConfig = field(default_factory=ErrorCorrectionConfig)
    supported_time_signatures: List[Tuple[int, int]] = field(default_factory=lambda: [
        (2, 4), (3, 4), (4, 4), (3, 8), (6, 8), (9, 8), (12, 8), (2, 2),
        (5, 4), (7, 4), (5, 8), (7, 8)  # 不规则拍号 [出处: ABRSM Grade 5]
    ])
    supported_key_signatures: List[int] = field(default_factory=lambda: list(range(-7, 8)))
    log_level: int = logging.INFO
    log_format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


FOLK_SYMBOL_TEMPLATES: Dict[str, dict] = {
    "fermata": {
        "description": "延长记号",
        "musicxml_direction": "fermata",
        "shape_features": {"aspect_ratio": (0.5, 1.5), "circularity_min": 0.3},
    },
    "mordent": {
        "description": "波音",
        "musicxml_ornament": "mordent",
        "shape_features": {"aspect_ratio": (0.3, 1.2), "zigzag_count": 3},
    },
    "inverted_mordent": {
        "description": "逆波音",
        "musicxml_ornament": "inverted-mordent",
        "shape_features": {"aspect_ratio": (0.3, 1.2), "zigzag_count": 3},
    },
    "harmonics": {
        "description": "泛音",
        "text_pattern": ["*", "\u25cb"],
        "musicxml_notehead": "diamond",
    },
    "strike": {
        "description": "打音",
        "text_pattern": ["+"],
        "musicxml_direction": "strike",
    },
    "open_string": {
        "description": "空弦",
        "text_pattern": ["0", "\u25cb"],
        "musicxml_technical": "open-string",
    },
    "triplet": {
        "description": "三连音",
        "text_pattern": ["3"],
        "musicxml_time_modification": {"actual_notes": 3, "normal_notes": 2},
    },
}


def get_config() -> SystemConfig:
    """获取系统默认配置实例"""
    return SystemConfig()


def setup_logging(name: str, level: int = logging.INFO) -> logging.Logger:
    """初始化日志记录器"""
    cfg = get_config()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(cfg.log_format))
        logger.addHandler(handler)
    return logger


def get_instrument_pitch_range(instrument_key: str) -> Optional[Tuple[int, int]]:
    """获取指定乐器的音域范围（MIDI音高值）"""
    inst = INSTRUMENTS.get(instrument_key)
    if inst is None:
        return None
    return inst.pitch_range


SUPPORTED_SYMBOLS_SUMMARY = [
    "三连音（Triplet）",
    "延长记号（Fermata）",
    "波音/逆波音（Mordent/Inverted Mordent）",
    "临时升号（Sharp）",
    "临时降号（Flat）",
    "临时还原号（Natural）",
    "泛音（Harmonics - * / ○）",
    "打音（Strike - +）",
    "空弦（Open String - 0 / ○）",
    "连音线（Slur）",
    "延音线（Tie）",
    "强音记号（Accent）",
    "顿音记号（Staccato）",
    "呼吸记号（Breath Mark）",
]