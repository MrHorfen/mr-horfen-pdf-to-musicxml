"""
文件: pdf_rules.py
用途: PDF乐谱处理规则库，沉淀历史转谱经验，按谱子类型提供标准化处理方案
调用方: converter_service.py
输入: 谱子类型/特征
输出: 标准化处理策略（预处理/声部/后处理）
依赖: 无（纯规则库）
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class ScoreType(Enum):
    """乐谱类型枚举"""
    PIANO_SOLO = "piano_solo"              # 钢琴独奏（双声部大谱表）
    PIANO_SINGLE = "piano_single"          # 钢琴独奏（单声部单行谱）
    PIANO_VOCAL = "piano_vocal"            # 钢琴伴奏+人声（五线谱）
    PIANO_JIANPU = "piano_jianpu"          # 钢琴伴奏+简谱人声
    CHOIR = "choir"                        # 合唱谱（多声部无伴奏或有伴奏）
    ORCHESTRA = "orchestra"                # 交响乐总谱（24+声部）
    FOLK_ENSEMBLE = "folk_ensemble"        # 民乐合奏（二胡/洞箫/琵琶/扬琴）
    GUITAR_TAB = "guitar_tab"              # 吉他六线谱
    UNKNOWN = "unknown"


@dataclass
class PreprocessStrategy:
    """PDF预处理策略"""
    # 是否需要页面切割（PDF含多首曲子）
    needs_cutting: bool = False
    # 切割模式：'page_range'（按页范围）| 'horizontal'（水平切割）| 'both'
    cut_mode: str = ""
    # 是否需要歪斜校正
    needs_deskew: bool = False
    # 歪斜检测角度范围（度）
    deskew_angle_range: float = 5.0
    # 渲染DPI
    dpi: int = 300
    # 是否需要图像标准化（扫描件/拍照件）
    needs_standardization: bool = False
    # 最大像素数限制（Audiveris硬性限制）
    max_pixels: int = 20_000_000
    # 是否灰度输出
    grayscale: bool = True
    # 超大页面自动缩放（页面尺寸超过A4的1.5倍时触发）
    oversized_page_scale: bool = True


@dataclass
class VoiceStrategy:
    """声部处理策略"""
    # 声部总数
    voice_count: int = 1
    # 声部名称列表
    voice_names: List[str] = field(default_factory=list)
    # 是否使用"先建框架再填内容"模式
    use_framework_mode: bool = False
    # 各声部起始小节（声部名 → 起始小节号，0表示从头开始）
    voice_start_measures: Dict[str, int] = field(default_factory=dict)
    # 钢琴是否保持大谱表（staves=2，不拆分）
    piano_grand_staff: bool = False
    # 是否需要排除简谱干扰
    exclude_jianpu: bool = False
    # 是否需要动态声部映射（合唱/交响乐）
    dynamic_voice_mapping: bool = False


@dataclass
class PostprocessStrategy:
    """后处理策略"""
    # 是否需要谱号修正
    needs_clef_fix: bool = False
    # 是否需要voice数归一化
    needs_voice_normalization: bool = False
    # 是否需要measure number连续化
    needs_measure_renumber: bool = True
    # 是否需要移除布局标记（system-layout等）
    remove_layout_marks: bool = True
    # 是否需要休止小节填充
    needs_rest_fill: bool = False


@dataclass
class ProcessingRule:
    """完整处理规则"""
    score_type: ScoreType
    name: str
    description: str
    preprocess: PreprocessStrategy
    voice: VoiceStrategy
    postprocess: PostprocessStrategy
    # Audiveris参数
    audiveris_params: Dict = field(default_factory=dict)


# ============================================================
# 规则库：每种谱子类型的标准处理方案
# ============================================================

RULES: Dict[ScoreType, ProcessingRule] = {
    ScoreType.PIANO_SOLO: ProcessingRule(
        score_type=ScoreType.PIANO_SOLO,
        name="钢琴独奏（双声部大谱表）",
        description="标准钢琴谱，高音谱表+低音谱表，staves=2",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
            needs_standardization=False,
        ),
        voice=VoiceStrategy(
            voice_count=1,  # 1个part，staves=2
            voice_names=["钢琴"],
            use_framework_mode=False,
            piano_grand_staff=True,
        ),
        postprocess=PostprocessStrategy(
            needs_clef_fix=False,
            needs_voice_normalization=True,
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={"twoStaves": True},
    ),

    ScoreType.PIANO_SINGLE: ProcessingRule(
        score_type=ScoreType.PIANO_SINGLE,
        name="钢琴独奏（单声部）",
        description="单行谱表，如变奏曲主题",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=1,
            voice_names=["钢琴"],
            use_framework_mode=False,
            piano_grand_staff=False,
        ),
        postprocess=PostprocessStrategy(
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={},
    ),

    ScoreType.PIANO_VOCAL: ProcessingRule(
        score_type=ScoreType.PIANO_VOCAL,
        name="钢琴伴奏+人声（五线谱）",
        description="人声用五线谱记谱，钢琴用大谱表",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=3,  # 人声I + 人声II + 钢琴
            voice_names=["人声I", "人声II", "钢琴"],
            use_framework_mode=True,  # 先建框架再填内容
            voice_start_measures={"人声I": 10, "人声II": 27},  # 示例值，实际需用户确认
            piano_grand_staff=True,
        ),
        postprocess=PostprocessStrategy(
            needs_rest_fill=True,
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={"twoStaves": True},
    ),

    ScoreType.PIANO_JIANPU: ProcessingRule(
        score_type=ScoreType.PIANO_JIANPU,
        name="钢琴伴奏+简谱人声",
        description="人声用简谱数字记谱，Audiveris天然不识别简谱",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=1,  # 只输出钢琴
            voice_names=["钢琴"],
            use_framework_mode=False,
            piano_grand_staff=True,
            exclude_jianpu=True,
        ),
        postprocess=PostprocessStrategy(
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={"twoStaves": True},
    ),

    ScoreType.CHOIR: ProcessingRule(
        score_type=ScoreType.CHOIR,
        name="合唱谱（多声部）",
        description="4-7声部，需动态声部映射，声部休止管理",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=4,  # 默认4声部，实际动态检测
            voice_names=["Soprano", "Alto", "Tenor", "Bass"],
            use_framework_mode=True,
            dynamic_voice_mapping=True,
        ),
        postprocess=PostprocessStrategy(
            needs_rest_fill=True,
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={},
    ),

    ScoreType.ORCHESTRA: ProcessingRule(
        score_type=ScoreType.ORCHESTRA,
        name="交响乐总谱（24+声部）",
        description="多声部总谱，处理方式类似合唱但声部更多",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=24,  # 默认24声部，实际动态检测
            voice_names=[],  # 动态填充
            use_framework_mode=True,
            dynamic_voice_mapping=True,
        ),
        postprocess=PostprocessStrategy(
            needs_rest_fill=True,
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={},
    ),

    ScoreType.FOLK_ENSEMBLE: ProcessingRule(
        score_type=ScoreType.FOLK_ENSEMBLE,
        name="民乐合奏（二胡/洞箫/琵琶/扬琴）",
        description="4声部民乐合奏，需检测民乐符号",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=4,
            voice_names=["二胡", "洞箫", "琵琶", "扬琴"],
            use_framework_mode=True,
        ),
        postprocess=PostprocessStrategy(
            needs_rest_fill=True,
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={},
    ),

    ScoreType.GUITAR_TAB: ProcessingRule(
        score_type=ScoreType.GUITAR_TAB,
        name="吉他六线谱",
        description="1-2声部，六线TAB谱",
        preprocess=PreprocessStrategy(
            needs_cutting=False,
            needs_deskew=False,
            dpi=300,
        ),
        voice=VoiceStrategy(
            voice_count=1,
            voice_names=["吉他"],
            use_framework_mode=False,
        ),
        postprocess=PostprocessStrategy(
            needs_measure_renumber=True,
            remove_layout_marks=True,
        ),
        audiveris_params={},
    ),
}


# ============================================================
# 辅助函数
# ============================================================

def get_rule(score_type: ScoreType) -> ProcessingRule:
    """获取指定类型的处理规则"""
    return RULES.get(score_type, RULES[ScoreType.PIANO_SOLO])


def get_rule_by_name(type_name: str) -> ProcessingRule:
    """通过类型名称获取处理规则"""
    try:
        st = ScoreType(type_name)
    except ValueError:
        st = ScoreType.UNKNOWN
    return get_rule(st)


def list_all_rules() -> List[Dict]:
    """列出所有可用规则（供前端展示）"""
    result = []
    for st, rule in RULES.items():
        result.append({
            "type": st.value,
            "name": rule.name,
            "description": rule.description,
            "voice_count": rule.voice.voice_count,
            "voice_names": rule.voice.voice_names,
            "needs_cutting": rule.preprocess.needs_cutting,
            "needs_deskew": rule.preprocess.needs_deskew,
            "piano_grand_staff": rule.voice.piano_grand_staff,
            "exclude_jianpu": rule.voice.exclude_jianpu,
        })
    return result


def infer_score_type(
    instrument: str = "",
    voices: str = "",
    notes: str = "",
) -> ScoreType:
    """
    根据用户输入推断乐谱类型

    Args:
        instrument: 乐器类型（piano/guitar/violin/other）
        voices: 声部类型（single/grand_staff/multi）
        notes: 用户特殊说明文本

    Returns:
        推断的乐谱类型
    """
    notes_lower = (notes or "").lower()

    # 简谱检测
    if "简谱" in notes_lower or "jianpu" in notes_lower:
        return ScoreType.PIANO_JIANPU

    # 合唱/交响乐检测
    if "合唱" in notes_lower or "choir" in notes_lower:
        return ScoreType.CHOIR
    if "交响" in notes_lower or "orchestra" in notes_lower or "总谱" in notes_lower:
        return ScoreType.ORCHESTRA

    # 民乐检测
    if "民乐" in notes_lower or "二胡" in notes_lower or "folk" in notes_lower:
        return ScoreType.FOLK_ENSEMBLE

    # 吉他检测
    if instrument == "guitar" or "吉他" in notes_lower or "tab" in notes_lower:
        return ScoreType.GUITAR_TAB

    # 钢琴系列
    if instrument == "piano" or instrument == "":
        if voices == "single":
            return ScoreType.PIANO_SINGLE
        elif voices == "multi":
            return ScoreType.PIANO_VOCAL
        else:
            # 检查是否有人声
            if "人声" in notes_lower or "声乐" in notes_lower or "伴奏" in notes_lower:
                return ScoreType.PIANO_VOCAL
            return ScoreType.PIANO_SOLO

    return ScoreType.UNKNOWN


def build_processing_plan(
    score_type: ScoreType,
    notes: str = "",
    user_overrides: Optional[Dict] = None,
) -> Dict:
    """
    构建完整的处理方案

    Args:
        score_type: 乐谱类型
        notes: 用户特殊说明
        user_overrides: 用户覆盖的参数

    Returns:
        处理方案字典
    """
    rule = get_rule(score_type)
    overrides = user_overrides or {}
    notes_lower = (notes or "").lower()

    plan = {
        "score_type": score_type.value,
        "rule_name": rule.name,
        "preprocess": {
            "needs_cutting": rule.preprocess.needs_cutting,
            "cut_mode": rule.preprocess.cut_mode,
            "needs_deskew": rule.preprocess.needs_deskew,
            "dpi": overrides.get("dpi", rule.preprocess.dpi),
            "needs_standardization": rule.preprocess.needs_standardization,
            "max_pixels": rule.preprocess.max_pixels,
            "grayscale": rule.preprocess.grayscale,
        },
        "voice": {
            "voice_count": rule.voice.voice_count,
            "voice_names": rule.voice.voice_names,
            "use_framework_mode": rule.voice.use_framework_mode,
            "voice_start_measures": rule.voice.voice_start_measures,
            "piano_grand_staff": rule.voice.piano_grand_staff,
            "exclude_jianpu": rule.voice.exclude_jianpu,
            "dynamic_voice_mapping": rule.voice.dynamic_voice_mapping,
        },
        "postprocess": {
            "needs_clef_fix": rule.postprocess.needs_clef_fix,
            "needs_voice_normalization": rule.postprocess.needs_voice_normalization,
            "needs_measure_renumber": rule.postprocess.needs_measure_renumber,
            "remove_layout_marks": rule.postprocess.remove_layout_marks,
            "needs_rest_fill": rule.postprocess.needs_rest_fill,
        },
        "audiveris_params": rule.audiveris_params,
    }

    # 根据notes关键词覆盖参数
    if "歪斜" in notes_lower or "倾斜" in notes_lower or "skew" in notes_lower:
        plan["preprocess"]["needs_deskew"] = True

    if "多首" in notes_lower or "第" in notes_lower and "首" in notes_lower:
        plan["preprocess"]["needs_cutting"] = True
        plan["preprocess"]["cut_mode"] = "page_range"

    if "只取钢琴" in notes_lower or "不要人声" in notes_lower or "排除人声" in notes_lower:
        plan["preprocess"]["needs_cutting"] = True
        plan["preprocess"]["cut_mode"] = "horizontal"

    if "扫描" in notes_lower or "拍照" in notes_lower:
        plan["preprocess"]["needs_standardization"] = True

    if "高音谱号" in notes_lower or "低音谱号" in notes_lower:
        plan["postprocess"]["needs_clef_fix"] = True

    return plan
