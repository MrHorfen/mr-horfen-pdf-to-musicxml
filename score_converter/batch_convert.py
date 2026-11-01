"""
文件: batch_convert.py
用途: PDF乐谱批量转MusicXML主程序，预处理→OMR识别→纠错→MusicXML导出完整流水线
调用方: 命令行 / 批量处理任务
输入: PDF文件目录
输出: MusicXML文件
依赖: PyMuPDF(fitz), cv2, numpy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import sys
import argparse
import logging
import json
import time
from pathlib import Path

# 添加模块搜索路径
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.pdf_preprocessor import PDFPreprocessor
from modules.image_enhancer import ImageEnhancer
from modules.omr_engine import OMRProcessor
from modules.musicxml_postprocessor import MusicXMLPostprocessor


def setup_logging(level=logging.INFO):
    """配置日志输出"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


logger = setup_logging()


def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="PDF乐谱批量转MusicXML - 批量处理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  %(prog)s
  %(prog)s --input ./my_scores/ --output ./my_output/
  %(prog)s --input score.pdf --dpi 400
  %(prog)s --config custom_config.json
        """,
    )
    parser.add_argument(
        "--input", "-i",
        default=os.path.join(PROJECT_ROOT, "输入"),
        help="输入目录或PDF文件路径 (默认: ./input/)",
    )
    parser.add_argument(
        "--output", "-o",
        default=os.path.join(PROJECT_ROOT, "输出"),
        help="输出目录路径 (默认: ./output/)",
    )
    parser.add_argument(
        "--dpi", "-d",
        type=int,
        default=300,
        help="扫描PDF渲染DPI (默认: 300)",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="JSON配置文件路径 (可选)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="启用详细日志输出",
    )
    return parser.parse_args()


def load_config(config_path):
    """加载JSON配置文件"""
    if config_path and os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def find_pdf_files(input_path):
    """查找所有PDF文件

    Args:
        input_path: 文件或目录路径

    Returns:
        PDF文件路径列表, 是否为单文件模式
    """
    if os.path.isfile(input_path):
        if input_path.lower().endswith(".pdf"):
            return [input_path], True
        else:
            logger.warning(f"非PDF文件: {input_path}")
            return [], False
    elif os.path.isdir(input_path):
        pdf_files = sorted([
            os.path.join(input_path, f)
            for f in os.listdir(input_path)
            if f.lower().endswith(".pdf")
        ])
        if not pdf_files:
            logger.warning(f"目录中未找到PDF文件: {input_path}")
        return pdf_files, False
    else:
        logger.error(f"路径不存在: {input_path}")
        return [], False


def process_single_pdf(pdf_path, output_dir, dpi, config):
    """处理单个PDF文件

    执行为民乐合奏谱定制的完整识别流水线：
    PDF预处理 → 图像增强 → OMR识别 → MusicXML后处理

    Args:
        pdf_path: PDF文件路径
        output_dir: 输出目录
        dpi: 扫描DPI
        config: 配置字典

    Returns:
        (成功标志, 输出文件路径或错误信息)
    """
    filename = os.path.splitext(os.path.basename(pdf_path))[0]
    logger.info(f"{'='*60}")
    logger.info(f"开始处理: {filename}")
    logger.info(f"{'='*60}")

    input_name = Path(pdf_path).stem
    part_output_dir = os.path.join(output_dir, input_name)
    os.makedirs(part_output_dir, exist_ok=True)

    try:
        # 1. PDF预处理
        logger.info("[1/4] PDF预处理...")
        preprocessor = PDFPreprocessor(dpi=dpi, config=config)
        pages = preprocessor.process(pdf_path)
        logger.info(f"  -> 完成: {len(pages)} 页")

        # 2. 图像增强
        logger.info("[2/4] 图像增强...")
        enhancer = ImageEnhancer(config=config)
        enhanced_pages = enhancer.enhance_all(pages)
        logger.info(f"  -> 完成: {len(enhanced_pages)} 页增强")

        # 3. OMR识别
        logger.info("[3/4] OMR乐谱识别...")
        omr_processor = OMRProcessor(config=config)
        staves = omr_processor.process(enhanced_pages)

        staff_count = sum(len(s) for s in staves)
        note_count = sum(
            len(m.notes)
            for staff_list in staves
            for staff in staff_list
            for m in staff.measures
        )
        logger.info(f"  -> 完成: {staff_count} 声部, {note_count} 音符")

        # 4. MusicXML后处理与导出
        logger.info("[4/4] MusicXML后处理与导出...")
        postprocessor = MusicXMLPostprocessor(config=config)

        output_path = os.path.join(part_output_dir, f"{input_name}.musicxml")
        result = postprocessor.export(staves, output_path)
        logger.info(f"  -> 导出完成: {output_path}")

        # 5. 生成处理报告
        report_path = os.path.join(part_output_dir, f"{input_name}_report.json")
        report = {
            "file": pdf_path,
            "pages": len(pages),
            "staves": staff_count,
            "notes": note_count,
            "output": output_path,
            "status": "success",
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"  -> 报告已保存: {report_path}")
        logger.info(f"{'='*60}")
        logger.info(f"✓ 处理成功: {filename}")
        logger.info(f"{'='*60}")

        return True, output_path

    except Exception as e:
        logger.error(f"✗ 处理失败: {filename} - {e}", exc_info=True)

        # 保存错误报告
        report_path = os.path.join(part_output_dir, f"{input_name}_report.json")
        report = {
            "file": pdf_path,
            "status": "failed",
            "error": str(e),
        }
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return False, str(e)


def print_summary(results, total_time):
    """打印处理汇总"""
    success_count = sum(1 for r in results if r[0])
    fail_count = len(results) - success_count

    print()
    print("=" * 60)
    print("  处理汇总")
    print("=" * 60)
    print(f"  总文件数:    {len(results)}")
    print(f"  成功:        {success_count}")
    print(f"  失败:        {fail_count}")
    print(f"  总耗时:      {total_time:.1f} 秒")
    if success_count > 0:
        print(f"  平均耗时:    {total_time / success_count:.1f} 秒/文件")
    print("=" * 60)
    print()
    print(f"输出目录: {output_dir}")
    print()


def main():
    """主程序入口"""
    global output_dir
    args = parse_arguments()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logger.setLevel(log_level)

    config = load_config(args.config)

    input_path = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    pdf_files, single_mode = find_pdf_files(input_path)

    if not pdf_files:
        logger.error("未找到可处理的PDF文件")
        sys.exit(1)

    logger.info(f"找到 {len(pdf_files)} 个PDF文件")
    if single_mode:
        logger.info(f"单文件模式: {input_path}")
    else:
        logger.info(f"批量模式: {input_path}")
    logger.info(f"输出目录: {output_dir}")
    logger.info(f"扫描DPI: {args.dpi}")
    print()

    start_time = time.time()
    results = []

    for i, pdf_path in enumerate(pdf_files, 1):
        logger.info(f"\n处理进度: [{i}/{len(pdf_files)}]")
        result = process_single_pdf(pdf_path, output_dir, args.dpi, config)
        results.append(result)

    total_time = time.time() - start_time
    print_summary(results, total_time)


if __name__ == "__main__":
    main()