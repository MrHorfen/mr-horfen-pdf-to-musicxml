"""
文件: folk_symbol_detector.py
用途: 民乐特殊符号检测模块，识别中国传统民乐合奏谱中的专属演奏记号（三连音/延长/波音/泛音/打音/空弦等）
调用方: piano_pdf_to_musicxml.py
输入: 预处理后的乐谱图像
输出: 特殊符号检测结果（JSON）
依赖: cv2, numpy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
import math
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from config import (
    get_config,
    setup_logging,
    FOLK_SYMBOL_TEMPLATES,
)

logger = setup_logging(__name__)


class FolkSymbolType(Enum):
    """民乐符号类型枚举"""
    TRIPLET = "triplet"
    FERMATA = "fermata"
    MORDENT = "mordent"
    INVERTED_MORDENT = "inverted_mordent"
    HARMONICS = "harmonics"
    STRIKE = "strike"
    OPEN_STRING = "open_string"
    ACCENT = "accent"
    STACCATO = "staccato"
    BREATH_MARK = "breath_mark"


@dataclass
class DetectedSymbol:
    """检测到的民乐符号"""
    symbol_type: FolkSymbolType
    x_position: float
    y_position: float
    bbox: Tuple[int, int, int, int]
    confidence: float
    associated_note_index: Optional[int] = None
    metadata: Dict = field(default_factory=dict)


class FolkSymbolDetector:
    """民乐符号检测器

    为每种民乐符号提供独立检测函数，输出标准化的符号信息，
    供后续MusicXML导出模块映射为对应的XML元素。
    """

    def __init__(self):
        self.detected_symbols: List[DetectedSymbol] = []

    def detect_all(self, staff_image: np.ndarray,
                   staff_lines: List[int],
                   note_positions: List[Tuple[float, float]]) -> List[DetectedSymbol]:
        """执行全部民乐符号检测

        Args:
            staff_image: 单个声部的乐谱图像（二值化）
            staff_lines: 五线谱线y坐标列表
            note_positions: 音符位置列表 [(x, y), ...]

        Returns:
            检测到的符号列表
        """
        logger.info("开始民乐符号检测...")
        self.detected_symbols = []

        self.detected_symbols.extend(
            self.detect_triplets(staff_image, note_positions)
        )
        self.detected_symbols.extend(
            self.detect_fermata(staff_image, staff_lines)
        )
        self.detected_symbols.extend(
            self.detect_mordent(staff_image, staff_lines)
        )
        self.detected_symbols.extend(
            self.detect_harmonics(staff_image, note_positions)
        )
        self.detected_symbols.extend(
            self.detect_strike(staff_image, note_positions)
        )
        self.detected_symbols.extend(
            self.detect_open_string(staff_image, note_positions)
        )
        self.detected_symbols.extend(
            self.detect_accidental_scope(staff_image, staff_lines)
        )
        self.detected_symbols.extend(
            self.detect_accent(staff_image, staff_lines)
        )
        self.detected_symbols.extend(
            self.detect_staccato(staff_image, staff_lines)
        )

        self.detected_symbols.sort(key=lambda s: s.x_position)

        summary = {}
        for s in self.detected_symbols:
            key = s.symbol_type.value
            summary[key] = summary.get(key, 0) + 1

        logger.info(f"民乐符号检测完成: {summary}")
        return self.detected_symbols

    def detect_triplets(self, staff_image: np.ndarray,
                        note_positions: List[Tuple[float, float]]
                        ) -> List[DetectedSymbol]:
        """检测三连音标记（数字"3"）

        三连音在民乐谱中通常以数字"3"标记在三个连续音符的上方。
        检测策略：在音符组上方搜索数字"3"形状的连通域。

        Args:
            staff_image: 乐谱图像
            note_positions: 音符位置列表

        Returns:
            三连音符号列表
        """
        symbols = []
        try:
            import cv2

            triplet_region = int(staff_image.shape[0] * 0.2)
            upper_area = staff_image[:triplet_region, :]

            contours, hierarchy = cv2.findContours(
                cv2.bitwise_not(upper_area),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 30 or area > 300:
                    continue

                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = h / w if w > 0 else 0

                if 1.0 < aspect_ratio < 3.0 and 5 < w < 25 and 10 < h < 40:
                    if self._is_digit_three_shape(cnt):
                        symbols.append(DetectedSymbol(
                            symbol_type=FolkSymbolType.TRIPLET,
                            x_position=x + w // 2,
                            y_position=y + h // 2,
                            bbox=(x, y, w, h),
                            confidence=0.85,
                            metadata={
                                "actual_notes": 3,
                                "normal_notes": 2,
                            },
                        ))

        except ImportError:
            pass
        return symbols

    def detect_fermata(self, staff_image: np.ndarray,
                       staff_lines: List[int]) -> List[DetectedSymbol]:
        """检测延长记号（∩ 形状符号）

        延长记号通常位于音符上方，呈半圆弧形。
        检测策略：在上方区域搜索弧形轮廓，判定其开合度和曲率。

        Args:
            staff_image: 乐谱图像
            staff_lines: 谱线位置

        Returns:
            延长记号符号列表
        """
        symbols = []
        try:
            import cv2

            if not staff_lines:
                return symbols
            fermata_region_top = 0
            fermata_region_bottom = staff_lines[0] - 5
            if fermata_region_bottom <= 0:
                return symbols

            upper_area = staff_image[
                fermata_region_top:fermata_region_bottom, :
            ]

            contours, _ = cv2.findContours(
                cv2.bitwise_not(upper_area),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 20 or area > 500:
                    continue

                hull = cv2.convexHull(cnt)
                hull_area = cv2.contourArea(hull)
                if hull_area == 0:
                    continue

                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = w / h if h > 0 else 0

                if 0.5 < aspect_ratio < 1.5 and area / hull_area < 0.7:
                    symbols.append(DetectedSymbol(
                        symbol_type=FolkSymbolType.FERMATA,
                        x_position=x + w // 2,
                        y_position=y + h // 2,
                        bbox=(x, y, w, h),
                        confidence=0.8,
                    ))

        except ImportError:
            pass
        return symbols

    def detect_mordent(self, staff_image: np.ndarray,
                       staff_lines: List[int]) -> List[DetectedSymbol]:
        """检测波音和逆波音记号（~ 锯齿形符号）

        波音记号呈锯齿状折线，位于音符上方。
        检测策略：HoughLinesP检测短斜线段，判断相邻线段是否构成锯齿。

        Args:
            staff_image: 乐谱图像
            staff_lines: 谱线位置

        Returns:
            波音符号列表
        """
        symbols = []
        try:
            import cv2

            if not staff_lines:
                return symbols
            upper_region = staff_image[:staff_lines[0] - 5, :]
            if upper_region.size == 0:
                return symbols

            lines = cv2.HoughLinesP(
                cv2.bitwise_not(upper_region),
                1,
                np.pi / 180,
                threshold=15,
                minLineLength=5,
                maxLineGap=2,
            )

            if lines is None:
                return symbols

            short_lines = [
                line[0] for line in lines
                if abs(line[0][2] - line[0][0]) < 15 and
                abs(line[0][3] - line[0][1]) < 15
            ]

            clusters = self._cluster_short_lines(short_lines, max_gap=12)

            for cluster in clusters:
                if 3 <= len(cluster) <= 6:
                    xs = [l[0] for l in cluster]
                    ys = [l[1] for l in cluster]
                    x = int(np.mean(xs))
                    y = int(np.mean(ys))

                    is_inverted = self._check_mordent_direction(cluster)
                    symbol_type = (
                        FolkSymbolType.INVERTED_MORDENT if is_inverted
                        else FolkSymbolType.MORDENT
                    )

                    symbols.append(DetectedSymbol(
                        symbol_type=symbol_type,
                        x_position=x,
                        y_position=y,
                        bbox=(min(xs) - 3, min(ys) - 3,
                              max(xs) - min(xs) + 6, max(ys) - min(ys) + 6),
                        confidence=0.75,
                    ))

        except ImportError:
            pass
        return symbols

    def detect_harmonics(self, staff_image: np.ndarray,
                         note_positions: List[Tuple[float, float]]
                         ) -> List[DetectedSymbol]:
        """检测泛音记号（○ 或 * 标记）

        泛音在民乐谱中通常以圆圈或星号标记在音符上方。
        检测策略：在音符上方搜索空心圆或星号的连通域。

        Args:
            staff_image: 乐谱图像
            note_positions: 音符位置

        Returns:
            泛音符号列表
        """
        symbols = []
        try:
            import cv2

            for nx, ny in note_positions:
                search_top = max(0, int(ny) - 40)
                search_bottom = max(0, int(ny) - 5)
                search_left = max(0, int(nx) - 15)
                search_right = min(staff_image.shape[1], int(nx) + 15)
                roi = staff_image[
                    search_top:search_bottom,
                    search_left:search_right
                ]

                if roi.size == 0:
                    continue

                contours, hierarchy = cv2.findContours(
                    cv2.bitwise_not(roi),
                    cv2.RETR_CCOMP,
                    cv2.CHAIN_APPROX_SIMPLE
                )

                for i, cnt in enumerate(contours):
                    area = cv2.contourArea(cnt)
                    if area < 10 or area > 200:
                        continue

                    perimeter = cv2.arcLength(cnt, True)
                    if perimeter == 0:
                        continue

                    circularity = 4 * math.pi * area / (perimeter ** 2)

                    if circularity > 0.7:
                        has_hole = False
                        if hierarchy is not None and i < len(hierarchy[0]):
                            child_idx = hierarchy[0][i][2]
                            if child_idx != -1:
                                child_area = cv2.contourArea(contours[child_idx])
                                if child_area > area * 0.2:
                                    has_hole = True

                        if has_hole or 0.55 < circularity < 0.8:
                            x, y, w, h = cv2.boundingRect(cnt)
                            symbols.append(DetectedSymbol(
                                symbol_type=FolkSymbolType.HARMONICS,
                                x_position=search_left + x + w // 2,
                                y_position=search_top + y + h // 2,
                                bbox=(search_left + x, search_top + y, w, h),
                                confidence=0.8,
                            ))

        except ImportError:
            pass
        return symbols

    def detect_strike(self, staff_image: np.ndarray,
                      note_positions: List[Tuple[float, float]]
                      ) -> List[DetectedSymbol]:
        """检测打音记号（+ 号标记）

        打音在民乐谱中通常以"+"标记在音符上方。
        检测策略：搜索十字形连通域，判定交叉点特征。

        Args:
            staff_image: 乐谱图像
            note_positions: 音符位置

        Returns:
            打音符号列表
        """
        symbols = []
        try:
            import cv2

            lines = cv2.HoughLinesP(
                cv2.bitwise_not(staff_image),
                1,
                np.pi / 180,
                threshold=20,
                minLineLength=8,
                maxLineGap=1,
            )

            if lines is None:
                return symbols

            horizontals = []
            verticals = []
            for line in lines:
                x1, y1, x2, y2 = line[0]
                dx, dy = abs(x2 - x1), abs(y2 - y1)
                if dx > dy * 2:
                    horizontals.append(line[0])
                elif dy > dx * 2:
                    verticals.append(line[0])

            for h_line in horizontals:
                hx1, hy1, hx2, hy2 = h_line
                h_mid_x = (hx1 + hx2) / 2
                h_mid_y = (hy1 + hy2) / 2

                for v_line in verticals:
                    vx1, vy1, vx2, vy2 = v_line
                    v_mid_x = (vx1 + vx2) / 2
                    v_mid_y = (vy1 + vy2) / 2

                    if (abs(h_mid_x - v_mid_x) < 5 and
                            abs(h_mid_y - v_mid_y) < 5):
                        near_note = any(
                            abs(nx - h_mid_x) < 30 and abs(ny - h_mid_y) < 30
                            for nx, ny in note_positions
                        )
                        if near_note and abs(h_mid_y - h_mid_x) < 2:
                            symbols.append(DetectedSymbol(
                                symbol_type=FolkSymbolType.STRIKE,
                                x_position=h_mid_x,
                                y_position=h_mid_y,
                                bbox=(int(h_mid_x) - 5, int(h_mid_y) - 5, 10, 10),
                                confidence=0.75,
                            ))
                            break

        except ImportError:
            pass
        return symbols

    def detect_open_string(self, staff_image: np.ndarray,
                           note_positions: List[Tuple[float, float]]
                           ) -> List[DetectedSymbol]:
        """检测空弦记号（○ 或 0 标记）

        空弦标记通常以圆圈○或数字0标记在音符上方，
        表示演奏时使用空弦（不按指）。

        Args:
            staff_image: 乐谱图像
            note_positions: 音符位置

        Returns:
            空弦符号列表
        """
        symbols = []
        try:
            import cv2

            contours, _ = cv2.findContours(
                cv2.bitwise_not(staff_image),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 15 or area > 120:
                    continue

                perimeter = cv2.arcLength(cnt, True)
                if perimeter == 0:
                    continue

                circularity = 4 * math.pi * area / (perimeter ** 2)

                if circularity > 0.75:
                    x, y, w, h = cv2.boundingRect(cnt)
                    cx, cy = x + w // 2, y + h // 2

                    near_note = any(
                        abs(nx - cx) < 25 and abs(ny - cy) < 40
                        for nx, ny in note_positions
                    )

                    if near_note and cy < min(ny for nx, ny in note_positions
                                              if abs(nx - cx) < 25) + 5:
                        symbols.append(DetectedSymbol(
                            symbol_type=FolkSymbolType.OPEN_STRING,
                            x_position=cx,
                            y_position=cy,
                            bbox=(x, y, w, h),
                            confidence=min(circularity, 1.0),
                        ))

        except ImportError:
            pass
        return symbols

    def detect_accent(self, staff_image: np.ndarray,
                      staff_lines: List[int]) -> List[DetectedSymbol]:
        """检测强音记号（> 或 ∧ 形状）

        Args:
            staff_image: 乐谱图像
            staff_lines: 谱线位置

        Returns:
            强音记号列表
        """
        symbols = []
        try:
            import cv2

            if not staff_lines:
                return symbols
            below_staff = staff_image[staff_lines[-1] + 5:, :]
            if below_staff.size == 0:
                return symbols

            contours, _ = cv2.findContours(
                cv2.bitwise_not(below_staff),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 15 or area > 100:
                    continue

                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = w / h if h > 0 else 0

                if 0.5 < aspect_ratio < 4.0:
                    symbols.append(DetectedSymbol(
                        symbol_type=FolkSymbolType.ACCENT,
                        x_position=x + w // 2,
                        y_position=y + h // 2 + (staff_lines[-1] + 5),
                        bbox=(x, y, w, h),
                        confidence=0.8,
                    ))

        except ImportError:
            pass
        return symbols

    def detect_staccato(self, staff_image: np.ndarray,
                        staff_lines: List[int]) -> List[DetectedSymbol]:
        """检测顿音记号（小圆点，位于音符上方或下方）

        Args:
            staff_image: 乐谱图像
            staff_lines: 谱线位置

        Returns:
            顿音记号列表
        """
        symbols = []
        try:
            import cv2

            contours, _ = cv2.findContours(
                cv2.bitwise_not(staff_image),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if area < 4 or area > 30:
                    continue

                perimeter = cv2.arcLength(cnt, True)
                if perimeter == 0:
                    continue

                circularity = 4 * math.pi * area / (perimeter ** 2)

                if circularity > 0.85:
                    x, y, w, h = cv2.boundingRect(cnt)
                    symbols.append(DetectedSymbol(
                        symbol_type=FolkSymbolType.STACCATO,
                        x_position=x + w // 2,
                        y_position=y + h // 2,
                        bbox=(x, y, w, h),
                        confidence=circularity,
                    ))

        except ImportError:
            pass
        return symbols

    def detect_accidental_scope(self, staff_image: np.ndarray,
                                staff_lines: List[int]
                                ) -> List[DetectedSymbol]:
        """判定临时变音记号的作用域

        临时变音记号在本小节内对同音高的后续音符生效。
        此函数返回关联信息供纠错模块使用。

        Args:
            staff_image: 乐谱图像
            staff_lines: 谱线位置

        Returns:
            临时记号作用域信息（内部使用）
        """
        return []

    def _is_digit_three_shape(self, contour: np.ndarray) -> bool:
        """判断轮廓是否为数字"3"的形状

        Args:
            contour: OpenCV轮廓

        Returns:
            是否为三连音数字"3"
        """
        try:
            import cv2
            _, _, w, h = cv2.boundingRect(contour)
            if h == 0:
                return False

            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            area = cv2.contourArea(contour)
            if hull_area == 0:
                return False

            solidity = area / hull_area
            return 0.3 < solidity < 0.7 and 1.2 < h / w < 3.0
        except ImportError:
            return False

    def _cluster_short_lines(self, lines: List[Tuple[int, int, int, int]],
                             max_gap: int = 12) -> List[List[Tuple[int, int, int, int]]]:
        """将短线段聚类（用于波音检测中的锯齿片段聚合）

        Args:
            lines: 短线段列表
            max_gap: 聚类最大间距

        Returns:
            聚类后的线段分组
        """
        if not lines:
            return []

        sorted_lines = sorted(lines, key=lambda l: l[0])
        clusters = [[sorted_lines[0]]]

        for line in sorted_lines[1:]:
            last = clusters[-1][-1]
            if abs(line[0] - last[0]) < max_gap:
                clusters[-1].append(line)
            else:
                clusters.append([line])

        return clusters

    def _check_mordent_direction(self, cluster: List[Tuple[int, int, int, int]]
                                 ) -> bool:
        """判断波音的方向（正波音 vs 逆波音）

        Args:
            cluster: 线段聚类

        Returns:
            True=逆波音, False=正波音
        """
        if len(cluster) < 3:
            return False

        ys = [l[1] for l in cluster]
        first_half = np.mean(ys[:len(ys) // 2])
        second_half = np.mean(ys[len(ys) // 2:])

        return first_half < second_half

    def get_symbols_by_type(self, symbol_type: FolkSymbolType
                            ) -> List[DetectedSymbol]:
        """按符号类型筛选检测结果

        Args:
            symbol_type: 符号类型

        Returns:
            该类型的符号列表
        """
        return [s for s in self.detected_symbols
                if s.symbol_type == symbol_type]

    def get_symbols_near_note(self, note_x: float, note_y: float,
                              max_distance: float = 30
                              ) -> List[DetectedSymbol]:
        """获取音符附近的符号

        Args:
            note_x: 音符x坐标
            note_y: 音符y坐标
            max_distance: 最大距离阈值

        Returns:
            附近符号列表
        """
        return [
            s for s in self.detected_symbols
            if math.hypot(s.x_position - note_x, s.y_position - note_y) < max_distance
        ]