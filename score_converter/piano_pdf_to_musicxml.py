# Copyright (c) 2026 MrHorfen
# SPDX-License-Identifier: MIT
"""
文件: piano_pdf_to_musicxml.py
用途: 钢琴PDF转MusicXML转换脚本，使用Audiveris OMR引擎，复用PDF预处理管线
调用方: main.py / converter_service.py
输入: 钢琴PDF文件
输出: MusicXML文件
依赖: Audiveris(Java), PyMuPDF(fitz), cv2
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-10-21
更新说明: 合并阶段改为结构安全组装（声部并集 + 缺声部补休止 + 补登记 score-part），
         交付前做结构校验并输出用户可读错误；仅各页声部结构一致时才合并为单一钢琴Part
"""

import os
import sys
import subprocess
import tempfile
import shutil
import zipfile
import concurrent.futures
import threading
import uuid
import re
from pathlib import Path
import fitz  # PyMuPDF
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# 子进程内并发限流：限制单个 PDF 转码任务中同时运行的 Audiveris Java 进程数
_AUDIVERIS_SEMAPHORE = threading.Semaphore(6)


def _extract_audiveris_error(stdout: str, stderr: str) -> str:
    """从 Audiveris 输出中提取可读的失败原因（供上层生成用户可读错误）"""
    text = f"{stdout}\n{stderr}"

    if "too low interline" in text or "interline value of" in text:
        m = re.search(r"interline value of (\d+) pixels", text)
        val = m.group(1) if m else "?"
        return (
            f"谱线间距过低（{val}px，正常需约20px），Audiveris 无法识别该谱面。"
            f"可能原因：乐谱过密或分辨率不足，请更换更清晰的扫描版乐谱"
        )
    if "no multi-line staves" in text:
        return "未检测到五线谱结构，请确认上传的是五线谱乐谱"
    if "resolution is too low" in text:
        return "图像分辨率过低，无法识别，请使用更清晰的扫描件"
    if "Error in export" in text or "did not complete successfully" in text:
        return "Audiveris 识别未完成，无法导出乐谱（可能包含无法识别的元素）"
    for line in reversed(text.splitlines()):
        if "Exception" in line or "ERROR" in line.upper():
            return line.strip()[:200]
    return "Audiveris 识别失败"


def pdf_to_images(pdf_path, output_dir, dpi=300, max_pixels=19_000_000,
                  enhancement_method: str = "official",
                  use_pipeline: bool = False,
                  pipeline_config: dict = None):
    """将PDF转换为PNG图像

    默认使用简单渲染（直接PyMuPDF渲染 + 像素控制），这是经过验证的稳定方案。
    预处理管线（PDFPreprocessingPipeline）仅在 use_pipeline=True 时启用，
    适用于需要文字清理/谱线裁剪的特殊PDF。

    Args:
        pdf_path: PDF文件路径
        output_dir: 输出目录
        dpi: 渲染DPI
        max_pixels: 最大像素数（超过则等比缩小）
        enhancement_method: 增强方法（预留，当前简单渲染不使用）
        use_pipeline: 是否使用预处理管线（默认False）
        pipeline_config: 前端传来的预处理模块勾选配置
            {typeDetect, adaptiveDpi, deskew, enhancement, qualityCheck, regionCut}
    """
    logger.info(f"将PDF转换为图像: {pdf_path}")

    if use_pipeline:
        logger.info(f"使用预处理管线模式 (config={pipeline_config})")
        try:
            score_converter_root = Path(__file__).parent
            if str(score_converter_root) not in sys.path:
                sys.path.insert(0, str(score_converter_root))

            from modules.pdf_preprocessor import quick_preprocess

            # 将前端勾选配置转换为管线 custom_config
            custom_config = {
                "dpi": dpi,
                "deskew_enabled": pipeline_config.get("deskew", True) if pipeline_config else True,
                "enhancement_enabled": pipeline_config.get("enhancement", False) if pipeline_config else False,
                "region_cut_enabled": pipeline_config.get("regionCut", False) if pipeline_config else False,
            }

            output_files = quick_preprocess(
                pdf_path, output_dir, profile="standard",
                custom_config=custom_config,
            )
            logger.info(f"预处理管线完成: {len(output_files)} 页")
            for f in output_files:
                logger.info(f"  -> 保存: {f}")
            return output_files

        except Exception as e:
            logger.warning(f"预处理管线失败 ({e})，回退到简单渲染")

    # 简单渲染（默认方案）：直接PyMuPDF渲染 + 像素控制
    logger.info(f"使用简单渲染模式 (DPI={dpi})")
    doc = fitz.open(pdf_path)
    os.makedirs(output_dir, exist_ok=True)
    image_paths = []

    for i in range(len(doc)):
        page = doc[i]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        total_pixels = pix.width * pix.height
        if total_pixels > max_pixels:
            scale = (max_pixels / total_pixels) ** 0.5
            new_dpi = dpi * scale
            logger.info(f"  第{i+1}页: {pix.width}x{pix.height}={total_pixels:,}像素超限，缩放DPI {dpi:.0f}→{new_dpi:.0f}")
            mat = fitz.Matrix(new_dpi / 72, new_dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)

        img_path = os.path.join(output_dir, f"page_{i+1:03d}.png")
        pix.save(img_path)
        image_paths.append(img_path)
        logger.info(f"  第{i+1}页: {pix.width}x{pix.height} -> {img_path}")

    doc.close()
    return image_paths


def run_audiveris(image_path, output_dir):
    """运行Audiveris OMR识别"""
    logger.info(f"运行Audiveris识别: {image_path}")
    
    # 不指定 twoStaves=true，让 Audiveris 自动检测谱号（高音+低音）
    # twoStaves=true 会强制生成两个相同谱号的 staff，导致两个高音谱表
    cmd = [
        "audiveris",
        "-batch",
        "-export",
        "-output", output_dir,
        image_path,
    ]
    
    logger.info(f"命令: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            encoding="utf-8",
            errors="ignore",
        )
        
        if result.stdout:
            logger.debug(f"Audiveris stdout: {result.stdout[:500]}")
        if result.stderr:
            logger.debug(f"Audiveris stderr: {result.stderr[:500]}")
        
        if result.returncode != 0:
            reason = _extract_audiveris_error(result.stdout or "", result.stderr or "")
            logger.error(f"Audiveris执行失败 (code={result.returncode}): {reason}")
            return None
        
        # 查找生成的MusicXML文件（Audiveris生成.mxl压缩格式）
        for pattern in ["*.xml", "*.musicxml", "*.mxl"]:
            files = list(Path(output_dir).rglob(pattern))
            if files:
                return str(files[0])
        
        return None
        
    except subprocess.TimeoutExpired:
        logger.error("Audiveris识别超时")
        return None
    except Exception as e:
        logger.error(f"Audiveris识别异常: {e}")
        return None


def extract_mxl_to_xml(mxl_path, output_dir):
    """从.mxl压缩文件中提取MusicXML"""
    try:
        with zipfile.ZipFile(mxl_path, 'r') as zf:
            # .mxl是zip格式，直接查找.xml文件
            xml_names = [n for n in zf.namelist() if n.endswith('.xml') and not n.startswith('META-INF/')]
            if xml_names:
                xml_path_in_zip = xml_names[0]
                # 提取XML文件
                xml_content = zf.read(xml_path_in_zip)
                xml_output = os.path.join(output_dir, Path(mxl_path).stem + '.xml')
                with open(xml_output, 'wb') as f:
                    f.write(xml_content)
                return xml_output
    except Exception as e:
        logger.error(f"解压.mxl失败: {e}")
    return None


def merge_musicxml_files(xml_files, output_path):
    """合并多个MusicXML文件为一个"""
    logger.info(f"合并 {len(xml_files)} 个MusicXML文件")
    
    if len(xml_files) == 1:
        if xml_files[0].endswith('.mxl'):
            temp_dir = os.path.dirname(xml_files[0])
            xml_path = extract_mxl_to_xml(xml_files[0], temp_dir)
            if xml_path:
                shutil.copy2(xml_path, output_path)
                return True
        else:
            shutil.copy2(xml_files[0], output_path)
            return True
    
    try:
        from lxml import etree
        
        # 处理所有文件，如果是.mxl则先解压
        processed_xml_files = []
        for xml_file in xml_files:
            if xml_file.endswith('.mxl'):
                temp_dir = os.path.dirname(xml_file)
                extracted = extract_mxl_to_xml(xml_file, temp_dir)
                if extracted:
                    processed_xml_files.append(extracted)
            else:
                processed_xml_files.append(xml_file)
        
        if not processed_xml_files:
            logger.error("没有可处理的XML文件")
            return False
        
        # 按页序组装（结构安全版）：声部取并集、缺声部补休止、跨页新声部补登记 score-part
        score_converter_root = Path(__file__).parent
        if str(score_converter_root) not in sys.path:
            sys.path.insert(0, str(score_converter_root))
        from modules.musicxml_merge import MusicXMLMergeError, assemble_pages, validate_merged

        roots = [etree.parse(f).getroot() for f in processed_xml_files]
        structures = {tuple(sorted(p.get("id") for p in r.findall("part"))) for r in roots}
        merged = assemble_pages(roots)

        # 各页声部结构一致时，保持既有交付形态：合并为单一钢琴 Part（staff 1 / staff 2）
        if len(structures) == 1 and len(merged.findall("part")) > 1:
            _collapse_to_single_part(merged)

        validate_merged(merged, title="乐谱合并结果")
        merged.getroottree().write(output_path, encoding="utf-8", xml_declaration=True)
        logger.info(f"合并完成: {output_path}")
        return True

    except MusicXMLMergeError as e:
        logger.error(f"乐谱合并结果结构异常，已阻止交付：{e}")
        return False
    except Exception as e:
        logger.error(f"合并MusicXML失败：{e}。请重试；若仍失败，请更换更清晰的扫描件。")
        return False


def _collapse_to_single_part(root):
    """将多 Part 合并为单一钢琴 Part：保留音符最多的 Part，其余音符并入其 staff 2"""
    from copy import deepcopy
    from lxml import etree as _etree

    parts = root.findall("part")
    note_counts = {}
    for part in parts:
        pid = part.get("id")
        note_counts[pid] = sum(1 for _ in part.findall(".//note"))
        logger.info(f"  Part {pid}: {note_counts[pid]} 个音符")

    best_id = max(note_counts, key=note_counts.get)
    best_part = root.find(f"part[@id='{best_id}']")
    logger.info(f"  以Part {best_id}为主体（{note_counts[best_id]}个音符）")

    best_measures = {m.get("number"): m for m in best_part.findall("measure")}
    for part in parts:
        if part.get("id") == best_id:
            continue
        added = 0
        for m in part.findall("measure"):
            target = best_measures.get(m.get("number"))
            if target is None:
                continue
            for note in m.findall("note"):
                staff_el = note.find("staff")
                if staff_el is not None:
                    staff_el.text = "2"
                else:
                    _etree.SubElement(note, "staff").text = "2"
                target.append(deepcopy(note))
                added += 1
        logger.info(f"  从Part {part.get('id')}补充了{added}个音符到staff 2")

    for part in parts:
        if part.get("id") != best_id:
            root.remove(part)

    part_list = root.find("part-list")
    if part_list is not None:
        for sp in part_list.findall("score-part"):
            if sp.get("id") != best_id:
                part_list.remove(sp)

    logger.info("  合并完成，最终保留1个Part")


def normalize_musicxml_tempo(xml_path: str, target_duration_sec: float):
    """
    节奏归一化：按目标时长缩放 MusicXML 中所有音符时值

    解决 Audiveris 将音符时值偏大（如八分音符→四分音符）导致
    总时长偏长的问题。通过全局缩放矫正。

    Args:
        xml_path: MusicXML 文件路径（原地修改）
        target_duration_sec: 期望的总时长（秒）
    """
    from lxml import etree

    tree = etree.parse(xml_path)
    root = tree.getroot()

    # 计算当前总时长
    current_duration = 0.0
    divisions = None

    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            # 获取 divisions（每四分音符的刻度数）
            attrs = measure.find('attributes')
            if attrs is not None:
                div_elem = attrs.find('divisions')
                if div_elem is not None:
                    divisions = int(div_elem.text)

            for note in measure.findall('note'):
                dur_elem = note.find('duration')
                if dur_elem is not None and divisions:
                    current_duration += int(dur_elem.text) / divisions

    if current_duration <= 0 or target_duration_sec <= 0:
        logger.warning(f"节奏归一化跳过: current={current_duration:.1f}, target={target_duration_sec:.1f}")
        return

    scale = target_duration_sec / current_duration
    if 0.95 < scale < 1.05:
        logger.info(f"节奏已在合理范围 (±5%)，无需归一化: {current_duration:.1f}s")
        return

    # 缩放所有音符的 duration
    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            for note in measure.findall('note'):
                dur_elem = note.find('duration')
                if dur_elem is not None:
                    new_dur = max(1, round(int(dur_elem.text) * scale))
                    dur_elem.text = str(new_dur)

    tree.write(xml_path, encoding='utf-8', xml_declaration=True)
    logger.info(f"节奏归一化: {current_duration:.1f}s → {target_duration_sec:.1f}s (scale={scale:.3f})")


def main():
    """主函数 - 支持命令行参数"""
    import argparse

    parser = argparse.ArgumentParser(description="钢琴PDF转MusicXML（Audiveris引擎）")
    parser.add_argument("--input", "-i", required=True, help="输入PDF路径")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--output-name", type=str, default=None,
                        help="输出文件名（不含路径），默认使用输入PDF的stem")
    parser.add_argument("--dpi", type=int, default=300, help="渲染DPI（默认300）")
    parser.add_argument("--instrument", default="piano", help="乐器类型（默认piano）")
    parser.add_argument("--target-duration", type=float, default=None,
                        help="目标时长（秒），用于节奏归一化，矫正Audiveris时值偏长问题")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--use-pipeline", action="store_true", help="启用预处理管线")
    parser.add_argument("--pipeline-config", type=str, default=None,
                        help="预处理配置（JSON字符串）")
    parser.add_argument("--per-page", action="store_true",
                        help="逐页识别模式：每页独立输出MusicXML，不合并")
    args = parser.parse_args()

    input_pdf = args.input
    output_dir = args.output
    dpi = args.dpi
    instrument = args.instrument

    # 根据乐器选择增强方法
    INSTRUMENT_ENHANCEMENT = {
        "guitar": "guitar",
        "piano": "official",
        "violin": "official",
        "flute": "official",
    }
    enhancement_method = INSTRUMENT_ENHANCEMENT.get(instrument, "official")

    # 输出文件名：优先使用 --output-name，否则用输入PDF的stem
    if args.output_name:
        base_name = Path(args.output_name).stem
    else:
        base_name = Path(input_pdf).stem
    output_path = os.path.join(output_dir, f"{base_name}.musicxml")

    # 临时目录（UUID 保证多用户并发时目录不冲突）
    temp_dir = os.path.join(tempfile.gettempdir(), f"audiveris_{base_name}_{uuid.uuid4().hex[:8]}")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    logger.info("=" * 60)
    logger.info("钢琴PDF转MusicXML转换（Audiveris引擎）")
    logger.info("=" * 60)
    logger.info(f"输入: {input_pdf}")
    logger.info(f"输出: {output_path}")
    logger.info(f"DPI: {dpi}")

    # 步骤1: PDF转图像
    images_dir = os.path.join(temp_dir, "images")
    if os.path.exists(images_dir):
        shutil.rmtree(images_dir)

    # 解析预处理配置
    pipeline_config = None
    if args.use_pipeline and args.pipeline_config:
        import json
        pipeline_config = json.loads(args.pipeline_config)

    image_paths = pdf_to_images(input_pdf, images_dir, dpi=dpi,
                                enhancement_method=enhancement_method,
                                use_pipeline=args.use_pipeline,
                                pipeline_config=pipeline_config)
    logger.info(f"共生成 {len(image_paths)} 页图像")

    # 步骤2: 并行 OMR 识别（多页同时处理，加速6x+）
    xml_files = []
    # 先创建好所有输出目录
    page_dirs = []
    for i in range(len(image_paths)):
        page_dir = os.path.join(temp_dir, f"page_{i+1:03d}")
        os.makedirs(page_dir, exist_ok=True)
        page_dirs.append(page_dir)

    # 多线程并行执行 Audiveris（信号量限流，防止子进程内线程爆炸）
    with _AUDIVERIS_SEMAPHORE, concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_page = {
            executor.submit(run_audiveris, img_path, page_dirs[i]): i
            for i, img_path in enumerate(image_paths)
        }
        for future in concurrent.futures.as_completed(future_to_page):
            page_idx = future_to_page[future]
            try:
                xml_path = future.result()
                if xml_path:
                    xml_files.append(xml_path)
                    logger.info(f"  -> 第{page_idx+1}页识别成功: {xml_path}")
                else:
                    logger.warning(f"  -> 第{page_idx+1}页识别失败")
            except Exception as e:
                logger.warning(f"  -> 第{page_idx+1}页识别异常: {e}")

    # 按页码排序，确保合并顺序正确
    xml_files.sort()

    if not xml_files:
        logger.error("所有页面识别失败")
        sys.exit(1)

    # 步骤3: 合并MusicXML文件 或 逐页输出
    if args.per_page:
        # 逐页识别模式：每页独立输出
        logger.info(f"\n逐页识别模式：输出 {len(xml_files)} 个独立MusicXML文件")
        for i, xml_file in enumerate(xml_files):
            page_output = os.path.join(output_dir, f"{base_name}_page{i+1:03d}.musicxml")
            if xml_file.endswith('.mxl'):
                temp_xml = extract_mxl_to_xml(xml_file, os.path.dirname(xml_file))
                if temp_xml:
                    shutil.copy2(temp_xml, page_output)
                    logger.info(f"  第{i+1}页 -> {page_output}")
            else:
                shutil.copy2(xml_file, page_output)
                logger.info(f"  第{i+1}页 -> {page_output}")
        success = True
        logger.info(f"\n逐页识别完成，共输出 {len(xml_files)} 个文件")
    else:
        # 合并模式
        success = merge_musicxml_files(xml_files, output_path)

    # 清理临时目录
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"已清理临时目录: {temp_dir}")
        except Exception as e:
            logger.warning(f"清理临时目录失败: {temp_dir} ({e})")

    if success:
        # 步骤4: 节奏归一化（可选）
        if args.target_duration and not args.per_page:
            normalize_musicxml_tempo(output_path, args.target_duration)

        logger.info("\n" + "=" * 60)
        logger.info("转换完成!")
        if args.per_page:
            logger.info(f"输出目录: {output_dir}")
            logger.info(f"共 {len(xml_files)} 个MusicXML文件")
        else:
            logger.info(f"输出文件: {output_path}")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("转换失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
