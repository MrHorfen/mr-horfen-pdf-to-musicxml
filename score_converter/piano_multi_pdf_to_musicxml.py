"""
文件: piano_multi_pdf_to_musicxml.py
用途: 钢琴多声部PDF转MusicXML（保留多Part结构 + 多voice）
      适用于：钢琴伴奏+人声、钢琴+其他乐器、同谱表多voice等场景
调用方: converter_service.py（voices=multi 时路由到此脚本）
输入: 原始PDF文件路径
输出: 保留多声部结构的MusicXML文件
依赖: PyMuPDF(fitz), lxml, Audiveris
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-07-10
最后更新: 2026-10-21
更新说明: 合并阶段改为结构安全组装（声部并集 + 缺声部补休止 + 补登记 score-part），
         交付前做结构校验并输出用户可读错误；保留原有"碎片Part并入钢琴staff 2"策略
"""

import os
import sys
import subprocess
import tempfile
import shutil
import zipfile
from pathlib import Path
from copy import deepcopy
import fitz  # PyMuPDF
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def pdf_to_images(pdf_path, output_dir, dpi=300, max_pixels=19_000_000,
                  use_pipeline: bool = False, pipeline_config: dict = None):
    """将PDF转换为PNG图像

    默认使用简单渲染（直接PyMuPDF渲染 + 像素控制），这是经过验证的稳定方案。
    预处理管线仅在 use_pipeline=True 时启用。
    """
    logger.info(f"将PDF转换为图像: {pdf_path}")

    if use_pipeline:
        logger.info(f"使用预处理管线模式 (config={pipeline_config})")
        try:
            score_converter_root = Path(__file__).parent
            if str(score_converter_root) not in sys.path:
                sys.path.insert(0, str(score_converter_root))

            from modules.pdf_preprocessor import quick_preprocess

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

    doc.close()
    return image_paths


def run_audiveris(image_path, output_dir):
    """运行Audiveris OMR识别（多声部模式：不强制twoStaves）"""
    logger.info(f"运行Audiveris识别: {image_path}")

    # 多声部模式：不设置 twoStaves=true，让Audiveris自动检测声部数量
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
            encoding="utf-8",
            errors="ignore",
        )

        if result.returncode != 0:
            logger.error(f"Audiveris执行失败 (code={result.returncode})")
            return None

        for pattern in ["*.xml", "*.musicxml", "*.mxl"]:
            files = list(Path(output_dir).rglob(pattern))
            if files:
                return str(files[0])

        return None

    except Exception as e:
        logger.error(f"Audiveris识别异常: {e}")
        return None


def extract_mxl_to_xml(mxl_path, output_dir):
    """从.mxl压缩文件中提取MusicXML"""
    try:
        with zipfile.ZipFile(mxl_path, 'r') as zf:
            xml_names = [n for n in zf.namelist() if n.endswith('.xml') and not n.startswith('META-INF/')]
            if xml_names:
                xml_content = zf.read(xml_names[0])
                xml_output = os.path.join(output_dir, Path(mxl_path).stem + '.xml')
                with open(xml_output, 'wb') as f:
                    f.write(xml_content)
                return xml_output
    except Exception as e:
        logger.error(f"解压.mxl失败: {e}")
    return None


def _get_part_instrument_name(part_list, part_id):
    """从part-list获取Part的乐器名称"""
    if part_list is None:
        return None
    for sp in part_list.findall('score-part'):
        if sp.get('id') == part_id:
            name_el = sp.find('part-name')
            if name_el is not None and name_el.text:
                return name_el.text.strip()
    return None


def _count_part_notes(part):
    """统计Part中的音符总数"""
    return sum(1 for _ in part.findall('.//note'))


def _get_part_staves(part):
    """获取Part使用的staff编号集合"""
    staves = set()
    for note in part.findall('.//note'):
        staff_el = note.find('staff')
        if staff_el is not None:
            staves.add(int(staff_el.text))
        else:
            staves.add(1)
    return staves


def merge_musicxml_files(xml_files, output_path):
    """合并多个MusicXML文件为一个（多声部模式：保留多Part结构）

    与双声部方案的核心区别：
    1. 不强制合并所有Part为一个
    2. 保留voice编号（不强制voice=1）
    3. 只合并明显是同一乐器碎片化的Part（如Audiveris将钢琴低音部误识别为Tuba）
    4. 保留独立乐器Part（如Vocal、SOLO TUBA等）
    """
    from lxml import etree

    logger.info(f"合并 {len(xml_files)} 个MusicXML文件（多声部模式）")

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

    try:
        roots = [etree.parse(f).getroot() for f in processed_xml_files]
        root = assemble_pages(roots)
    except MusicXMLMergeError as e:
        logger.error(f"乐谱合并结果结构异常，已阻止交付：{e}")
        return False

    # === 多声部Part合并策略 ===
    # 分析所有Part，识别哪些是"碎片化Part"（应合并），哪些是"独立乐器Part"（应保留）
    parts = root.findall('part')
    part_list = root.find('part-list')

    if len(parts) <= 1:
        logger.info("  仅有1个Part，无需合并")
    else:
        # 统计每个Part的信息
        part_info = {}
        for part in parts:
            pid = part.get('id')
            name = _get_part_instrument_name(part_list, pid)
            note_count = _count_part_notes(part)
            staves = _get_part_staves(part)
            part_info[pid] = {
                'name': name,
                'notes': note_count,
                'staves': staves,
                'part': part,
            }
            logger.info(f"  Part {pid}: name='{name}', {note_count}音符, staves={staves}")

        # 判断策略：
        # 1. 如果存在多个Part且其中一个明显是钢琴主体（音符最多 + 双staff），
        #    其他单staff Part可能是碎片化的低音部 → 合并到主体的staff 2
        # 2. 如果存在明显不同的乐器名（如Vocal、SOLO TUBA），保留为独立Part
        # 3. 如果所有Part都是单staff且音符量接近，可能是多乐器 → 全部保留

        # 找钢琴主体Part（音符最多 + 使用staff 1和2）
        piano_main = None
        for pid, info in part_info.items():
            if 1 in info['staves'] and 2 in info['staves']:
                if piano_main is None or info['notes'] > part_info[piano_main]['notes']:
                    piano_main = pid

        if piano_main is not None:
            # 有双staff的钢琴主体，检查其他Part是否需要合并
            main_info = part_info[piano_main]
            logger.info(f"  钢琴主体Part: {piano_main} ({main_info['notes']}音符)")

            for pid, info in part_info.items():
                if pid == piano_main:
                    continue

                name = info['name'] or ''
                # 判断是否是碎片化Part（应合并）还是独立乐器（应保留）
                # 碎片化特征：单staff + 音符数远少于主体 + 名称像误识别（Tuba等）
                is_fragment = (
                    len(info['staves']) == 1
                    and info['notes'] < main_info['notes'] * 0.5
                )

                # 检查名称是否像独立乐器
                independent_keywords = ['vocal', 'voice', 'sing', 'choir', 'solo']
                is_independent = any(kw in name.lower() for kw in independent_keywords)

                if is_fragment and not is_independent:
                    # 合并到钢琴主体的staff 2
                    logger.info(f"  -> 合并碎片Part {pid} ('{name}') 到staff 2")
                    _merge_part_to_staff(root, piano_main, pid, target_staff=2)
                else:
                    # 保留为独立Part
                    logger.info(f"  -> 保留独立Part {pid} ('{name}')")

        else:
            # 没有双staff主体，所有Part都保留（多乐器场景）
            logger.info("  无双staff主体Part，保留所有Part")

        # 清理已合并Part的part-list条目
        remaining_parts = root.findall('.//part')
        remaining_ids = {p.get('id') for p in remaining_parts}
        if part_list is not None:
            for sp in part_list.findall('score-part'):
                if sp.get('id') not in remaining_ids:
                    part_list.remove(sp)

        logger.info(f"  最终保留 {len(remaining_parts)} 个Part")

    try:
        validate_merged(root, title="乐谱合并结果")
    except MusicXMLMergeError as e:
        logger.error(f"乐谱合并结果结构异常，已阻止交付：{e}")
        return False

    root.getroottree().write(output_path, encoding="utf-8", xml_declaration=True)
    logger.info(f"合并完成: {output_path}")
    return True


def _merge_part_to_staff(root, target_part_id, source_part_id, target_staff=2):
    """将source Part的音符合并到target Part的指定staff"""
    from lxml import etree

    target_part = root.find(f".//part[@id='{target_part_id}']")
    source_part = root.find(f".//part[@id='{source_part_id}']")

    if target_part is None or source_part is None:
        return

    added = 0
    for m in source_part.findall('measure'):
        m_num = m.get('number')
        notes = m.findall('note')
        if not notes:
            continue

        target = None
        for bm in target_part.findall('measure'):
            if bm.get('number') == m_num:
                target = bm
                break
        if target is None:
            continue

        for note in notes:
            staff_el = note.find('staff')
            if staff_el is not None:
                staff_el.text = str(target_staff)
            else:
                _s = etree.SubElement(note, 'staff')
                _s.text = str(target_staff)
            target.append(deepcopy(note))
            added += 1

    # 删除source Part
    root.remove(source_part)
    logger.info(f"  从Part {source_part_id}合并了{added}个音符到staff {target_staff}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="钢琴多声部PDF转MusicXML（Audiveris引擎）")
    parser.add_argument("--input", "-i", required=True, help="输入PDF路径")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--output-name", type=str, default=None,
                        help="输出文件名（不含路径），默认使用输入PDF的stem")
    parser.add_argument("--dpi", type=int, default=300, help="渲染DPI（默认300）")
    parser.add_argument("--instrument", default="piano", help="乐器类型（默认piano）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--use-pipeline", action="store_true", help="启用预处理管线")
    parser.add_argument("--pipeline-config", type=str, default=None,
                        help="预处理配置（JSON字符串）")
    parser.add_argument("--per-page", action="store_true",
                        help="逐页识别模式：每页独立输出 MusicXML 文件（不合并）")
    args = parser.parse_args()

    input_pdf = args.input
    output_dir = args.output
    dpi = args.dpi

    # 输出文件名：优先使用 --output-name，否则用输入PDF的stem
    if args.output_name:
        base_name = Path(args.output_name).stem
    else:
        base_name = Path(input_pdf).stem
    output_path = os.path.join(output_dir, f"{base_name}.musicxml")

    # 临时目录（使用系统临时目录，不污染输出目录）
    temp_dir = os.path.join(tempfile.gettempdir(), f"audiveris_multi_{base_name}_{os.getpid()}")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    logger.info("=" * 60)
    logger.info("钢琴多声部PDF转MusicXML转换（Audiveris引擎）")
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
                                use_pipeline=args.use_pipeline,
                                pipeline_config=pipeline_config)
    logger.info(f"共生成 {len(image_paths)} 页图像")

    # 步骤2: 逐页OMR识别
    xml_files = []
    for i, img_path in enumerate(image_paths):
        logger.info(f"\n处理第 {i+1}/{len(image_paths)} 页")

        page_output_dir = os.path.join(temp_dir, f"page_{i+1:03d}")
        os.makedirs(page_output_dir, exist_ok=True)

        xml_path = run_audiveris(img_path, page_output_dir)

        if xml_path:
            xml_files.append(xml_path)
            logger.info(f"  -> 识别成功: {xml_path}")
        else:
            logger.warning(f"  -> 识别失败，跳过第 {i+1} 页")

    if not xml_files:
        logger.error("所有页面识别失败")
        sys.exit(1)

    # 步骤3: 合并MusicXML 或 逐页独立输出（多声部模式）
    if args.per_page:
        # 逐页识别模式：每页独立输出，文件名与单声部脚本一致（converter_service 通用收集）
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
        success = merge_musicxml_files(xml_files, output_path)

    # 清理临时目录
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"已清理临时目录: {temp_dir}")
        except Exception as e:
            logger.warning(f"清理临时目录失败: {temp_dir} ({e})")

    if success:
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
