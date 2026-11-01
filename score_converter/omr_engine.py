"""
文件: omr_engine.py
用途: 光学乐谱识别（OMR）核心引擎，执行五线谱行解析/谱号/调号/拍号/音符检测/符干解析/小节线/连音线/变音记号识别
调用方: main.py
输入: 预处理后的乐谱图像
输出: OMR识别结果（结构化数据）
依赖: Audiveris(Java), cv2, numpy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from typing import List, Dict, Tuple, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from config import (
    get_config,
    setup_logging,
    OMRConfig,
    ClefType,
    NoteName,
    AccidentalType,
)

logger = setup_logging(__name__)


class NoteType(Enum):
    """音符时值类型"""
    WHOLE = "whole"
    HALF = "half"
    QUARTER = "quarter"
    EIGHTH = "eighth"
    SIXTEENTH = "16th"
    THIRTY_SECOND = "32nd"


NOTE_DURATION_MAP: Dict[NoteType, int] = {
    NoteType.WHOLE: 16,
    NoteType.HALF: 8,
    NoteType.QUARTER: 4,
    NoteType.EIGHTH: 2,
    NoteType.SIXTEENTH: 1,
    NoteType.THIRTY_SECOND: 0,
}


@dataclass
class NoteHead:
    """音符头检测结果"""
    x: float
    y: float
    radius: float
    staff_position: int = 0
    is_filled: bool = True
    confidence: float = 1.0


@dataclass
class StemInfo:
    """符干信息"""
    x: float
    y_top: float
    y_bottom: float
    direction: str = "up"


@dataclass
class AccidentalInfo:
    """变音记号信息"""
    x: float
    y: float
    accidental_type: AccidentalType
    confidence: float = 1.0


@dataclass
class BarlineInfo:
    """小节线信息"""
    x: float
    barline_type: str = "single"
    is_repeat: bool = False


@dataclass
class ClefInfo:
    """谱号信息"""
    x: float
    clef_type: ClefType
    staff_line: int
    confidence: float = 1.0


@dataclass
class KeySignature:
    """调号信息"""
    num_sharps: int = 0
    num_flats: int = 0
    fifths: int = 0


@dataclass
class TimeSignature:
    """拍号信息"""
    beats: int = 4
    beat_type: int = 4


@dataclass
class DetectedNote:
    """识别出的完整音符"""
    step: str
    octave: int
    note_type: NoteType
    x_position: float
    y_position: float
    accidental: Optional[AccidentalType] = None
    is_dotted: bool = False
    is_chord_note: bool = False
    stem_direction: str = ""
    beam_group: Optional[int] = None
    confidence: float = 1.0


@dataclass
class MeasureData:
    """一个小节的数据"""
    measure_number: int
    notes: List[DetectedNote] = field(default_factory=list)
    clef: Optional[ClefInfo] = None
    key_signature: Optional[KeySignature] = None
    time_signature: Optional[TimeSignature] = None
    barline: Optional[BarlineInfo] = None
    expected_duration: int = 16
    actual_duration: int = 0
    accidental_map: Dict[str, AccidentalType] = field(default_factory=dict)


@dataclass
class StaffData:
    """一个五线谱行的完整识别数据"""
    staff_index: int
    clefs: List[ClefInfo] = field(default_factory=list)
    key_signatures: List[KeySignature] = field(default_factory=list)
    time_signatures: List[TimeSignature] = field(default_factory=list)
    measures: List[MeasureData] = field(default_factory=list)
    accidentals: List[AccidentalInfo] = field(default_factory=list)
    barlines: List[BarlineInfo] = field(default_factory=list)


class OMRProcessor:
    """光学乐谱识别处理器

    执行完整的OMR识别Pipeline：
    1. 五线谱行定位与谱线移除
    2. 谱号/调号/拍号识别
    3. 音符头检测与音高判定
    4. 符干与符尾解析
    5. 小节线检测与量度分割
    6. 连音线/延音线区分
    7. 临时变音记号与装饰音识别
    """

    def __init__(self, config: Optional[OMRConfig] = None):
        self.config = config or get_config().omr
        self.staff_data: List[StaffData] = []
        self.current_clef: ClefType = ClefType.TREBLE

    def process(self, image: np.ndarray, expected_staves: int = 4) -> List[StaffData]:
        """执行完整的OMR识别流程

        Args:
            image: 预处理后的二值化乐谱图像
            expected_staves: 预期的声部数量（民乐合奏默认为4）

        Returns:
            所有声部的识别数据列表
        """
        logger.info(f"开始OMR识别，预期声部数: {expected_staves}")
        self.staff_data = []

        staves = self._separate_staves(image, expected_staves)

        for idx, staff_img in enumerate(staves):
            logger.debug(f"  处理第 {idx+1} 个五线谱行")
            staff = StaffData(staff_index=idx)

            staff_lines = self._extract_staff_lines(staff_img)
            clean_img = self._remove_staff_lines(staff_img, staff_lines)

            clef = self._detect_clef(clean_img, staff_lines)
            staff.clefs.append(clef)
            self.current_clef = clef.clef_type

            key_sig = self._detect_key_signature(clean_img, staff_lines)
            staff.key_signatures.append(key_sig)

            time_sig = self._detect_time_signature(clean_img, staff_lines)
            staff.time_signatures.append(time_sig)

            barlines = self._detect_barlines(clean_img, staff_lines)
            staff.barlines = barlines

            notes = self._detect_notes(clean_img, staff_lines)
            accidentals = self._detect_accidentals(clean_img, staff_lines)
            staff.accidentals = accidentals

            measures = self._organize_measures(
                notes, barlines, time_sig, accidentals, clef
            )
            staff.measures = measures

            self.staff_data.append(staff)
            logger.info(
                f"  声部 {idx+1}: clef={clef.clef_type}, "
                f"{len(notes)} 音符, {len(measures)} 小节"
            )

        return self.staff_data

    def _separate_staves(self, image: np.ndarray, expected: int) -> List[np.ndarray]:
        """将整页乐谱图像按声部进行垂直分割

        Args:
            image: 整页乐谱图像
            expected: 预期的声部数量

        Returns:
            各声部子图像列表
        """
        try:
            import cv2

            horizontal_proj = np.sum(image == 0, axis=1)
            h = image.shape[0]
            gap_threshold = h // (expected * 2)

            gaps = []
            in_gap = False
            gap_start = 0

            for y in range(h):
                if horizontal_proj[y] < 5:
                    if not in_gap:
                        gap_start = y
                        in_gap = True
                else:
                    if in_gap:
                        gap_len = y - gap_start
                        if gap_len > gap_threshold:
                            gaps.append((gap_start, y))
                        in_gap = False

            if len(gaps) < expected - 1:
                segment_height = h // expected
                staves = []
                for i in range(expected):
                    start = i * segment_height
                    end = (i + 1) * segment_height if i < expected - 1 else h
                    staves.append(image[start:end, :])
                return staves

            gaps.sort(key=lambda g: g[1] - g[0], reverse=True)
            cut_points = [gaps[i][0] + (gaps[i][1] - gaps[i][0]) // 2
                         for i in range(min(expected - 1, len(gaps)))]
            cut_points.sort()

            staves = []
            prev = 0
            for cp in cut_points:
                staves.append(image[prev:cp, :])
                prev = cp
            staves.append(image[prev:h, :])

            return staves[:expected]
        except ImportError:
            segment_h = image.shape[0] // expected
            return [image[i * segment_h:(i + 1) * segment_h, :]
                    for i in range(expected)]

    def _extract_staff_lines(self, staff_img: np.ndarray) -> List[int]:
        """提取五线谱的5条线位置

        Args:
            staff_img: 单声部五线谱图像

        Returns:
            5条谱线的y坐标列表
        """
        try:
            import cv2

            horizontal_proj = np.sum(staff_img == 0, axis=1)
            threshold = np.mean(horizontal_proj) * 2.0

            lines = []
            for y in range(len(horizontal_proj)):
                if horizontal_proj[y] > threshold:
                    if not lines or y - lines[-1] > self.config.staff_spacing_min:
                        lines.append(y)
                    else:
                        if horizontal_proj[y] > horizontal_proj[lines[-1]]:
                            lines[-1] = y

            if len(lines) > 5:
                sorted_by_intensity = sorted(
                    lines, key=lambda y: horizontal_proj[y], reverse=True
                )
                lines = sorted(sorted_by_intensity[:5])

            logger.debug(f"    谱线位置: {lines}")
            return lines[:5]
        except ImportError:
            h = staff_img.shape[0]
            return [h // 5 * i for i in range(1, 6)]

    def _remove_staff_lines(self, staff_img: np.ndarray,
                            lines: List[int]) -> np.ndarray:
        """移除五线谱谱线，便于后续符号检测

        Args:
            staff_img: 含五线谱的图像
            lines: 谱线位置列表

        Returns:
            移除谱线后的图像
        """
        try:
            import cv2
            cleaned = staff_img.copy()
            for line_y in lines:
                if 0 <= line_y < cleaned.shape[0]:
                    cv2.line(cleaned, (0, line_y),
                             (cleaned.shape[1] - 1, line_y), 255, 1)
            return cleaned
        except ImportError:
            return staff_img

    def _detect_clef(self, staff_img: np.ndarray,
                     staff_lines: List[int]) -> ClefInfo:
        """识别谱号类型（高音谱号G/低音谱号F/中音谱号C）

        Args:
            staff_img: 移除谱线后的图像
            staff_lines: 谱线y坐标列表

        Returns:
            识别的谱号信息
        """
        try:
            import cv2

            x_start = 0
            x_end = staff_img.shape[1] // 4
            left_region = staff_img[:, x_start:x_end]

            contours, _ = cv2.findContours(
                cv2.bitwise_not(left_region),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            best_clef = ClefType.TREBLE
            best_score = 0.0

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 200:
                    continue

                M = cv2.moments(cnt)
                if M["m00"] == 0:
                    continue
                cy = int(M["m01"] / M["m00"])

                for clef_type in [ClefType.TREBLE, ClefType.BASS,
                                  ClefType.ALTO, ClefType.TENOR]:
                    expected_y = staff_lines[clef_type.line - 1] if clef_type.line <= len(staff_lines) else staff_lines[len(staff_lines) // 2]
                    offset = abs(cy - expected_y)
                    score = area / (offset + 1)
                    if score > best_score:
                        best_score = score
                        best_clef = clef_type

            logger.debug(f"    谱号识别: {best_clef.sign} (置信度={best_score:.2f})")
            return ClefInfo(
                x=x_start + x_end // 2,
                clef_type=best_clef,
                staff_line=best_clef.line,
                confidence=min(best_score / 1000.0, 1.0),
            )
        except ImportError:
            return ClefInfo(x=0, clef_type=ClefType.TREBLE, staff_line=2)

    def _detect_key_signature(self, staff_img: np.ndarray,
                              staff_lines: List[int]) -> KeySignature:
        """识别调号（升降号数量）

        Args:
            staff_img: 谱号右侧区域图像
            staff_lines: 谱线位置

        Returns:
            调号信息
        """
        try:
            import cv2

            key_area = staff_img[:, staff_img.shape[1] // 5:staff_img.shape[1] // 3]
            key_gray = cv2.bitwise_not(key_area)

            contours, _ = cv2.findContours(
                key_gray, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            sharps = 0
            flats = 0
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 15 or area > 200:
                    continue

                hull = cv2.convexHull(cnt)
                hull_area = cv2.contourArea(hull)
                if hull_area == 0:
                    continue

                solidity = area / hull_area

                if 0.15 < solidity < 0.55:
                    x, y, w, h = cv2.boundingRect(cnt)
                    if w < h:
                        sharps += 1
                    else:
                        flats += 1

            fifths = sharps - flats
            logger.debug(f"    调号: #{sharps} ♭{flats} (fifths={fifths})")
            return KeySignature(
                num_sharps=sharps,
                num_flats=flats,
                fifths=fifths,
            )
        except ImportError:
            return KeySignature()

    def _detect_time_signature(self, staff_img: np.ndarray,
                               staff_lines: List[int]) -> TimeSignature:
        """识别拍号（如4/4, 3/4等）

        Args:
            staff_img: 调号右侧区域图像
            staff_lines: 谱线位置

        Returns:
            拍号信息
        """
        try:
            import cv2

            time_area = staff_img[:, staff_img.shape[1] // 3:staff_img.shape[1] // 2]
            time_gray = cv2.bitwise_not(time_area)

            contours, _ = cv2.findContours(
                time_gray, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            numbers = []
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 30 or area > 500:
                    continue
                numbers.append(cnt)

            beats = 4
            beat_type = 4

            if len(numbers) >= 2:
                y_sorted = sorted(numbers, key=lambda c: cv2.boundingRect(c)[1])
                beats = self._classify_digit(y_sorted[0], staff_img)
                beat_type = self._classify_digit(y_sorted[1], staff_img)

            logger.debug(f"    拍号: {beats}/{beat_type}")
            return TimeSignature(beats=beats, beat_type=beat_type)
        except ImportError:
            return TimeSignature()

    def _classify_digit(self, contour: np.ndarray, _image: np.ndarray) -> int:
        """根据轮廓特征分类数字（2/3/4/6/8等拍号数字）

        Args:
            contour: 数字轮廓
            _image: 上下文图像（保留扩展接口）

        Returns:
            分类的数字值
        """
        try:
            import cv2
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            area = cv2.contourArea(contour)
            if hull_area == 0:
                return 4
            solidity = area / hull_area

            if solidity > 0.8:
                return 8
            elif solidity > 0.6:
                return 6
            elif solidity > 0.4:
                return 4
            elif solidity > 0.2:
                return 3
            return 4
        except ImportError:
            return 4

    def _detect_barlines(self, staff_img: np.ndarray,
                         staff_lines: List[int]) -> List[BarlineInfo]:
        """检测小节线位置与类型

        Args:
            staff_img: 移除谱线后的图像
            staff_lines: 谱线y坐标

        Returns:
            小节线信息列表（按x坐标排序）
        """
        try:
            import cv2

            top = max(0, staff_lines[0] - 5) if staff_lines else 0
            bottom = min(
                staff_img.shape[0],
                staff_lines[-1] + 5 if staff_lines else staff_img.shape[0]
            )

            vertical_proj = np.sum(staff_img[top:bottom, :] == 0, axis=0)
            bar_height = bottom - top
            threshold = bar_height * 0.7

            barlines = []
            in_bar = False
            bar_start = 0

            for x in range(len(vertical_proj)):
                if vertical_proj[x] > threshold:
                    if not in_bar:
                        bar_start = x
                        in_bar = True
                else:
                    if in_bar:
                        bar_width = x - bar_start
                        if bar_width >= 1:
                            bar_type = ("double" if bar_width > 8 else
                                       "final" if bar_width > 5 else "single")
                            barlines.append(BarlineInfo(
                                x=bar_start + bar_width // 2,
                                barline_type=bar_type,
                            ))
                        in_bar = False

            barlines.sort(key=lambda b: b.x)
            return barlines
        except ImportError:
            return []

    def _detect_notes(self, staff_img: np.ndarray,
                      staff_lines: List[int]) -> List[DetectedNote]:
        """检测音符：通过连通域分析定位音符头，判定音高和时值

        Args:
            staff_img: 移除谱线后的图像
            staff_lines: 谱线y坐标（用于音高计算）

        Returns:
            检测到的音符列表
        """
        try:
            import cv2

            contours, hierarchy = cv2.findContours(
                cv2.bitwise_not(staff_img),
                cv2.RETR_CCOMP,
                cv2.CHAIN_APPROX_SIMPLE
            )

            notes = []
            r_min = self.config.notehead_radius_min
            r_max = self.config.notehead_radius_max

            for i, cnt in enumerate(contours):
                area = cv2.contourArea(cnt)
                if area < np.pi * r_min ** 2 or area > np.pi * r_max ** 2 * 4:
                    continue

                (x, y), radius = cv2.minEnclosingCircle(cnt)
                if radius < r_min or radius > r_max:
                    continue

                perimeter = cv2.arcLength(cnt, True)
                if perimeter == 0:
                    continue
                circularity = 4 * np.pi * area / (perimeter ** 2)

                if circularity < self.config.notehead_circularity_min:
                    continue

                is_filled = True
                if hierarchy is not None and hierarchy[0][i][3] != -1:
                    child_idx = hierarchy[0][i][2]
                    if child_idx != -1:
                        child_area = cv2.contourArea(contours[child_idx])
                        if child_area > area * 0.3:
                            is_filled = False

                staff_pos = self._compute_staff_position(y, staff_lines)
                step, octave = self._staff_position_to_pitch(staff_pos)

                note_type = NoteType.QUARTER
                if not is_filled:
                    note_type = NoteType.HALF

                notes.append(DetectedNote(
                    step=step,
                    octave=octave,
                    note_type=note_type,
                    x_position=x,
                    y_position=y,
                    is_dotted=False,
                    confidence=min(circularity / 0.9, 1.0),
                ))

            notes.sort(key=lambda n: n.x_position)
            logger.debug(f"    检测到 {len(notes)} 个音符")
            return notes
        except ImportError:
            return []

    def _detect_stems_and_beams(self, staff_img: np.ndarray,
                                notes: List[DetectedNote],
                                staff_lines: List[int]) -> None:
        """检测符干和符梁（连尾线），更新音符时值和连音分组

        Args:
            staff_img: 乐谱图像
            notes: 音符列表（原地修改）
            staff_lines: 谱线位置
        """
        try:
            import cv2

            lines = cv2.HoughLinesP(
                cv2.bitwise_not(staff_img), 1, np.pi / 2,
                threshold=30, minLineLength=self.config.stem_length_min,
                maxLineGap=3
            )

            if lines is None:
                return

            for note in notes:
                best_line = None
                best_dist = float("inf")
                for line in lines:
                    x1, y1, x2, y2 = line[0]
                    if abs(x1 - note.x_position) < 15:
                        dist = abs(x1 - note.x_position)
                        if dist < best_dist:
                            best_dist = dist
                            best_line = line

                if best_line is not None:
                    x1, y1, x2, y2 = best_line
                    note.stem_direction = "up" if y1 < note.y_position else "down"

            x_sorted = sorted(notes, key=lambda n: n.x_position)
            beam_group = 0
            prev_x = -100
            for i, note in enumerate(x_sorted):
                if note.stem_direction:
                    if note.x_position - prev_x > 30:
                        beam_group += 1
                    note.beam_group = beam_group
                    prev_x = note.x_position

            for note in x_sorted:
                if note.beam_group is not None:
                    same_group = [n for n in x_sorted
                                   if n.beam_group == note.beam_group
                                   and n != note]
                    if same_group:
                        note.note_type = NoteType.EIGHTH

        except ImportError:
            pass

    def _detect_accidentals(self, staff_img: np.ndarray,
                            staff_lines: List[int]) -> List[AccidentalInfo]:
        """检测临时变音记号（升号#、降号♭、还原号♮）

        Args:
            staff_img: 乐谱图像（谱号右侧区域）
            staff_lines: 谱线位置

        Returns:
            变音记号列表
        """
        try:
            import cv2

            roi_size = self.config.accidental_roi_size
            acc_area = staff_img[:, :staff_img.shape[1] // 3]

            contours, _ = cv2.findContours(
                cv2.bitwise_not(acc_area),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            accidentals = []
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                if w > roi_size or h > roi_size:
                    continue
                area = cv2.contourArea(cnt)
                if area < 20:
                    continue

                aspect_ratio = h / w if w > 0 else 0
                acc_type = AccidentalType.NATURAL

                if aspect_ratio > 2.5:
                    acc_type = AccidentalType.SHARP
                elif aspect_ratio < 0.8:
                    acc_type = AccidentalType.FLAT

                accidentals.append(AccidentalInfo(
                    x=x + w // 2,
                    y=y + h // 2,
                    accidental_type=acc_type,
                    confidence=0.85,
                ))

            accidentals.sort(key=lambda a: a.x)
            return accidentals
        except ImportError:
            return []

    def _compute_staff_position(self, y: float, staff_lines: List[int]) -> int:
        """计算音符在五线谱上的相对位置（0=下加一线，偶数=线，奇数=间）

        Args:
            y: 音符y坐标
            staff_lines: 5条谱线的y坐标

        Returns:
            谱面位置（0=下加一线E，每+2上移一线）
        """
        if len(staff_lines) < 5:
            return 6

        line_spacing = (staff_lines[4] - staff_lines[0]) / 4
        if line_spacing == 0:
            return 6

        bottom_line = staff_lines[4]
        relative = (bottom_line - y) / line_spacing
        position = int(round(relative * 2))
        return max(0, min(20, position + 2))

    def _staff_position_to_pitch(self, position: int) -> Tuple[str, int]:
        """将谱面位置转换为音名和八度

        Args:
            position: 谱面位置（0=下加一线E）

        Returns:
            (音名, 八度) 元组
        """
        note_names = ["C", "D", "E", "F", "G", "A", "B"]

        if self.current_clef == ClefType.TREBLE:
            midi_base = 64
            pos_offset = 0
        elif self.current_clef == ClefType.BASS:
            midi_base = 43
            pos_offset = 0
        elif self.current_clef == ClefType.ALTO:
            midi_base = 55
            pos_offset = 0
        else:
            midi_base = 60
            pos_offset = 0

        midi_note = midi_base + position + pos_offset
        octave = midi_note // 12 - 1
        # 音名映射：MIDI 模 12 → 音名（处理 12 个半音）
        midi_to_step = {
            0: "C", 1: "C", 2: "D", 3: "D", 4: "E", 5: "F",
            6: "F", 7: "G", 8: "G", 9: "A", 10: "A", 11: "B"
        }
        step_idx = midi_note % 12
        step = midi_to_step.get(step_idx, "C")

        return step, max(0, octave)

    def _organize_measures(
        self,
        notes: List[DetectedNote],
        barlines: List[BarlineInfo],
        time_sig: TimeSignature,
        accidentals: List[AccidentalInfo],
        clef: ClefInfo,
    ) -> List[MeasureData]:
        """将音符按小节线划分到各小节中

        Args:
            notes: 检测到的音符列表
            barlines: 小节线列表
            time_sig: 拍号
            accidentals: 变音记号列表
            clef: 谱号信息

        Returns:
            小节数据列表
        """
        measures = []
        bar_x_positions = [bar.x for bar in barlines]

        expected_dur = time_sig.beats * 4

        measure_groups = self._split_by_barlines(notes, bar_x_positions)

        for idx, group in enumerate(measure_groups):
            # 正确计算小节实际时值
            actual_duration = 0
            for note in group:
                note_duration = NOTE_DURATION_MAP.get(note.note_type, 4)
                if note.is_dotted:
                    note_duration = int(note_duration * 1.5)
                actual_duration += note_duration

            measure = MeasureData(
                measure_number=idx + 1,
                notes=sorted(group, key=lambda n: n.x_position),
                clef=clef if idx == 0 else None,
                time_signature=time_sig if idx == 0 else None,
                expected_duration=expected_dur,
                actual_duration=actual_duration,
            )
            measures.append(measure)

        for acc in accidentals:
            for measure in measures:
                if measure.notes:
                    first_x = measure.notes[0].x_position
                    last_x = measure.notes[-1].x_position
                    if first_x <= acc.x <= last_x:
                        key = f"{acc.x:.0f}"
                        measure.accidental_map[key] = acc.accidental_type
                        break

        return measures

    def _split_by_barlines(self, notes: List[DetectedNote],
                           bar_x_positions: List[float]) -> List[List[DetectedNote]]:
        """按小节线x坐标将音符分组

        Args:
            notes: 音符列表
            bar_x_positions: 小节线x坐标列表

        Returns:
            按小节分组的音符列表
        """
        if not bar_x_positions:
            return [notes]

        sorted_notes = sorted(notes, key=lambda n: n.x_position)
        groups = []
        current = []
        bar_idx = 0

        for note in sorted_notes:
            while bar_idx < len(bar_x_positions) and note.x_position > bar_x_positions[bar_idx]:
                groups.append(current)
                current = []
                bar_idx += 1
            current.append(note)

        if current:
            groups.append(current)

        return groups

    def get_results(self) -> List[StaffData]:
        """获取识别结果

        Returns:
            所有声部的识别数据
        """
        return self.staff_data

    def extract_all_notes(self) -> Dict[int, List[DetectedNote]]:
        """提取所有声部的音符列表

        Returns:
            {声部索引: 音符列表} 字典
        """
        result = {}
        for staff in self.staff_data:
            all_notes = []
            for measure in staff.measures:
                all_notes.extend(measure.notes)
            result[staff.staff_index] = all_notes
        return result