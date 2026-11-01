"""
文件: musicxml_postprocessor.py
用途: 修正OMR输出MusicXML中的格式错误和谱面结构问题，保守修改不破坏真实识别音符
调用方: postprocess_pipeline 或 OMR识别后处理流程
输入: OMR识别输出的MusicXML文件
输出: 修正后的MusicXML文件
依赖: lxml, xml.etree.ElementTree
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from copy import deepcopy
import xml.etree.ElementTree as ET
from lxml import etree

logger = logging.getLogger(__name__)


@dataclass
class MusicXMLError:
    """MusicXML错误记录"""
    error_type: str
    location: str
    description: str
    severity: str = "warning"  # error|warning|info


class MusicXMLPostProcessor:
    """
    MusicXML后处理器，保守修正OMR输出的格式问题
    核心原则：
    1. 保留所有Audiveris识别的音符
    2. 只修复格式错误（如重复谱号、命名空间问题）
    3. 不改变音符的音高、时值、位置
    4. 不添加任何新的音符或休止符
    """

    def __init__(
        self,
        merge_multipage: bool = True,
        enforce_time_signature: bool = False,  # 关闭自动填充
        fix_clef_duplicates: bool = True,
        fix_barline_misplacement: bool = False,  # 关闭激进合并
        fix_duration_errors: bool = False,  # 关闭时值修改
        fix_voice_confusion: bool = True,
        optimize_for_musescore: bool = True,
    ):
        """
        初始化后处理器
        
        注意：所有可能修改音乐内容的选项默认关闭
        """
        self.merge_multipage = merge_multipage
        self.enforce_time_signature = enforce_time_signature
        self.fix_clef_duplicates = fix_clef_duplicates
        self.fix_barline_misplacement = fix_barline_misplacement
        self.fix_duration_errors = fix_duration_errors
        self.fix_voice_confusion = fix_voice_confusion
        self.optimize_for_musescore = optimize_for_musescore
        self.errors: List[MusicXMLError] = []

    def process(
        self,
        musicxml_path: str,
        output_path: str,
        confidence_data: Optional[List] = None,
    ) -> str:
        """
        处理单个MusicXML文件
        """
        self.errors = []
        logger.info(f"开始后处理: {Path(musicxml_path).name}")

        try:
            tree = etree.parse(musicxml_path)
            root = tree.getroot()
        except Exception as e:
            raise ValueError(f"无法解析MusicXML: {e}")

        nsmap = self._extract_namespace(root)

        # 仅执行安全的格式修复
        if self.fix_clef_duplicates:
            root = self._fix_duplicate_clefs(root, nsmap)

        if self.fix_barline_misplacement:
            root = self._fix_barline_placement(root, nsmap)

        if self.fix_duration_errors:
            root = self._fix_duration_errors(root, nsmap)

        if self.fix_voice_confusion:
            root = self._fix_voice_confusion(root, nsmap)

        if self.enforce_time_signature:
            root = self._enforce_time_signature(root, nsmap)

        # MuseScore优化
        if self.optimize_for_musescore:
            root = self._optimize_for_musescore(root, nsmap)

        if confidence_data:
            root = self._inject_confidence_metadata(root, confidence_data, nsmap)

        root = self._ensure_musicxml_40_compliance(root, nsmap)

        tree = etree.ElementTree(root)
        tree.write(
            output_path,
            pretty_print=True,
            xml_declaration=True,
            encoding="UTF-8",
        )

        logger.info(f"后处理完成: {output_path}, 发现{len(self.errors)}个问题")
        return output_path

    def process_batch(
        self,
        musicxml_paths: List[str],
        output_dir: str,
    ) -> Dict[str, str]:
        """批量处理MusicXML文件"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = {}
        for mx_path in musicxml_paths:
            try:
                output_name = f"corrected_{Path(mx_path).name}"
                output_path = str(output_dir / output_name)
                self.process(mx_path, output_path)
                results[mx_path] = output_path
            except Exception as e:
                logger.error(f"后处理失败 [{mx_path}]: {e}")
                raise

        return results

    def _extract_namespace(self, root: etree.Element) -> str:
        """提取MusicXML命名空间"""
        ns = root.nsmap.get(None, "")
        return "{" + ns + "}" if ns else ""

    def _fix_duplicate_clefs(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """
        修复重复谱号问题
        检测同一小节内完全相同的谱号并移除重复项
        """
        for measure in root.iter(f"{ns}measure"):
            clefs = measure.findall(f".//{ns}clef")
            if len(clefs) > 1:
                seen = set()
                duplicates = []
                for clef in clefs:
                    # 使用规范化字符串（去除首尾空白）避免格式化差异导致匹配失败
                    clef_str = etree.tostring(clef, encoding="unicode").strip()
                    if clef_str in seen:
                        duplicates.append(clef)
                    else:
                        seen.add(clef_str)
                for dup in duplicates:
                    parent = dup.getparent()
                    if parent is not None:
                        parent.remove(dup)
                        self.errors.append(MusicXMLError(
                            error_type="duplicate_clef",
                            location=f"measure {measure.get('number', '?')}",
                            description="移除重复谱号",
                        ))
        return root

    def _fix_barline_placement(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """
        修复小节线错位问题（保守版本）
        只合并完全为空的小节，不合并包含任何音符的小节
        """
        for part in root.iter(f"{ns}part"):
            measures = list(part.iter(f"{ns}measure"))
            if not measures:
                continue

            # 只移除完全没有内容的小节
            measures_to_remove = []
            for i, measure in enumerate(measures):
                notes = list(measure.iter(f"{ns}note"))
                directions = list(measure.iter(f"{ns}direction"))
                barlines = list(measure.iter(f"{ns}barline"))
                attributes = list(measure.iter(f"{ns}attributes"))
                
                # 如果小节只有barline且没有notes、directions、attributes
                # 且前后小节有时值，可以安全移除
                if len(notes) == 0 and len(directions) == 0 and len(attributes) == 0:
                    if i > 0 and i < len(measures) - 1:
                        # 合并到前一个小节
                        prev_measure = measures[i - 1]
                        for elem in list(measure):
                            if elem.tag not in [f"{ns}print"]:
                                prev_measure.append(elem)
                        measures_to_remove.append(measure)
                        self.errors.append(MusicXMLError(
                            error_type="empty_measure_merged",
                            location=f"measure {measure.get('number', '?')}",
                            description="合并空小节",
                        ))

            for measure in measures_to_remove:
                parent = measure.getparent()
                if parent is not None:
                    parent.remove(measure)

        return root

    def _fix_duration_errors(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """
        修复时值错误（保守版本）
        只修复明显的格式错误，不改变音符的相对时值
        """
        # 不修改任何duration值，只记录警告
        for measure in root.iter(f"{ns}measure"):
            divisions_elem = measure.find(f".//{ns}divisions")
            divisions = 4
            if divisions_elem is not None and divisions_elem.text:
                try:
                    divisions = int(divisions_elem.text)
                except:
                    pass

            for note in measure.iter(f"{ns}note"):
                duration = note.find(f"{ns}duration")
                type_elem = note.find(f"{ns}type")

                if duration is not None and type_elem is not None and duration.text:
                    dur_val = int(duration.text or 0)
                    type_val = type_elem.text
                    expected = self._expected_duration(type_val, divisions)
                    
                    if expected and dur_val != expected:
                        # 只记录警告，不修改
                        self.errors.append(MusicXMLError(
                            error_type="duration_mismatch_warning",
                            location=f"measure {measure.get('number', '?')}",
                            description=f"时值不匹配: {type_val} dur={dur_val}, expected={expected} (div={divisions})",
                            severity="info",
                        ))
        return root

    def _expected_duration(self, note_type: Optional[str], divisions: int = 4) -> Optional[int]:
        """根据音符类型返回标准时值"""
        if note_type is None:
            return None
            
        duration_map = {
            "maxima": 32,
            "long": 16,
            "breve": 8,
            "whole": 4,
            "half": 2,
            "quarter": 1,
            "eighth": 0.5,
            "16th": 0.25,
            "32nd": 0.125,
            "64th": 0.0625,
        }
        
        base = duration_map.get(note_type)
        if base is None:
            return None
            
        return int(base * divisions)

    def _fix_voice_confusion(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """
        修复声部混淆问题（已禁用）
        Audiveris已经正确处理了和弦标记，此函数会误判顺序音符为和弦
        """
        # 不执行任何修改，保留原始数据
        return root
    
    def _fix_grace_notes(self, measure: etree.Element, ns: str) -> None:
        """修复装饰音识别问题"""
        notes = list(measure.iter(f"{ns}note"))
        if len(notes) < 2:
            return
            
        divisions_elem = measure.find(f".//{ns}divisions")
        divisions = int(divisions_elem.text) if divisions_elem is not None else 4
        
        grace_threshold = divisions * 0.5
        
        for i, note in enumerate(notes):
            duration = note.find(f"{ns}duration")
            type_elem = note.find(f"{ns}type")
            
            if duration is not None and duration.text:
                dur_val = int(duration.text)
                
                is_rest = note.find(f"{ns}rest") is not None
                is_chord = note.find(f"{ns}chord") is not None
                
                if dur_val < grace_threshold and dur_val > 0 and not is_rest and not is_chord:
                    if i > 0:
                        prev_note = notes[i - 1]
                        prev_dur = prev_note.find(f"{ns}duration")
                        if prev_dur is not None and prev_dur.text:
                            prev_dur_val = int(prev_dur.text)
                            if prev_dur_val >= grace_threshold:
                                grace = etree.SubElement(note, f"{ns}grace")
                                grace.set("slash", "yes")
                                
                                if duration is not None:
                                    note.remove(duration)
                                
                                self.errors.append(MusicXMLError(
                                    error_type="grace_note",
                                    location=f"measure {measure.get('number', '?')}",
                                    description=f"标记装饰音 (duration={dur_val})",
                                ))
    
    def _fix_glissando(self, measure: etree.Element, ns: str) -> None:
        """修复滑音识别问题"""
        notes = list(measure.iter(f"{ns}note"))
        if len(notes) < 2:
            return
            
        for i in range(len(notes) - 1):
            current = notes[i]
            next_note = notes[i + 1]
            
            notations = current.find(f"{ns}notations")
            if notations is not None:
                slur = notations.find(f"{ns}slur")
                if slur is not None and slur.get("type") == "start":
                    current_pitch = current.find(f"{ns}pitch")
                    next_pitch = next_note.find(f"{ns}pitch")
                    
                    if current_pitch is not None and next_pitch is not None:
                        interval = self._calculate_interval(current_pitch, next_pitch, ns)
                        
                        if interval >= 4:
                            if notations is not None:
                                gliss = etree.SubElement(notations, f"{ns}glissando")
                                gliss.set("type", "start")
                                gliss.set("number", slur.get("number", "1"))
                                
                                next_notations = next_note.find(f"{ns}notations")
                                if next_notations is None:
                                    next_notations = etree.SubElement(next_note, f"{ns}notations")
                                next_gliss = etree.SubElement(next_notations, f"{ns}glissando")
                                next_gliss.set("type", "stop")
                                next_gliss.set("number", slur.get("number", "1"))
                                
                                self.errors.append(MusicXMLError(
                                    error_type="glissando",
                                    location=f"measure {measure.get('number', '?')}",
                                    description=f"标记滑音 (interval={interval}半音)",
                                ))
    
    def _calculate_interval(
        self, pitch1: etree.Element, pitch2: etree.Element, ns: str
    ) -> int:
        """计算两个音之间的半音数"""
        step_values = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
        
        step1 = pitch1.find(f"{ns}step")
        oct1 = pitch1.find(f"{ns}octave")
        alter1 = pitch1.find(f"{ns}alter")
        
        step2 = pitch2.find(f"{ns}step")
        oct2 = pitch2.find(f"{ns}octave")
        alter2 = pitch2.find(f"{ns}alter")
        
        if step1 is None or step2 is None or oct1 is None or oct2 is None:
            return 0
            
        s1 = step1.text or 'C'
        s2 = step2.text or 'C'
        o1 = int(oct1.text or 4)
        o2 = int(oct2.text or 4)
        a1 = int(alter1.text) if alter1 is not None and alter1.text else 0
        a2 = int(alter2.text) if alter2 is not None and alter2.text else 0
        
        midi1 = (o1 + 1) * 12 + step_values.get(s1, 0) + a1
        midi2 = (o2 + 1) * 12 + step_values.get(s2, 0) + a2
        
        return abs(midi2 - midi1)

    def _notes_overlap(
        self, n1: etree.Element, n2: etree.Element, ns: str
    ) -> bool:
        """判断两个音符是否在同一位置（应构成和弦）"""
        d1 = n1.find(f"{ns}duration")
        d2 = n2.find(f"{ns}duration")
        return d1 is not None and d2 is not None and d1.text == d2.text

    def _enforce_time_signature(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """
        检查拍号合规性（只记录警告，不修改）
        """
        time_elem = root.find(f".//{ns}time")
        if time_elem is None:
            return root

        beats = time_elem.find(f"{ns}beats")
        beat_type = time_elem.find(f"{ns}beat-type")
        if beats is None or beat_type is None:
            return root

        beats_per_measure = int(beats.text or 4)

        for measure in root.iter(f"{ns}measure"):
            total_duration = 0
            for note in measure.iter(f"{ns}note"):
                if note.find(f"{ns}chord") is not None:
                    continue
                
                duration = note.find(f"{ns}duration")
                if duration is not None and duration.text:
                    try:
                        dur = int(duration.text)
                        rest = note.find(f"{ns}rest")
                        if rest is not None and rest.get('measure') == 'yes':
                            continue
                        total_duration += dur
                    except:
                        pass

            if total_duration > 0:
                self.errors.append(MusicXMLError(
                    error_type="time_signature_check",
                    location=f"measure {measure.get('number', '?')}",
                    description=f"小节时值: {total_duration} (期望 {beats_per_measure * 4})",
                    severity="info",
                ))

        return root

    def _optimize_for_musescore(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """优化MuseScore兼容性"""
        # 1. 确保所有part都有id
        for i, part in enumerate(root.iter(f"{ns}part")):
            if not part.get("id"):
                part.set("id", f"P{i+1}")
        
        # 2. 移除可能导致MuseScore解析错误的空属性
        for elem in root.iter():
            if elem.get("print-object") == "":
                del elem.attrib["print-object"]
            
            if elem.tag == f"{ns}duration" and (not elem.text or not elem.text.strip()):
                elem.text = "4"
        
        # 3. 规范化measure number（确保是连续整数）
        for part in root.iter(f"{ns}part"):
            measures = list(part.iter(f"{ns}measure"))
            for i, measure in enumerate(measures, 1):
                measure.set("number", str(i))
        
        return root

    def _inject_confidence_metadata(
        self,
        root: etree.Element,
        confidence_data: List,
        ns: str,
    ) -> etree.Element:
        """将置信度数据注入MusicXML元数据"""
        identification = root.find(f"{ns}identification")
        if identification is None:
            identification = etree.SubElement(root, f"{ns}identification")

        misc = identification.find(f"{ns}miscellaneous")
        if misc is None:
            misc = etree.SubElement(identification, f"{ns}miscellaneous")

        for conf in confidence_data:
            misc_field = etree.SubElement(misc, f"{ns}miscellaneous-field")
            misc_field.set("name", f"omr-confidence-{conf.element_type}")
            misc_field.text = str(conf.confidence)

        return root

    def _ensure_musicxml_40_compliance(
        self, root: etree.Element, ns: str
    ) -> etree.Element:
        """确保符合MusicXML 4.0规范"""
        root.set("version", "4.0")

        score_partwise = root.tag.endswith("score-partwise")
        if not score_partwise:
            logger.warning("根元素不是score-partwise，尝试包装")

        for part in root.iter(f"{ns}part"):
            if not part.get("id"):
                part.set("id", "P1")

        return root

    def get_errors(self) -> List[MusicXMLError]:
        """获取处理过程中发现的错误列表"""
        return self.errors


def postprocess_musicxml(
    input_path: str,
    output_path: str,
    merge_multipage: bool = True,
    enforce_time_signature: bool = True,
) -> str:
    """
    便捷的MusicXML后处理函数
    """
    processor = MusicXMLPostProcessor(
        merge_multipage=merge_multipage,
        enforce_time_signature=enforce_time_signature,
    )
    return processor.process(input_path, output_path)
