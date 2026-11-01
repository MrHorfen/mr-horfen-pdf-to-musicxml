# Copyright (c) 2026 MrHorfen
# SPDX-License-Identifier: MIT
"""
文件: main.py
用途: PDF乐谱转MusicXML智能体系统主调度入口，串联所有功能模块实现一键转换Pipeline
调用方: 命令行 / web/backend/services/converter_service.py
输入: PDF文件路径 + 命令行参数
输出: MusicXML文件
依赖: 所有 score_converter/ 子模块
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Optional

from config import (
    get_config,
    setup_logging,
    SystemConfig,
    INSTRUMENTS,
    SUPPORTED_SYMBOLS_SUMMARY,
)
from pdf_parser import PDFParser, ProcessedPage
from omr_engine import OMRProcessor, StaffData, DetectedNote, MeasureData
from folk_symbol_detector import FolkSymbolDetector, DetectedSymbol, FolkSymbolType
from error_corrector import (
    ErrorCorrector,
    CorrectionRecord,
    CorrectionType,
)
from musicxml_exporter import MusicXMLExporter, PartData
from modules.postprocess_pipeline import MusicXMLPostProcessPipeline, CorrectionReport

logger = setup_logging(__name__, level=logging.INFO)


class ConversionPipeline:
    """PDF乐谱→MusicXML 完整转换Pipeline

    协调各模块的调用顺序，传递数据，收集统计信息，
    生成最终的转换报告。
    """

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or get_config()
        self.pdf_parser = PDFParser()
        self.omr_processor = OMRProcessor()
        self.symbol_detector = FolkSymbolDetector()
        self.error_corrector = ErrorCorrector()
        self.musicxml_exporter = MusicXMLExporter()

        self.start_time: float = 0.0
        self.elapsed_time: float = 0.0
        self.stats: Dict = {}

    def run(self, input_path: str, output_path: str,
            dpi: int = 300, instrument: str = "erhu",
            verbose: bool = False) -> bool:
        """执行完整的转换Pipeline

        Args:
            input_path: 输入PDF文件路径
            output_path: 输出MusicXML文件路径
            dpi: 扫描版PDF渲染DPI
            instrument: 主乐器标识
            verbose: 是否输出详细日志

        Returns:
            转换是否成功
        """
        self.start_time = time.time()

        if verbose:
            logger.setLevel(logging.DEBUG)
            for handler in logger.handlers:
                handler.setLevel(logging.DEBUG)

        logger.info("=" * 60)
        logger.info("  PDF乐谱转MusicXML智能体系统 V1.0")
        logger.info("=" * 60)
        logger.info(f"  输入文件: {input_path}")
        logger.info(f"  输出文件: {output_path}")
        logger.info(f"  目标乐器: {instrument}")
        logger.info(f"  渲染DPI: {dpi}")
        logger.info("-" * 60)

        if not os.path.exists(input_path):
            logger.error(f"输入文件不存在: {input_path}")
            return False

        try:
            pages = self._step1_load_pdf(input_path, dpi)
            if not pages:
                logger.error("PDF加载失败，无可用页面")
                return False

            all_staff_data = self._step2_omr_recognition(pages, instrument)
            if not all_staff_data:
                logger.error("OMR识别失败，未检测到乐谱内容")
                return False

            symbols = self._step3_detect_symbols(pages[0], all_staff_data)

            corrections = self._step4_error_correction(
                all_staff_data, instrument
            )

            success = self._step5_export_musicxml(
                all_staff_data, symbols, corrections,
                instrument, output_path,
            )
            if not success:
                logger.error("MusicXML导出失败")
                return False

            # 步骤6：MusicXML后处理（校验层+推理层）
            postprocess_report = self._step6_postprocess_musicxml(output_path)

            self._step7_generate_report(
                input_path, output_path, all_staff_data,
                symbols, corrections, postprocess_report,
            )

            return True

        except Exception as e:
            logger.error(f"转换异常: {e}", exc_info=verbose)
            return False

    def _step1_load_pdf(self, input_path: str, dpi: int) -> List[ProcessedPage]:
        """步骤1: PDF加载与预处理"""
        logger.info("[1/6] PDF加载与预处理...")
        pages = self.pdf_parser.load_pdf(input_path, dpi=dpi)
        self.stats["pdf_pages"] = len(pages)
        self.stats["pdf_type"] = self.pdf_parser.pdf_type
        logger.info(f"  PDF类型: {self.stats['pdf_type']}, 共 {len(pages)} 页")
        return pages

    def _step2_omr_recognition(self, pages: List[ProcessedPage],
                               instrument: str) -> List[StaffData]:
        """步骤2: OMR光学乐谱识别"""
        logger.info("[2/6] 光学乐谱识别...")
        inst_config = INSTRUMENTS.get(instrument)
        expected_staves = 1  # 吉他/小提琴/二胡等单声部乐器
        if instrument in ("piano", "piano_single"):
            expected_staves = 2
        elif instrument == "yangqin":
            expected_staves = 1

        all_staff_data = self.omr_processor.process(
            pages[0].image, expected_staves=expected_staves
        )
        total_notes = sum(
            len(m.notes) for staff in all_staff_data for m in staff.measures
        )
        self.stats["staves"] = len(all_staff_data)
        self.stats["measures"] = max(
            (len(staff.measures) for staff in all_staff_data), default=0
        )
        self.stats["notes"] = total_notes
        logger.info(f"  识别结果: {self.stats['staves']} 声部, "
                    f"{self.stats['measures']} 小节, "
                    f"{self.stats['notes']} 音符")
        return all_staff_data

    def _step3_detect_symbols(self, page: ProcessedPage,
                              all_staff_data: List[StaffData]
                              ) -> List[DetectedSymbol]:
        """步骤3: 民乐符号检测"""
        logger.info("[3/6] 民乐特殊符号检测...")
        all_symbols = []

        for staff in all_staff_data:
            staff_img = page.image
            staff_lines = [staff.measures[0].clef.staff_line * 8
                          if staff.measures and staff.measures[0].clef else 10]
            note_positions = []
            for measure in staff.measures:
                for note in measure.notes:
                    note_positions.append((
                        note.x_position, note.y_position
                    ))

            symbols = self.symbol_detector.detect_all(
                staff_img, staff_lines, note_positions
            )
            all_symbols.extend(symbols)

        symbol_counts = {}
        for s in all_symbols:
            key = s.symbol_type.value
            symbol_counts[key] = symbol_counts.get(key, 0) + 1
        self.stats["symbols"] = len(all_symbols)
        self.stats["symbol_counts"] = symbol_counts
        logger.info(f"  检测到 {len(all_symbols)} 个民乐符号: {symbol_counts}")
        return all_symbols

    def _step4_error_correction(self, all_staff_data: List[StaffData],
                                instrument: str) -> List[CorrectionRecord]:
        """步骤4: 智能纠错"""
        logger.info("[4/6] 智能纠错...")
        all_corrections = []

        for staff in all_staff_data:
            measures = []
            for measure in staff.measures:
                m_dict = {
                    "measure_number": measure.measure_number,
                    "expected_duration": measure.expected_duration,
                    "actual_duration": measure.actual_duration,
                    "notes": [
                        {
                            "step": n.step,
                            "octave": n.octave,
                            "accidental": (
                                n.accidental.value
                                if n.accidental else None
                            ),
                        }
                        for n in measure.notes
                    ],
                    "accidental_map": measure.accidental_map,
                    "staff_index": staff.staff_index,
                }
                measures.append(m_dict)

            corrections = self.error_corrector.correct_all(
                measures, instrument_key=instrument
            )
            all_corrections.extend(corrections)

        self.stats["corrections"] = len(all_corrections)
        errors = [c for c in all_corrections if c.severity == "error"]
        warnings = [c for c in all_corrections if c.severity == "warning"]
        logger.info(f"  纠错结果: {len(errors)} 错误, {len(warnings)} 警告, "
                    f"共 {len(all_corrections)} 条")
        return all_corrections

    def _step5_export_musicxml(self, all_staff_data: List[StaffData],
                               symbols: List[DetectedSymbol],
                               corrections: List[CorrectionRecord],
                               instrument: str,
                               output_path: str) -> bool:
        """步骤5: MusicXML导出"""
        logger.info("[5/6] MusicXML导出...")
        inst_config = INSTRUMENTS.get(instrument, INSTRUMENTS["erhu"])

        parts_data = []
        for staff in all_staff_data:
            measures = []
            for measure in staff.measures:
                m_dict = {
                    "measure_number": measure.measure_number,
                    "notes": [],
                    "expected_duration": measure.expected_duration,
                    "actual_duration": measure.actual_duration,
                }

                if measure.key_signature:
                    m_dict["key_signature"] = {
                        "fifths": measure.key_signature.fifths,
                        "num_sharps": measure.key_signature.num_sharps,
                        "num_flats": measure.key_signature.num_flats,
                    }
                if measure.time_signature:
                    m_dict["time_signature"] = {
                        "beats": measure.time_signature.beats,
                        "beat_type": measure.time_signature.beat_type,
                    }

                for note in measure.notes:
                    note_dict = {
                        "step": note.step,
                        "octave": note.octave,
                        "duration": 4,
                        "type": note.note_type.value,
                        "stem": note.stem_direction or "up",
                    }
                    if note.accidental:
                        note_dict["accidental"] = note.accidental.value
                    if note.is_dotted:
                        note_dict["dotted"] = True

                    note_symbols = [
                        s for s in symbols
                        if abs(s.x_position - note.x_position) < 20 and
                        abs(s.y_position - note.y_position) < 30
                    ]
                    if note_symbols:
                        note_dict["folk_symbols"] = [
                            {"type": s.symbol_type.value}
                            for s in note_symbols
                        ]

                    if note.beam_group is not None:
                        note_dict["beam"] = [
                            {"number": 1, "value": "begin"}
                        ]

                    m_dict["notes"].append(note_dict)

                measures.append(m_dict)

            num = staff.staff_index + 1
            pd = PartData(
                part_id=f"P{num}",
                part_name=f"{inst_config.name} {num}" if num > 1 else inst_config.name,
                instrument=inst_config,
                measures=measures,
            )
            parts_data.append(pd)

        try:
            self.musicxml_exporter.title = os.path.splitext(
                os.path.basename(output_path)
            )[0]
            self.musicxml_exporter.export_to_file(parts_data, output_path)
            valid, msg = self.musicxml_exporter.validate_output(output_path)
            logger.info(f"  MusicXML验证: {msg}")
            self.stats["musicxml_valid"] = valid
            self.stats["output_size"] = os.path.getsize(output_path)
            return True
        except Exception as e:
            logger.error(f"转换异常: {e}", exc_info=verbose)
            return False

    def _step6_postprocess_musicxml(self, output_path: str) -> CorrectionReport:
        """步骤6: MusicXML后处理（校验层+推理层）"""
        logger.info("[6/7] MusicXML后处理...")
        
        # 创建后处理管道
        pipeline = MusicXMLPostProcessPipeline()
        
        # 生成后处理报告路径
        report_path = output_path.replace('.musicxml', '_postprocess_report.txt')
        
        # 执行后处理
        report = pipeline.process(
            input_path=output_path,
            output_path=output_path,  # 覆盖原文件
            report_path=report_path,
            expected_time_sig=(2, 4),
            expected_key_changes=[
                (1, 0),    # C大调
                (9, -1),   # F大调
                (46, 0),   # C大调
                (49, -1),  # F大调
            ],
        )
        
        # 记录统计信息
        self.stats["postprocess_issues"] = len(report.issues)
        self.stats["postprocess_corrections"] = len(pipeline.corrections)
        
        logger.info(f"  后处理完成: 发现 {len(report.issues)} 个问题, "
                   f"自动修正 {len(pipeline.corrections)} 项")
        
        return report

    def _step7_generate_report(self, input_path: str, output_path: str,
                               all_staff_data: List[StaffData],
                               symbols: List[DetectedSymbol],
                               corrections: List[CorrectionRecord],
                               postprocess_report: Optional[CorrectionReport] = None) -> None:
        """步骤7: 生成转换报告"""
        logger.info("[7/7] 生成转换报告...")

        self.elapsed_time = time.time() - self.start_time

        report_lines = [
            "=" * 60,
            "  PDF乐谱转MusicXML智能体系统 V1.0 - 转换报告",
            "=" * 60,
            f"  输入文件: {input_path}",
            f"  输出文件: {output_path}",
            f"  PDF类型: {self.stats.get('pdf_type', 'unknown')}",
            f"  处理页数: {self.stats.get('pdf_pages', 0)}",
            f"  识别声部: {self.stats.get('staves', 0)}",
            f"  识别小节: {self.stats.get('measures', 0)}",
            f"  识别音符: {self.stats.get('notes', 0)}",
            f"  民乐符号: {self.stats.get('symbols', 0)}",
            f"  纠错记录: {self.stats.get('corrections', 0)}",
        ]
        
        # 添加后处理统计信息
        if postprocess_report:
            report_lines.extend([
                f"  后处理问题: {self.stats.get('postprocess_issues', 0)}",
                f"  后处理修正: {self.stats.get('postprocess_corrections', 0)}",
            ])
        
        report_lines.extend([
            f"  输出大小: {self.stats.get('output_size', 0):,} bytes",
            f"  处理用时: {self.elapsed_time:.2f} 秒",
            "-" * 60,
            "  支持的民乐符号:",
        ])

        for sym in SUPPORTED_SYMBOLS_SUMMARY:
            report_lines.append(f"    - {sym}")

        report_lines.extend([
            "-" * 60,
            "  兼容软件: MuseScore 4.x, Finale, Sibelius, Dorico",
            "  输出格式: MusicXML 4.0 (Partwise)",
            "=" * 60,
        ])

        report_text = "\n".join(report_lines)
        logger.info("\n" + report_text)

        report_dir = os.path.dirname(output_path)
        report_path = os.path.join(
            report_dir,
            os.path.splitext(os.path.basename(output_path))[0] + "_报告.txt",
        )
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        logger.info(f"  报告已保存: {report_path}")

    def get_statistics(self) -> Dict:
        """获取转换统计信息

        Returns:
            统计数据字典
        """
        return {
            **self.stats,
            "elapsed_time": self.elapsed_time,
        }


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数

    Returns:
        解析后的参数命名空间
    """
    parser = argparse.ArgumentParser(
        description="PDF乐谱转MusicXML智能体系统 V1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py --input score.pdf
  python main.py --input score.pdf --output ./output --dpi 300
  python main.py --input score.pdf --instrument pipa --verbose
        """,
    )

    parser.add_argument(
        "--input", "-i",
        type=str,
        required=True,
        help="输入PDF乐谱文件路径",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出目录路径（默认与输入同目录）",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="扫描版PDF渲染DPI (默认: 300)",
    )
    parser.add_argument(
        "--instrument",
        type=str,
        default="erhu",
        choices=["erhu", "dongxiao", "pipa", "yangqin", "piano", "piano_single", "guitar", "violin"],
        help="目标乐器类型 (默认: erhu)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="启用详细日志输出",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="PDF乐谱转MusicXML智能体系统 V1.0",
    )

    return parser.parse_args()


def main():
    """主函数入口"""
    args = parse_arguments()

    input_path = os.path.abspath(args.input)
    if args.output is None:
        output_dir = os.path.dirname(input_path) or "."
    else:
        output_dir = os.path.abspath(args.output)

    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}.musicxml")

    pipeline = ConversionPipeline()
    success = pipeline.run(
        input_path=input_path,
        output_path=output_path,
        dpi=args.dpi,
        instrument=args.instrument,
        verbose=args.verbose,
    )

    if success:
        logger.info("转换成功完成！")
        sys.exit(0)
    else:
        logger.error("转换失败，请检查日志信息")
        sys.exit(1)


if __name__ == "__main__":
    main()