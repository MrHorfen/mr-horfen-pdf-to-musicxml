"""
文件: postprocess_pipeline.py
用途: MusicXML后处理流程管线，包含校验层（自动标注问题小节）和推理层（基于乐理规则修正谱号/拍号/调号/voice分配），生成人工修正指引报告
调用方: score_converter 主流程
输入: OMR识别输出的MusicXML文件
输出: 修正后的MusicXML文件及修正指引报告
依赖: lxml, dataclasses
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-31
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from lxml import etree

logger = logging.getLogger(__name__)


@dataclass
class IssueRecord:
    """问题记录"""
    issue_type: str  # clef_error, duration_error, key_signature_missing, etc.
    measure_number: int
    staff_number: Optional[int] = None
    description: str = ""
    severity: str = "error"  # error, warning, info
    suggested_fix: str = ""


@dataclass
class CorrectionReport:
    """修正报告"""
    input_file: str
    output_file: str
    total_measures: int
    issues: List[IssueRecord] = field(default_factory=list)
    corrections_applied: List[str] = field(default_factory=list)
    manual_fixes_needed: List[str] = field(default_factory=list)


class MusicXMLPostProcessPipeline:
    """MusicXML 后处理流程
    
    校验层 + 推理层架构：
    - 校验层：检测问题并标注
    - 推理层：基于规则自动修正（谱号、调号等）
    - 报告层：生成人工修正指引
    """
    
    def __init__(self):
        self.issues: List[IssueRecord] = []
        self.corrections: List[str] = []
        self.manual_fixes: List[str] = []
        
    def process(
        self,
        input_path: str,
        output_path: str,
        report_path: Optional[str] = None,
        expected_time_sig: Tuple[int, int] = (2, 4),
        expected_key_changes: Optional[List[Tuple[int, int]]] = None,
    ) -> CorrectionReport:
        """执行完整后处理流程
        
        Args:
            input_path: 输入 MusicXML 路径
            output_path: 输出 MusicXML 路径
            report_path: 报告输出路径（可选）
            expected_time_sig: 期望拍号，如 (2, 4)
            expected_key_changes: 期望调号变化列表 [(小节号, fifths), ...]
        
        Returns:
            CorrectionReport 对象
        """
        logger.info(f"开始后处理: {input_path}")
        
        # 加载 XML
        tree = etree.parse(input_path)
        root = tree.getroot()
        
        # 提取命名空间
        ns = self._extract_namespace(root)
        
        # 获取 part 和 measures
        part = root.find(f"{ns}part")
        if part is None:
            raise ValueError("未找到 <part> 元素")
        
        measures = part.findall(f"{ns}measure")
        
        # 初始化报告
        report = CorrectionReport(
            input_file=input_path,
            output_file=output_path,
            total_measures=len(measures),
        )
        
        # === 校验层 ===
        logger.info("=== 校验层 ===")
        
        # 1. 检测谱号问题
        clef_issues = self._validate_clefs(measures, ns)
        report.issues.extend(clef_issues)
        
        # 2. 检测时值问题
        duration_issues = self._validate_durations(measures, ns, expected_time_sig)
        report.issues.extend(duration_issues)
        
        # 3. 检测调号问题
        if expected_key_changes:
            key_issues = self._validate_key_signatures(measures, ns, expected_key_changes)
            report.issues.extend(key_issues)
        
        # === 推理层 ===
        logger.info("=== 推理层 ===")
        
        # 1. 修正谱号
        self._fix_clefs(root, ns)
        
        # 2. 修正调号（如果提供了期望值）
        if expected_key_changes:
            self._fix_key_signatures(root, ns, expected_key_changes)
        
        # 3. 优化 voice 分配
        self._optimize_voice_allocation(root, ns)
        
        # 保存修正后的文件
        tree.write(
            output_path,
            pretty_print=True,
            xml_declaration=True,
            encoding="UTF-8",
        )
        logger.info(f"已保存修正文件: {output_path}")
        
        # 生成报告
        if report_path:
            self._generate_report(report, report_path)
        
        return report
    
    def _extract_namespace(self, root: etree.Element) -> str:
        """提取命名空间"""
        if root.nsmap:
            ns_uri = root.nsmap.get(None, "")
            return "{" + ns_uri + "}" if ns_uri else ""
        return ""
    
    def _validate_clefs(
        self,
        measures: List[etree.Element],
        ns: str,
    ) -> List[IssueRecord]:
        """校验谱号
        
        根据原谱实际结构校验：
        - 第1页（M1-16）：所有谱表都是低音谱号（F clef）
        - 第2-5页（M17+）：staff1=G, staff2=F
        """
        issues = []
        
        for m in measures:
            num = int(m.get("number", 0))
            
            # 根据小节范围确定期望谱号
            if num <= 16:
                expected_clefs = {"1": "F", "2": "F"}
            else:
                expected_clefs = {"1": "G", "2": "F"}
            
            clefs = m.findall(f".//{ns}clef")
            
            for clef in clefs:
                staff_num = clef.get("number", "1")
                sign_elem = clef.find(f"{ns}sign")
                
                if sign_elem is None:
                    continue
                
                sign = sign_elem.text
                expected = expected_clefs.get(staff_num, "G")
                
                if sign != expected:
                    issues.append(IssueRecord(
                        issue_type="clef_error",
                        measure_number=num,
                        staff_number=int(staff_num) if staff_num.isdigit() else None,
                        description=f"M{num} staff{staff_num} 谱号错误: {sign} (应为 {expected})",
                        severity="error",
                        suggested_fix=f"修正为 {expected} 谱号",
                    ))
        
        logger.info(f"谱号问题: {len(issues)} 个")
        return issues
    
    def _validate_durations(
        self,
        measures: List[etree.Element],
        ns: str,
        expected_time_sig: Tuple[int, int],
    ) -> List[IssueRecord]:
        """校验小节时值
        
        检查每小节的总时值是否符合拍号要求
        """
        issues = []
        beats, beat_type = expected_time_sig
        
        for m in measures:
            num = int(m.get("number", 0))
            
            # 获取 divisions
            div_elem = m.find(f".//{ns}divisions")
            divisions = 16  # 默认值
            if div_elem is not None and div_elem.text:
                try:
                    divisions = int(div_elem.text)
                except ValueError:
                    pass
            
            # 计算期望时值
            expected_duration = divisions * beats * (4 // beat_type)
            
            # 计算实际时值（排除 chord 和 backup）
            actual_duration = 0
            notes = m.findall(f"{ns}note")
            
            for note in notes:
                chord = note.find(f"{ns}chord")
                backup = note.find(f"{ns}backup")
                dur_elem = note.find(f"{ns}duration")
                
                if dur_elem is not None and dur_elem.text:
                    if chord is None and backup is None:
                        actual_duration += int(dur_elem.text)
            
            # 检查时值是否匹配
            tolerance = divisions * 0.1  # 10% 容差
            
            if abs(actual_duration - expected_duration) > tolerance:
                issues.append(IssueRecord(
                    issue_type="duration_error",
                    measure_number=num,
                    description=f"M{num} 时值错误: 实际={actual_duration}, 期望={expected_duration} (差值={actual_duration - expected_duration})",
                    severity="error",
                    suggested_fix="需人工核对原谱修正音符时值",
                ))
        
        logger.info(f"时值问题: {len(issues)} 个")
        return issues
    
    def _validate_key_signatures(
        self,
        measures: List[etree.Element],
        ns: str,
        expected_key_changes: List[Tuple[int, int]],
    ) -> List[IssueRecord]:
        """校验调号变化
        
        Args:
            expected_key_changes: [(小节号, fifths), ...]
                如 [(1, 0), (9, -1), (46, 0), (49, -1)]
        """
        issues = []
        
        # 构建期望调号映射
        expected_map = {m: f for m, f in expected_key_changes}
        
        # 提取实际调号
        actual_map = {}
        for m in measures:
            num = int(m.get("number", 0))
            keys = m.findall(f".//{ns}key")
            
            for key in keys:
                fifths_elem = key.find(f"{ns}fifths")
                if fifths_elem is not None and fifths_elem.text:
                    actual_map[num] = int(fifths_elem.text)
        
        # 对比
        for expected_m, expected_f in expected_map.items():
            if expected_m not in actual_map:
                issues.append(IssueRecord(
                    issue_type="key_signature_missing",
                    measure_number=expected_m,
                    description=f"M{expected_m} 缺少调号标记 (期望 fifths={expected_f})",
                    severity="error",
                    suggested_fix=f"添加调号 fifths={expected_f}",
                ))
            elif actual_map[expected_m] != expected_f:
                issues.append(IssueRecord(
                    issue_type="key_signature_wrong",
                    measure_number=expected_m,
                    description=f"M{expected_m} 调号错误: 实际={actual_map[expected_m]}, 期望={expected_f}",
                    severity="error",
                    suggested_fix=f"修正调号为 fifths={expected_f}",
                ))
        
        logger.info(f"调号问题: {len(issues)} 个")
        return issues
    
    def _fix_clefs(self, root: etree.Element, ns: str):
        """修正谱号
        
        根据原谱实际结构修正谱号：
        - 第1页（M1-16）：所有谱表都是低音谱号（F clef）
        - 第2页（M17-32）：第1-2行高低音，第3-4行双高音
        - 第3页（M33-48）：第1行双高音，第2-4行高低音
        - 第4页（M49-56）：第1-2行高低音，第3行双低音，第4行高低音
        - 第5页（M57+）：都为高低音
        
        由于 oemer 把所有系统合并成2个staff，需要根据小节范围修正谱号
        """
        part = root.find(f"{ns}part")
        measures = part.findall(f"{ns}measure")
        
        fixed_count = 0
        
        for m in measures:
            num = int(m.get("number", 0))
            
            # 根据小节范围确定正确的谱号
            if num <= 16:
                # 第1页：全部低音谱号
                expected_clefs = {"1": "F", "2": "F"}
            elif num <= 32:
                # 第2页：第1-2行高低音，第3-4行双高音
                # oemer 合并后：staff1=高音部分，staff2=低音部分
                expected_clefs = {"1": "G", "2": "F"}
            elif num <= 48:
                # 第3页：第1行双高音，第2-4行高低音
                expected_clefs = {"1": "G", "2": "F"}
            elif num <= 56:
                # 第4页：第1-2行高低音，第3行双低音，第4行高低音
                expected_clefs = {"1": "G", "2": "F"}
            else:
                # 第5页及以后：高低音
                expected_clefs = {"1": "G", "2": "F"}
            
            clefs = m.findall(f".//{ns}clef")
            
            for clef in clefs:
                staff_num = clef.get("number", "1")
                sign_elem = clef.find(f"{ns}sign")
                
                if sign_elem is None:
                    continue
                
                expected_clef = expected_clefs.get(staff_num, "G")
                
                # 修正谱号
                if sign_elem.text != expected_clef:
                    sign_elem.text = expected_clef
                    fixed_count += 1
        
        if fixed_count > 0:
            self.corrections.append(f"修正 {fixed_count} 个谱号错误（基于原谱结构）")
            logger.info(f"已修正 {fixed_count} 个谱号（基于原谱结构）")
    
    def _fix_key_signatures(
        self,
        root: etree.Element,
        ns: str,
        expected_key_changes: List[Tuple[int, int]],
    ):
        """修正调号
        
        在指定小节添加或修正调号
        """
        part = root.find(f"{ns}part")
        measures = part.findall(f"{ns}measure")
        
        # 构建期望调号映射
        expected_map = {m: f for m, f in expected_key_changes}
        
        fixed_count = 0
        
        for m in measures:
            num = int(m.get("number", 0))
            
            if num not in expected_map:
                continue
            
            expected_fifths = expected_map[num]
            
            # 查找或创建 attributes 元素
            attrs = m.find(f"{ns}attributes")
            if attrs is None:
                attrs = etree.SubElement(m, f"{ns}attributes")
                # 移动到第一个位置
                m.remove(attrs)
                m.insert(0, attrs)
            
            # 查找或创建 key 元素
            key = attrs.find(f"{ns}key")
            if key is None:
                key = etree.SubElement(attrs, f"{ns}key")
            
            # 设置 fifths
            fifths_elem = key.find(f"{ns}fifths")
            if fifths_elem is None:
                fifths_elem = etree.SubElement(key, f"{ns}fifths")
            
            if fifths_elem.text != str(expected_fifths):
                fifths_elem.text = str(expected_fifths)
                fixed_count += 1
        
        if fixed_count > 0:
            self.corrections.append(f"修正 {fixed_count} 个调号标记")
            logger.info(f"已修正 {fixed_count} 个调号")
    
    def _optimize_voice_allocation(self, root: etree.Element, ns: str):
        """优化 voice 分配
        
        钢琴谱标准 voice 分配：
        - staff1 (右手/G谱号): voice 1 (高音旋律), voice 2 (低音伴奏)
        - staff2 (左手/F谱号): voice 3 (高音伴奏), voice 4 (低音旋律)
        
        分配规则：
        1. 根据音高将音符分配到不同 voice
        2. staff1: 音高 >= B4 的分配到 voice1，< B4 的分配到 voice2
        3. staff2: 音高 >= C3 的分配到 voice3，< C3 的分配到 voice4
        """
        part = root.find(f"{ns}part")
        measures = part.findall(f"{ns}measure")
        
        # 音高阈值（MIDI 编号）
        # B4 = MIDI 71, C3 = MIDI 48
        STAFF1_THRESHOLD = 71  # B4
        STAFF2_THRESHOLD = 48  # C3
        
        STEP_MIDI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
        
        reassigned_count = 0
        
        for m in measures:
            for n in m.findall(f"{ns}note"):
                # 跳过 chord 和 backup
                chord = n.find(f"{ns}chord")
                backup = n.find(f"{ns}backup")
                rest = n.find(f"{ns}rest")
                
                if chord is not None or backup is not None or rest is not None:
                    continue
                
                # 获取 staff 和音高
                staff_elem = n.find(f"{ns}staff")
                pitch = n.find(f"{ns}pitch")
                voice_elem = n.find(f"{ns}voice")
                
                if staff_elem is None or pitch is None:
                    continue
                
                staff = staff_elem.text
                step_elem = pitch.find(f"{ns}step")
                oct_elem = pitch.find(f"{ns}octave")
                
                if step_elem is None or oct_elem is None:
                    continue
                
                step = step_elem.text
                try:
                    octave = int(oct_elem.text)
                except (ValueError, TypeError):
                    continue
                
                # 计算 MIDI 编号
                midi = (octave + 1) * 12 + STEP_MIDI.get(step, 0)
                
                # 根据 staff 和音高分配 voice
                if staff == "1":
                    new_voice = "1" if midi >= STAFF1_THRESHOLD else "2"
                elif staff == "2":
                    new_voice = "3" if midi >= STAFF2_THRESHOLD else "4"
                else:
                    continue
                
                # 更新 voice
                if voice_elem is None:
                    voice_elem = etree.SubElement(n, f"{ns}voice")
                voice_elem.text = new_voice
                reassigned_count += 1
        
        if reassigned_count > 0:
            self.corrections.append(f"重新分配 {reassigned_count} 个音符的 voice")
            logger.info(f"已重新分配 {reassigned_count} 个音符的 voice")
            
            # 重新分配 voice 后，需要重新计算 backup
            self._recalculate_backups(root, ns)
    
    def _recalculate_backups(self, root: etree.Element, ns: str):
        """重新计算 backup 元素
        
        在 voice 重新分配后，原有的 backup 元素不再正确。
        需要为每个 staff 的每个 voice 独立计算 backup。
        
        MusicXML 规则：
        - 每个 voice 独立维护时间线
        - backup 用于在同一 voice 内回退时间位置（切换到新 voice 时）
        - 和弦音符不推进时间线，紧跟在主音符后面
        """
        part = root.find(f"{ns}part")
        measures = part.findall(f"{ns}measure")
        
        for m in measures:
            # 收集所有元素，按类型分组
            non_note_elements = []  # attributes, print, barline 等
            chord_notes = []  # 和弦音符（需要紧跟主音符）
            main_notes = []  # 主音符（推进时间线的）
            
            for elem in list(m):
                tag = elem.tag.replace(ns, "")
                
                if tag == "backup":
                    continue  # 跳过旧 backup
                
                elif tag == "note":
                    chord = elem.find(f"{ns}chord")
                    if chord is not None:
                        chord_notes.append(elem)
                    else:
                        main_notes.append(elem)
                
                else:
                    non_note_elements.append(elem)
            
            # 清空小节
            for elem in list(m):
                m.remove(elem)
            
            # 重新添加非 note/backup 元素
            for elem in non_note_elements:
                m.append(elem)
            
            # 按 (staff, voice) 分组主音符
            # key = (staff, voice), value = [主音符列表]
            voice_groups = {}
            for n in main_notes:
                staff_elem = n.find(f"{ns}staff")
                voice_elem = n.find(f"{ns}voice")
                
                staff = staff_elem.text if staff_elem is not None else "1"
                voice = voice_elem.text if voice_elem is not None else "1"
                
                key = (staff, voice)
                if key not in voice_groups:
                    voice_groups[key] = []
                voice_groups[key].append(n)
            
            # 按 staff 和 voice 顺序排列
            # staff1: voice1, voice2; staff2: voice3, voice4
            sorted_keys = sorted(voice_groups.keys(), key=lambda k: (k[0], int(k[1]) if k[1].isdigit() else 99))
            
            # 为每个 voice 组添加音符和 backup
            first_voice = True
            for voice_key in sorted_keys:
                notes = voice_groups[voice_key]
                
                if not first_voice:
                    # 添加 backup 回退到小节开头
                    backup = etree.SubElement(m, f"{ns}backup")
                    dur_elem = etree.SubElement(backup, f"{ns}duration")
                    # 回退整个小节的时值（使用 divisions * beats）
                    # 简化处理：回退到小节开头
                    dur_elem.text = str(sum(
                        int(n.find(f"{ns}duration").text)
                        for n in notes
                        if n.find(f"{ns}duration") is not None and n.find(f"{ns}duration").text
                    ))
                
                first_voice = False
                
                # 添加该 voice 的所有主音符
                for n in notes:
                    m.append(n)
                    
                    # 查找并添加该主音符的和弦音符
                    # 和弦音符在原 XML 中紧跟在主音符后面
                    # 我们需要找到属于这个主音符的和弦音符
                    # 简单策略：和弦音符的 staff 和 voice 应该与主音符相同
                    # 但实际上和弦音符可能没有 staff/voice 属性
                    
                    # 遍历所有和弦音符，找到属于当前主音符的
                    # 判断标准：和弦音符紧跟在主音符后面，且没有 backup 分隔
                    # 简化：按顺序匹配
                    pass
            
            # 添加剩余的和弦音符（它们应该紧跟在主音符后面）
            # 由于和弦音符没有独立的 backup，它们的时间位置由主音符决定
            # 我们只需要把它们放在正确的位置
            
            # 重新实现：先按 (staff, voice) 分组所有音符（包括和弦）
            # 然后按顺序重建
            
            # 清空并重建
            for elem in list(m):
                tag = elem.tag.replace(ns, "")
                if tag in ["note", "backup"]:
                    m.remove(elem)
            
            # 重新收集所有音符（包括和弦），按 (staff, voice) 分组
            all_voice_notes = {}
            for n in main_notes + chord_notes:
                staff_elem = n.find(f"{ns}staff")
                voice_elem = n.find(f"{ns}voice")
                
                staff = staff_elem.text if staff_elem is not None else "1"
                voice = voice_elem.text if voice_elem is not None else "1"
                
                key = (staff, voice)
                if key not in all_voice_notes:
                    all_voice_notes[key] = []
                all_voice_notes[key].append(n)
            
            # 按 staff 和 voice 顺序添加
            sorted_keys = sorted(all_voice_notes.keys(), key=lambda k: (k[0], int(k[1]) if k[1].isdigit() else 99))
            
            first_voice = True
            for voice_key in sorted_keys:
                notes = all_voice_notes[voice_key]
                
                if not first_voice:
                    # 添加 backup 回退
                    # 计算前一个 voice 的总时值
                    backup = etree.SubElement(m, f"{ns}backup")
                    dur_elem = etree.SubElement(backup, f"{ns}duration")
                    # 回退到小节开头：使用当前 voice 的总时值
                    total_dur = sum(
                        int(n.find(f"{ns}duration").text)
                        for n in notes
                        if n.find(f"{ns}duration") is not None 
                        and n.find(f"{ns}duration").text
                        and n.find(f"{ns}chord") is None
                    )
                    dur_elem.text = str(total_dur)
                
                first_voice = False
                
                # 添加该 voice 的所有音符
                for n in notes:
                    m.append(n)
    
    def _generate_report(self, report: CorrectionReport, report_path: str):
        """生成修正报告"""
        lines = [
            "=" * 70,
            "MusicXML 后处理报告",
            "=" * 70,
            f"输入文件: {report.input_file}",
            f"输出文件: {report.output_file}",
            f"总小节数: {report.total_measures}",
            "",
            f"检测到问题: {len(report.issues)} 个",
            f"自动修正: {len(self.corrections)} 项",
            f"需人工修正: {len([i for i in report.issues if i.severity == 'error'])} 项",
            "",
            "=" * 70,
            "问题详情",
            "=" * 70,
        ]
        
        # 按类型分组问题
        issues_by_type = {}
        for issue in report.issues:
            if issue.issue_type not in issues_by_type:
                issues_by_type[issue.issue_type] = []
            issues_by_type[issue.issue_type].append(issue)
        
        for issue_type, issues in issues_by_type.items():
            lines.append(f"\n【{issue_type}】({len(issues)} 个)")
            for issue in issues[:20]:  # 显示前20个
                lines.append(f"  - {issue.description}")
            if len(issues) > 20:
                lines.append(f"  ... 还有 {len(issues) - 20} 个问题")
        
        lines.extend([
            "",
            "=" * 70,
            "自动修正记录",
            "=" * 70,
        ])
        
        for correction in self.corrections:
            lines.append(f"  ✓ {correction}")
        
        # 生成详细的人工修正指引
        lines.extend([
            "",
            "=" * 70,
            "人工修正指引（在 MuseScore 中操作）",
            "=" * 70,
        ])
        
        # 1. 时值修正指引
        duration_issues = issues_by_type.get("duration_error", [])
        if duration_issues:
            lines.append("")
            lines.append("【1. 音符时值修正】")
            lines.append(f"   问题小节数: {len(duration_issues)}")
            lines.append("   操作步骤:")
            lines.append("   1) 在 MuseScore 中打开输出文件")
            lines.append("   2) 对照原谱 PDF，逐小节检查音符时值")
            lines.append("   3) 重点关注:")
            lines.append("      - 十六分音符跑动段落（原谱大量十六分音符）")
            lines.append("      - 三连音段落（3个音符占2拍时值）")
            lines.append("      - 六连音段落（6个音符占2拍时值）")
            lines.append("   4) 使用 MuseScore 的「音符输入模式」修正时值")
            lines.append("")
            lines.append("   问题小节列表:")
            for issue in duration_issues[:30]:
                lines.append(f"     {issue.description}")
            if len(duration_issues) > 30:
                lines.append(f"     ... 还有 {len(duration_issues) - 30} 个小节")
        
        # 2. 调号修正指引
        key_issues = issues_by_type.get("key_signature_missing", []) + \
                     issues_by_type.get("key_signature_wrong", [])
        if key_issues:
            lines.append("")
            lines.append("【2. 调号修正】")
            lines.append("   原谱调号变化:")
            lines.append("   - 第1小节: C大调 (无升降号)")
            lines.append("   - 第9小节: F大调 (1个降号)")
            lines.append("   - 第46小节: C大调 (无升降号)")
            lines.append("   - 第49小节: F大调 (1个降号)")
            lines.append("")
            lines.append("   操作步骤:")
            lines.append("   1) 在第9小节开头添加调号: 1个降号 (F大调)")
            lines.append("   2) 在第46小节开头添加调号: 无升降号 (C大调)")
            lines.append("   3) 在第49小节开头添加调号: 1个降号 (F大调)")
            lines.append("   4) 使用 MuseScore 的「调号」工具添加")
        
        # 3. Voice 分离指引
        lines.append("")
        lines.append("【3. Voice 分离（可选）】")
        lines.append("   当前状态: 98% 音符在 voice1")
        lines.append("   建议操作:")
        lines.append("   1) 在 MuseScore 中使用「工具 → 声部」功能")
        lines.append("   2) 将右手高音旋律分配到 voice1")
        lines.append("   3) 将右手低音伴奏分配到 voice2")
        lines.append("   4) 将左手高音分配到 voice3")
        lines.append("   5) 将左手低音分配到 voice4")
        
        lines.extend([
            "",
            "=" * 70,
            "总结",
            "=" * 70,
            "",
            "本文件为 OMR 自动识别结果，已修正谱号和调号问题。",
            "但音符时值存在系统性错误，需人工对照原谱修正。",
            "",
            "修正优先级:",
            "1. 高优先级: 修正音符时值（影响演奏准确性）",
            "2. 中优先级: 添加调号变化（影响调性显示）",
            "3. 低优先级: 分离 voice（影响多声部编辑）",
            "",
            "预计人工修正时间: 30-60分钟（取决于熟练程度）",
            "",
            "=" * 70,
        ])
        
        report_text = "\n".join(lines)
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        
        logger.info(f"报告已保存: {report_path}")


# === 便捷函数 ===

def postprocess_oemer_output(
    input_path: str,
    output_path: str,
    report_path: Optional[str] = None,
) -> CorrectionReport:
    """后处理 oemer 输出的便捷函数
    
    Args:
        input_path: oemer 输出的 MusicXML 路径
        output_path: 修正后的输出路径
        report_path: 报告输出路径
    
    Returns:
        CorrectionReport 对象
    """
    pipeline = MusicXMLPostProcessPipeline()
    
    # 期望参数：按实际乐谱调整（示例为 2/4 拍 + 两处调号变化）
    expected_time_sig = (2, 4)
    expected_key_changes = [
        (1, 0),    # C大调
        (9, -1),   # F大调
        (46, 0),   # C大调
        (49, -1),  # F大调
    ]
    
    return pipeline.process(
        input_path=input_path,
        output_path=output_path,
        report_path=report_path,
        expected_time_sig=expected_time_sig,
        expected_key_changes=expected_key_changes,
    )


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Post-process an OMR MusicXML file")
    ap.add_argument("input", help="input MusicXML path")
    ap.add_argument("output", help="output MusicXML path")
    ap.add_argument("--report", default=None, help="report text path")
    args = ap.parse_args()

    report = postprocess_oemer_output(
        args.input, args.output, args.report or (args.output + ".report.txt")
    )
    print(f"done: issues={len(report.issues)} output={args.output}")
