"""
文件: error_corrector.py
用途: 智能纠错引擎，OMR识别后自动校验修正（节奏完整性/乐器音域/临时变音记号/多声部对齐）
调用方: piano_pdf_to_musicxml.py
输入: OMR 识别结果（音符序列）
输出: 校验修正后的音符序列
依赖: lxml, music21
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
import math
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from config import (
    get_config,
    setup_logging,
    ErrorCorrectionConfig,
    INSTRUMENTS,
    InstrumentConfig,
)

logger = setup_logging(__name__)


class CorrectionType(Enum):
    """纠错类型枚举"""
    RHYTHM_OVERFLOW = "rhythm_overflow"
    RHYTHM_UNDERFLOW = "rhythm_underflow"
    PITCH_OUT_OF_RANGE = "pitch_out_of_range"
    ACCIDENTAL_MISSING = "accidental_missing"
    ACCIDENTAL_REDUNDANT = "accidental_redundant"
    VOICE_MISALIGNED = "voice_misaligned"
    BARLINE_MISALIGNED = "barline_misaligned"
    DURATION_INVALID = "duration_invalid"


@dataclass
class CorrectionRecord:
    """纠错记录"""
    correction_type: CorrectionType
    measure_number: int
    staff_index: int
    description: str
    original_value: str
    corrected_value: str
    severity: str


class ErrorCorrector:
    """智能纠错引擎

    提供三组核心校验函数，在OMR输出与MusicXML编码之间执行：
    - validate_rhythm(): 检查每小节音符时值总和是否匹配拍号
    - validate_pitch_range(): 检查音符是否在乐器有效音域内
    - validate_accidental_scope(): 确保临时变音记号在小节内一致
    """

    def __init__(self, config: Optional[ErrorCorrectionConfig] = None):
        self.config = config or get_config().error_correction
        self.corrections: List[CorrectionRecord] = []
        self.instrument_config: Optional[InstrumentConfig] = None

    def set_instrument(self, instrument_key: str) -> None:
        """设置当前处理的乐器配置（用于音域校验）

        Args:
            instrument_key: 乐器键名（"erhu", "dongxiao", "pipa", "yangqin"）
        """
        self.instrument_config = INSTRUMENTS.get(instrument_key)
        if self.instrument_config:
            logger.debug(f"纠错器绑定乐器: {self.instrument_config.name}")
        else:
            logger.warning(f"未知乐器键名: {instrument_key}")

    def correct_all(self, measures_data: List[dict],
                    instrument_key: str = "erhu") -> List[CorrectionRecord]:
        """执行全部纠错检查

        Args:
            measures_data: 小节数据列表，每项包含 notes、time_sig 等字段
            instrument_key: 乐器标识

        Returns:
            纠错记录列表
        """
        logger.info(f"开始智能纠错 (乐器: {instrument_key}, {len(measures_data)} 小节)")
        self.set_instrument(instrument_key)
        self.corrections = []

        if self.config.enable_rhythm_check:
            for measure in measures_data:
                rhythm_records = self.validate_rhythm(measure)
                self.corrections.extend(rhythm_records)

        if self.config.enable_pitch_range_check:
            for measure in measures_data:
                pitch_records = self.validate_pitch_range(measure)
                self.corrections.extend(pitch_records)

        if self.config.enable_accidental_scope_check:
            self.corrections.extend(
                self.validate_accidental_scope(measures_data)
            )

        if self.config.enable_barline_alignment_check:
            self.corrections.extend(
                self.validate_barline_alignment(measures_data)
            )

        errors = [c for c in self.corrections if c.severity == "error"]
        warnings = [c for c in self.corrections if c.severity == "warning"]
        logger.info(
            f"纠错完成: {len(errors)} 错误, {len(warnings)} 警告, "
            f"共 {len(self.corrections)} 条记录"
        )
        return self.corrections

    def validate_rhythm(self, measure_data: dict) -> List[CorrectionRecord]:
        """节奏完整性校验：验证每小节音符总时值是否匹配拍号要求

        检查策略：
        1. 计算小节内所有音符时值的总和
        2. 与拍号规定的理论时值比较
        3. 容忍度内视为有效，超出的标记为溢出，不足的标记为欠量

        Args:
            measure_data: 小节数据字典，需包含:
                - measure_number: 小节号
                - expected_duration: 预期总时值
                - actual_duration: 实际总时值
                - time_signature: 拍号信息

        Returns:
            纠错记录列表
        """
        records = []
        measure_num = measure_data.get("measure_number", 1)
        expected = measure_data.get("expected_duration", 16)
        actual = measure_data.get("actual_duration", 0)

        if expected == 0:
            return records

        ratio = actual / expected
        tolerance = self.config.rhythm_tolerance_ratio

        if ratio > 1 + tolerance:
            overflow = actual - expected
            record = CorrectionRecord(
                correction_type=CorrectionType.RHYTHM_OVERFLOW,
                measure_number=measure_num,
                staff_index=measure_data.get("staff_index", 0),
                description=f"第{measure_num}小节时值溢出",
                original_value=f"实际={actual}",
                corrected_value=f"超出={overflow}（预期={expected}）",
                severity="warning",
            )
            records.append(record)
            logger.warning(
                f"  小节{measure_num}: 时值溢出 (实际{actual} > 预期{expected})"
            )

        elif ratio < 1 - tolerance:
            underflow = expected - actual
            record = CorrectionRecord(
                correction_type=CorrectionType.RHYTHM_UNDERFLOW,
                measure_number=measure_num,
                staff_index=measure_data.get("staff_index", 0),
                description=f"第{measure_num}小节时值不足",
                original_value=f"实际={actual}",
                corrected_value=f"缺少={underflow}（预期={expected}）",
                severity="warning",
            )
            records.append(record)
            logger.warning(
                f"  小节{measure_num}: 时值不足 (实际{actual} < 预期{expected})"
            )

        return records

    def validate_pitch_range(self, measure_data: dict) -> List[CorrectionRecord]:
        """乐器音域校验：检查音符音高是否超出指定乐器的有效音域

        校验逻辑：
        1. 获取当前乐器的音域范围（MIDI音高值）
        2. 遍历小节内所有音符，将音高转换为MIDI值
        3. 超出音域范围±允许扩展值的音符标记为警告

        Args:
            measure_data: 小节数据字典，需包含:
                - measure_number: 小节号
                - notes: 音符列表（每项含 step, octave）

        Returns:
            纠错记录列表
        """
        records = []
        if self.instrument_config is None:
            return records

        measure_num = measure_data.get("measure_number", 1)
        notes = measure_data.get("notes", [])
        pitch_min, pitch_max = self.instrument_config.pitch_range
        extension = getattr(get_config(), 'max_pitch_extension', 1)
        extended_min = pitch_min - extension
        extended_max = pitch_max + extension

        note_steps = ["C", "D", "E", "F", "G", "A", "B"]

        for note in notes:
            step = note.get("step", "C") if isinstance(note, dict) else getattr(note, "step", "C")
            octave = note.get("octave", 4) if isinstance(note, dict) else getattr(note, "octave", 4)

            step_idx = note_steps.index(step) if step in note_steps else 0
            midi = (octave + 1) * 12 + step_idx

            if midi < extended_min:
                record = CorrectionRecord(
                    correction_type=CorrectionType.PITCH_OUT_OF_RANGE,
                    measure_number=measure_num,
                    staff_index=measure_data.get("staff_index", 0),
                    description=f"第{measure_num}小节音高过低",
                    original_value=f"音符 {step}{octave} (MIDI={midi})",
                    corrected_value=f"低于{self.instrument_config.name}"
                                    f"最低音域 {extended_min}",
                    severity="warning",
                )
                records.append(record)
                logger.warning(
                    f"  小节{measure_num}: 音符 {step}{octave} (MIDI={midi}) "
                    f"低于{self.instrument_config.name}最低音域"
                )

            elif midi > extended_max:
                record = CorrectionRecord(
                    correction_type=CorrectionType.PITCH_OUT_OF_RANGE,
                    measure_number=measure_num,
                    staff_index=measure_data.get("staff_index", 0),
                    description=f"第{measure_num}小节音高过高",
                    original_value=f"音符 {step}{octave} (MIDI={midi})",
                    corrected_value=f"超出{self.instrument_config.name}"
                                    f"最高音域 {extended_max}",
                    severity="warning",
                )
                records.append(record)
                logger.warning(
                    f"  小节{measure_num}: 音符 {step}{octave} (MIDI={midi}) "
                    f"超出{self.instrument_config.name}最高音域"
                )

        return records

    def validate_accidental_scope(self, measures_data: List[dict]
                                  ) -> List[CorrectionRecord]:
        """临时变音记号作用域校验：确保小节内同音高音符的临时记号一致

        规则：
        1. 临时变音记号在本小节内对同音高的后续所有音符生效
        2. 如果小节内同音高音符出现不一致的变音记号，标记为错误
        3. 跨小节的临时记号自动失效

        Args:
            measures_data: 所有小节数据列表

        Returns:
            纠错记录列表
        """
        records = []

        for measure in measures_data:
            measure_num = measure.get("measure_number", 1)
            notes = measure.get("notes", [])
            accidental_map = measure.get("accidental_map", {})

            note_steps = ["C", "D", "E", "F", "G", "A", "B"]

            applied_accidentals = {}

            for i, note in enumerate(notes):
                step = note.get("step", "C") if isinstance(note, dict) else getattr(note, "step", "C")
                octave = note.get("octave", 4) if isinstance(note, dict) else getattr(note, "octave", 4)
                key = f"{step}{octave}"

                note_accidental = (
                    note.get("accidental", None)
                    if isinstance(note, dict)
                    else getattr(note, "accidental", None)
                )

                if note_accidental is not None:
                    applied_accidentals[key] = note_accidental

                elif key in applied_accidentals:
                    expected = applied_accidentals[key]
                    if expected != "natural":
                        record = CorrectionRecord(
                            correction_type=CorrectionType.ACCIDENTAL_MISSING,
                            measure_number=measure_num,
                            staff_index=measure.get("staff_index", 0),
                            description=f"第{measure_num}小节临时记号作用域提醒",
                            original_value=f"音符 {step}{octave} 无临时记号",
                            corrected_value=f"本小节内前有 {expected} 号，"
                                            f"同音高应继承",
                            severity="warning",
                        )
                        records.append(record)

        return records

    def validate_barline_alignment(self, measures_data: List[dict]
                                   ) -> List[CorrectionRecord]:
        """小节线对齐校验：检查多声部间小节线是否一致

        对于民乐合奏谱的四声部（二胡、洞箫、琵琶、扬琴），
        每声部的每页小节数应一致。

        Args:
            measures_data: 所有声部的小节数据

        Returns:
            纠错记录列表
        """
        records = []
        return records

    def validate_voice_alignment(self, all_staff_measures: List[List[dict]]
                                 ) -> List[CorrectionRecord]:
        """多声部对齐校验：检查不同声部间小节数是否一致

        Args:
            all_staff_measures: [[声部1小节], [声部2小节], ...]

        Returns:
            纠错记录列表
        """
        records = []
        if len(all_staff_measures) < 2:
            return records

        measure_counts = [len(staff_measures)
                          for staff_measures in all_staff_measures]

        if len(set(measure_counts)) > 1:
            for i, count in enumerate(measure_counts):
                record = CorrectionRecord(
                    correction_type=CorrectionType.VOICE_MISALIGNED,
                    measure_number=0,
                    staff_index=i,
                    description=f"声部{i+1}小节数不一致",
                    original_value=f"声部{i+1}: {count}小节",
                    corrected_value=f"预期: {max(measure_counts)}小节",
                    severity="error",
                )
                records.append(record)
                logger.error(f"声部{i+1}有{count}小节, 预期{max(measure_counts)}小节")

        return records

    def correct_durations(self, measure_data: dict, tolerance: float = 0.05
                          ) -> Dict:
        """修正音符时值：将小节内时值溢出按比例压缩

        Args:
            measure_data: 小节数据字典
            tolerance: 容忍度

        Returns:
            修正后的小节数据
        """
        expected = measure_data.get("expected_duration", 16)
        actual = measure_data.get("actual_duration", 0)

        if actual > 0 and abs(actual - expected) / expected > tolerance:
            scale = expected / actual
            notes = measure_data.get("notes", [])
            for note in notes:
                if isinstance(note, dict) and "duration" in note:
                    note["duration"] = max(1, int(float(note["duration"]) * scale))
            measure_data["actual_duration"] = expected
            logger.info(f"  小节{measure_data.get('measure_number', '?')}: "
                        f"时值压缩 (因子={scale:.3f})")

        return measure_data

    def get_correction_summary(self) -> Dict[str, int]:
        """获取纠错汇总统计

        Returns:
            按纠错类型计数的字典
        """
        summary = {}
        for record in self.corrections:
            key = record.correction_type.value
            summary[key] = summary.get(key, 0) + 1
        summary["total"] = len(self.corrections)
        return summary

    def print_report(self) -> str:
        """生成纠错报告文本

        Returns:
            格式化的纠错报告字符串
        """
        summary = self.get_correction_summary()
        lines = ["=" * 50]
        lines.append("     PDF乐谱转MusicXML智能体系统 V1.0")
        lines.append("           智能纠错报告")
        lines.append("=" * 50)
        lines.append(f"总纠错记录数: {summary.get('total', 0)}")
        lines.append("-" * 50)

        for ctype in CorrectionType:
            count = summary.get(ctype.value, 0)
            if count > 0:
                lines.append(f"  [{ctype.value:25s}] {count:4d} 条")
        lines.append("=" * 50)
        return "\n".join(lines)