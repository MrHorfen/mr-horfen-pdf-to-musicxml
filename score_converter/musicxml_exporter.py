"""
文件: musicxml_exporter.py
用途: MusicXML 4.0导出模块，将OMR识别结果编码为标准MusicXML格式文件
调用方: main.py
输入: OMR识别结果 + 纠错数据
输出: .musicxml 文件
依赖: lxml
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

from lxml import etree

from config import (
    get_config,
    setup_logging,
    INSTRUMENTS,
    InstrumentConfig,
    FOLK_SYMBOL_TEMPLATES,
)

logger = setup_logging(__name__)

MUSICXML_NAMESPACE = "http://www.musicxml.org/xmlns/musicxml/4.0.3"


@dataclass
class PartData:
    """声部导出数据"""
    part_id: str
    part_name: str
    instrument: InstrumentConfig
    measures: List[Dict] = field(default_factory=list)
    midi_program: int = 1


class MusicXMLExporter:
    """MusicXML 4.0格式导出器

    接收识别和纠错后的结构化数据，构建符合MusicXML 4.0规范的
    标准文件，妥善处理民乐符号到XML元素的映射。
    """

    def __init__(self):
        self.ns_map = {None: MUSICXML_NAMESPACE}
        self.ns_p = "{" + MUSICXML_NAMESPACE + "}"
        self.divisions = 4  # 四分音符=4 divisions
        self.title = "Untitled"
        self.composer = ""
        self.software = "PDF乐谱转MusicXML智能体系统 V1.0"

    def create_score(self, parts_data: List[PartData]) -> etree.Element:
        """创建完整的MusicXML乐谱文档

        Args:
            parts_data: 各声部的数据列表

        Returns:
            MusicXML根元素
        """
        root = etree.Element(
            "score-partwise",
            nsmap=self.ns_map,
            version="4.0",
        )

        work = etree.SubElement(root, self._tag("work"))
        etree.SubElement(work, self._tag("work-title")).text = self.title

        identification = etree.SubElement(root, self._tag("identification"))
        encoding = etree.SubElement(identification, self._tag("encoding"))
        etree.SubElement(encoding, self._tag("software")).text = self.software
        if self.composer:
            creator = etree.SubElement(
                identification, self._tag("creator")
            )
            creator.set("type", "composer")
            creator.text = self.composer

        part_list = etree.SubElement(root, self._tag("part-list"))
        for pd in parts_data:
            self._add_score_part(part_list, pd)

        for pd in parts_data:
            part_elem = self._build_part_xml(pd)
            root.append(part_elem)

        logger.info(
            f"MusicXML乐谱创建完成: {len(parts_data)} 声部, "
            f"\"{self.title}\""
        )
        return root

    def export_to_file(self, parts_data: List[PartData],
                       output_path: str) -> str:
        """导出MusicXML文件到磁盘

        Args:
            parts_data: 声部数据列表
            output_path: 输出文件路径

        Returns:
            输出文件绝对路径
        """
        root = self.create_score(parts_data)

        doctype = ('<!DOCTYPE score-partwise PUBLIC '
                   '"-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
                   '"http://www.musicxml.org/dtds/partwise.dtd">')

        xml_bytes = etree.tostring(
            root,
            pretty_print=True,
            xml_declaration=True,
            encoding="UTF-8",
            doctype=doctype,
        )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(xml_bytes)

        file_size = os.path.getsize(output_path)
        logger.info(f"MusicXML文件已导出: {output_path} ({file_size:,} bytes)")
        return os.path.abspath(output_path)

    def export_simple(self, notes_data: Dict[int, List[Dict]],
                      instrument_key: str = "erhu",
                      output_path: str = "output.musicxml") -> str:
        """简化的快速导出接口

        Args:
            notes_data: {声部索引: [音符字典列表]} 格式的数据
            instrument_key: 乐器标识
            output_path: 输出路径

        Returns:
            输出文件路径
        """
        inst_config = INSTRUMENTS.get(instrument_key, INSTRUMENTS["erhu"])

        parts = []
        for staff_idx, notes in notes_data.items():
            measures = self._notes_to_measures(notes)
            pd = PartData(
                part_id=inst_config.part_id,
                part_name=inst_config.name,
                instrument=inst_config,
                measures=measures,
            )
            parts.append(pd)

        return self.export_to_file(parts, output_path)

    def _add_score_part(self, part_list: etree.Element, pd: PartData) -> None:
        """向part-list添加一个score-part元素

        Args:
            part_list: part-list元素
            pd: 声部数据
        """
        sp = etree.SubElement(part_list, self._tag("score-part"))
        sp.set("id", pd.part_id)

        etree.SubElement(sp, self._tag("part-name")).text = pd.part_name

        if pd.instrument.is_transposing:
            transpose = etree.SubElement(sp, self._tag("transpose"))
            trans_data = pd.instrument.transposition or {}
            if "diatonic" in trans_data:
                etree.SubElement(transpose, self._tag("diatonic")).text = str(
                    trans_data["diatonic"]
                )
            if "chromatic" in trans_data:
                etree.SubElement(transpose, self._tag("chromatic")).text = str(
                    trans_data["chromatic"]
                )

        score_instrument = etree.SubElement(
            sp, self._tag("score-instrument")
        )
        score_instrument.set("id", f"{pd.part_id}-I1")
        etree.SubElement(
            score_instrument, self._tag("instrument-name")
        ).text = pd.part_name

        midi_instrument = etree.SubElement(
            sp, self._tag("midi-instrument")
        )
        midi_instrument.set("id", f"{pd.part_id}-I1")
        etree.SubElement(
            midi_instrument, self._tag("midi-channel")
        ).text = "1"
        etree.SubElement(
            midi_instrument, self._tag("midi-program")
        ).text = str(pd.midi_program)

    def _build_part_xml(self, pd: PartData) -> etree.Element:
        """构建单个声部的完整XML

        Args:
            pd: 声部数据

        Returns:
            part元素
        """
        part_elem = etree.Element(self._tag("part"))
        part_elem.set("id", pd.part_id)

        measure_number = 1
        for m_data in pd.measures:
            measure = self._build_measure_xml(
                pd, m_data, measure_number
            )
            part_elem.append(measure)
            measure_number += 1

        return part_elem

    def _build_measure_xml(self, pd: PartData, m_data: Dict,
                           measure_number: int) -> etree.Element:
        """构建单个小节的XML

        Args:
            pd: 声部数据
            m_data: 小节数据字典
            measure_number: 小节序号

        Returns:
            measure元素
        """
        measure = etree.Element(self._tag("measure"))
        measure.set("number", str(measure_number))

        if measure_number == 1:
            attrs = etree.SubElement(measure, self._tag("attributes"))
            etree.SubElement(attrs, self._tag("divisions")).text = str(self.divisions)

            time_sig = m_data.get("time_signature", {"beats": 4, "beat_type": 4})
            time_elem = etree.SubElement(attrs, self._tag("time"))
            if isinstance(time_sig, dict):
                etree.SubElement(time_elem, self._tag("beats")).text = str(
                    time_sig.get("beats", 4)
                )
                etree.SubElement(time_elem, self._tag("beat-type")).text = str(
                    time_sig.get("beat_type", 4)
                )

            clef_elem = etree.SubElement(attrs, self._tag("clef"))
            etree.SubElement(clef_elem, self._tag("sign")).text = str(
                pd.instrument.clef.sign
            )
            etree.SubElement(clef_elem, self._tag("line")).text = str(
                pd.instrument.clef.line
            )

            key_sig = m_data.get("key_signature", {"fifths": 0})
            key_elem = etree.SubElement(attrs, self._tag("key"))
            if isinstance(key_sig, dict):
                etree.SubElement(key_elem, self._tag("fifths")).text = str(
                    key_sig.get("fifths", 0)
                )

        notes = m_data.get("notes", [])
        for note_data in notes:
            note_elem = self._build_note_xml(note_data)
            measure.append(note_elem)

        if not notes:
            rest_note = etree.SubElement(measure, self._tag("note"))
            etree.SubElement(rest_note, self._tag("rest"))
            etree.SubElement(rest_note, self._tag("duration")).text = str(
                self.divisions * 4
            )
            etree.SubElement(rest_note, self._tag("type")).text = "whole"

        barline = m_data.get("barline")
        if barline:
            bar_elem = etree.SubElement(measure, self._tag("barline"))
            etree.SubElement(bar_elem, self._tag("bar-style")).text = str(barline)

        return measure

    def _build_note_xml(self, note_data: Dict) -> etree.Element:
        """构建单个音符的XML，包含民乐符号映射

        Args:
            note_data: 音符数据字典，可能包含:
                - step, octave: 音名和八度
                - duration: 时值
                - type: 音符类型
                - accidental: 变音记号
                - is_rest: 是否为休止符
                - dotted: 是否附点
                - folk_symbols: 民乐符号列表
                - beam: 符梁信息
                - stem: 符干方向
                - lyric: 歌词文本

        Returns:
            note元素
        """
        note_elem = etree.Element(self._tag("note"))

        is_rest = note_data.get("is_rest", False)
        is_grace = note_data.get("is_grace", False)

        if is_grace:
            etree.SubElement(note_elem, self._tag("grace"))

        if is_rest:
            etree.SubElement(note_elem, self._tag("rest"))
        else:
            step = note_data.get("step", "C")
            octave = note_data.get("octave", 4)
            pitch = etree.SubElement(note_elem, self._tag("pitch"))
            etree.SubElement(pitch, self._tag("step")).text = str(step)
            etree.SubElement(pitch, self._tag("octave")).text = str(octave)

            alter = note_data.get("alter", 0)
            if alter != 0:
                etree.SubElement(pitch, self._tag("alter")).text = str(alter)

            accidental = note_data.get("accidental")
            if accidental:
                etree.SubElement(
                    note_elem, self._tag("accidental")
                ).text = str(accidental)

        duration = note_data.get("duration", self.divisions)
        etree.SubElement(note_elem, self._tag("duration")).text = str(duration)

        note_type = note_data.get("type", "quarter")
        if not is_grace:
            etree.SubElement(note_elem, self._tag("type")).text = str(note_type)

        dotted = note_data.get("dotted", False)
        if dotted:
            etree.SubElement(note_elem, self._tag("dot"))

        voice = note_data.get("voice")
        if voice:
            etree.SubElement(note_elem, self._tag("voice")).text = str(voice)

        stem = note_data.get("stem")
        if stem:
            stem_elem = etree.SubElement(note_elem, self._tag("stem"))
            stem_elem.text = str(stem)

        beam = note_data.get("beam")
        if beam:
            for b in beam if isinstance(beam, list) else [beam]:
                beam_elem = etree.SubElement(note_elem, self._tag("beam"))
                beam_elem.set("number", str(b.get("number", 1)))
                beam_elem.text = str(b.get("value", "begin"))

        time_mod = note_data.get("time_modification")
        if time_mod:
            tm_elem = etree.SubElement(
                note_elem, self._tag("time-modification")
            )
            etree.SubElement(
                tm_elem, self._tag("actual-notes")
            ).text = str(time_mod.get("actual_notes", 3))
            etree.SubElement(
                tm_elem, self._tag("normal-notes")
            ).text = str(time_mod.get("normal_notes", 2))

        folk_symbols = note_data.get("folk_symbols", [])
        notations_elem = None

        for symbol in folk_symbols if isinstance(folk_symbols, list) else []:
            symbol_type = symbol.get("type", "")

            if notations_elem is None:
                notations_elem = etree.SubElement(
                    note_elem, self._tag("notations")
                )

            if symbol_type == "fermata":
                etree.SubElement(notations_elem, self._tag("fermata"))

            elif symbol_type in ("mordent", "inverted_mordent"):
                ornaments = etree.SubElement(
                    notations_elem, self._tag("ornaments")
                )
                etree.SubElement(ornaments, self._tag(symbol_type))

            elif symbol_type == "harmonics":
                etree.SubElement(
                    note_elem, self._tag("notehead")
                ).text = "diamond"

            elif symbol_type == "accent":
                articulations = etree.SubElement(
                    notations_elem, self._tag("articulations")
                )
                etree.SubElement(articulations, self._tag("accent"))

            elif symbol_type == "staccato":
                articulations = etree.SubElement(
                    notations_elem, self._tag("articulations")
                )
                etree.SubElement(articulations, self._tag("staccato"))

            elif symbol_type == "open_string":
                technical = etree.SubElement(
                    notations_elem, self._tag("technical")
                )
                etree.SubElement(technical, self._tag("string")).text = "0"

            elif symbol_type == "strike":
                technical = etree.SubElement(
                    notations_elem, self._tag("technical")
                )
                etree.SubElement(technical, self._tag("up-bow"))

            elif symbol_type == "breath_mark":
                articulations = etree.SubElement(
                    notations_elem, self._tag("articulations")
                )
                etree.SubElement(articulations, self._tag("breath-mark"))

        tie = note_data.get("tie")
        if tie:
            if notations_elem is None:
                notations_elem = etree.SubElement(
                    note_elem, self._tag("notations")
                )
            tie_elem = etree.SubElement(notations_elem, self._tag("tied"))
            tie_elem.set("type", str(tie))

        slur = note_data.get("slur")
        if slur:
            if notations_elem is None:
                notations_elem = etree.SubElement(
                    note_elem, self._tag("notations")
                )
            slur_elem = etree.SubElement(notations_elem, self._tag("slur"))
            slur_elem.set("type", str(slur.get("type", "start")))
            slur_elem.set("number", str(slur.get("number", 1)))

        lyric = note_data.get("lyric")
        if lyric:
            lyric_elem = etree.SubElement(note_elem, self._tag("lyric"))
            etree.SubElement(lyric_elem, self._tag("text")).text = str(lyric)

        return note_elem

    def _notes_to_measures(self, notes: List[Dict]) -> List[Dict]:
        """将音符列表按小节划分

        Args:
            notes: 音符列表

        Returns:
            小节数据列表
        """
        if not notes:
            return [{"notes": [], "measure_number": 1}]

        barlines = [
            i for i, n in enumerate(notes)
            if n.get("barline")
        ]

        if not barlines:
            return [{"notes": notes, "measure_number": 1}]

        measures = []
        prev_idx = 0
        for measure_num, bar_idx in enumerate(barlines, 1):
            measures.append({
                "notes": notes[prev_idx:bar_idx + 1],
                "measure_number": measure_num,
            })
            prev_idx = bar_idx + 1

        if prev_idx < len(notes):
            measures.append({
                "notes": notes[prev_idx:],
                "measure_number": len(measures) + 1,
            })

        return measures

    def _tag(self, name: str) -> str:
        """生成带命名空间的XML标签名"""
        return self.ns_p + name

    def generate_note_dict(self, step: str, octave: int,
                           duration: int, note_type: str,
                           **kwargs) -> Dict:
        """便捷方法：生成标准化的音符数据字典

        Args:
            step: 音名 (C, D, E, F, G, A, B)
            octave: 八度 (1-8)
            duration: 时值 (divisions单位)
            note_type: 音符类型 (whole/half/quarter/eighth/16th)
            **kwargs: 额外属性 (accidental, dotted, stem, beam等)

        Returns:
            音符数据字典
        """
        note = {
            "step": step,
            "octave": octave,
            "duration": duration,
            "type": note_type,
        }
        note.update(kwargs)
        return note

    def generate_rest_dict(self, duration: int) -> Dict:
        """便捷方法：生成休止符数据字典

        Args:
            duration: 时值

        Returns:
            休止符数据字典
        """
        return {"is_rest": True, "duration": duration, "type": "rest"}

    def validate_output(self, xml_path: str) -> Tuple[bool, str]:
        """验证导出的MusicXML文件有效性

        Args:
            xml_path: XML文件路径

        Returns:
            (是否有效, 信息)
        """
        try:
            from music21 import converter
            score = converter.parse(xml_path)
            parts = len(score.parts)
            measures = max(len(p.getElementsByClass("Measure"))
                           for p in score.parts)
            return True, f"验证通过: {parts}声部, 最多{measures}小节"
        except ImportError:
            try:
                tree = etree.parse(xml_path)
                root = tree.getroot()
                parts = len(root.findall(f".//{self.ns_p}part"))
                return True, f"XML解析通过: {parts}声部"
            except Exception as e:
                return False, f"验证失败: {e}"
        except Exception as e:
            return False, f"验证失败: {e}"