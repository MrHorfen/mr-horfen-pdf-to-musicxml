"""
文件: ensemble_pdf_to_musicxml.py
用途: 合奏/乐队总谱PDF转MusicXML（标签分割方案），与单乐器方案完全隔离独立运行
调用方: 命令行 / 手动执行
输入: 合奏总谱 PDF
输出: 分乐器 MusicXML 文件
依赖: PyMuPDF(fitz), xml.etree, pathlib
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import sys
import re
import json
import logging
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import fitz  # PyMuPDF

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent

# ============================================================
# 乐器名称标准化映射
# 第一页用全名，后续页用缩写，统一映射到标准名
# ============================================================
INSTRUMENT_NAME_MAP = {
    # 打击乐
    "click": "click",
    "cymbal": "cymbal",
    "tt": "TT",
    "hh": "HH",
    "sd": "SD",
    "bd": "BD",
    "drmkt": "drumkit",  # 后续页6个打击乐统一标为drmkt
    # 旋律乐器
    "bass": "bass",
    "el.bs.": "bass",
    "el.bs": "bass",
    "strings": "strings",
    "vln.": "strings",
    "vln": "strings",
    "organ": "organ",
    "org.": "organ",
    "org": "organ",
    "distortion guitar": "dist_guitar",
    "dist.guit.": "dist_guitar",
    "dist.guit": "dist_guitar",
}

# 打击乐组（drmkt 标签下包含的实际乐器）
DRUMKIT_PARTS = ["click", "cymbal", "TT", "HH", "SD", "BD"]

# 打击乐名称集合（第一页用全名，不区分大小写）
DRUM_NAME_SET = {"click", "cymbal", "tt", "hh", "sd", "bd", "drumkit", "drmkt"}

def _is_drum(name: str) -> bool:
    """判断是否为打击乐声部（不区分大小写）"""
    return name.lower() in DRUM_NAME_SET

# 非打击乐乐器（旋律乐器）
MELODIC_PARTS = ["bass", "strings", "organ", "dist_guitar"]


def _clean_text(text: str) -> str:
    """清理文本，去除特殊字符"""
    text = text.split("\n")[0]  # 只取第一行
    text = re.sub(r"[^\x20-\x7E]", "", text)  # 去除非ASCII
    return text.strip().lower()


def detect_instrument_labels(page: fitz.Page) -> List[Dict]:
    """
    从页面左侧提取乐器名称标签

    标签特征：x < 200, 包含英文字母, 在页面左侧

    Returns:
        [{"y": float, "name": str, "standard_name": str}, ...]
        按Y坐标排序
    """
    blocks = page.get_text("blocks")
    labels = []

    for b in blocks:
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        text = b[4] if len(b) > 4 else ""

        # 只看左侧文本
        if x0 > 200:
            continue

        raw = text.strip()
        if not raw or len(raw) < 2:
            continue

        # 必须包含英文字母
        if not any(c.isascii() and c.isalpha() for c in raw.split("\n")[0]):
            continue

        clean = _clean_text(raw)
        standard = INSTRUMENT_NAME_MAP.get(clean, clean)

        labels.append({
            "y": y0,
            "y_bottom": y1,
            "name": raw.split("\n")[0][:40],
            "standard_name": standard,
        })

    # 按Y排序
    labels.sort(key=lambda l: l["y"])
    return labels


def get_instrument_layout(doc: fitz.Document) -> List[Dict]:
    """
    分析整个PDF的乐器布局

    用第一页获取完整布局（全名），后续页验证一致性。

    区域划分策略：使用相邻标签的中点作为边界，而不是直接使用下一个标签的Y。
    这样每个乐器获得标签前后各半的区域，确保完整包含五线谱。

    Returns:
        [{standard_name, y_start, y_end, is_drum}, ...]
    """
    page0 = doc[0]
    labels = detect_instrument_labels(page0)

    if not labels:
        raise RuntimeError("未检测到乐器标签，PDF可能不是合奏总谱格式")

    logger.info(f"检测到 {len(labels)} 个乐器标签:")
    for l in labels:
        logger.info(f"  y={l['y']:.0f}  {l['name']}  →  {l['standard_name']}")

    page_height = page0.rect.height
    instruments = []

    # 用于计算中点边界的Y坐标列表
    label_ys = [l["y"] for l in labels]

    for i, label in enumerate(labels):
        # 上边界：当前标签和前一个标签的中点（或页面顶部）
        if i == 0:
            y_start = 0
        else:
            y_start = (label_ys[i - 1] + label_ys[i]) / 2

        # 下边界：当前标签和后一个标签的中点（或页面底部）
        if i + 1 < len(labels):
            y_end = (label_ys[i] + label_ys[i + 1]) / 2
        else:
            y_end = page_height

        is_drumkit = label["standard_name"] == "drumkit"

        instruments.append({
            "standard_name": label["standard_name"],
            "y_start": y_start,
            "y_end": y_end,
            "is_drumkit": is_drumkit,
            "page0_label": label["name"],
        })

    # 展开drmkt为6个独立打击乐声部
    expanded = []
    for inst in instruments:
        is_drum = _is_drum(inst["standard_name"])

        if inst["is_drumkit"]:
            drum_h = (inst["y_end"] - inst["y_start"]) / 6
            for j, part_name in enumerate(DRUMKIT_PARTS):
                expanded.append({
                    "standard_name": part_name,
                    "y_start": inst["y_start"] + j * drum_h,
                    "y_end": inst["y_start"] + (j + 1) * drum_h,
                    "is_drum": True,
                })
        elif is_drum:
            expanded.append({
                "standard_name": inst["standard_name"],
                "y_start": inst["y_start"],
                "y_end": inst["y_end"],
                "is_drum": True,
            })
        else:
            expanded.append({
                "standard_name": inst["standard_name"],
                "y_start": inst["y_start"],
                "y_end": inst["y_end"],
                "is_drum": False,
            })

    logger.info(f"展开后 {len(expanded)} 个声部:")
    for inst in expanded:
        logger.info(f"  {inst['standard_name']}: y={inst['y_start']:.0f}-{inst['y_end']:.0f} {'(打击乐)' if inst['is_drum'] else ''}")

    return expanded


def split_pdf_by_instruments(
    pdf_path: str,
    instruments: List[Dict],
    output_dir: str,
) -> Dict[str, str]:
    """
    将PDF按乐器切割为独立PDF

    使用遮罩方案：渲染整页PNG，对非目标乐器区域添加白色遮罩，
    保留完整页面尺寸让Audiveris有足够上下文识别五线谱。

    Args:
        pdf_path: 输入PDF
        instruments: 乐器布局（从get_instrument_layout获取）
        output_dir: 输出目录

    Returns:
        {standard_name: pdf_path}
    """
    import cv2
    import numpy as np

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"遮罩切割PDF: {total_pages} 页 → {len(instruments)} 个乐器")

    dpi = 150  # 降低DPI加速，遮罩方案不需要高精度
    instrument_pdfs = {}

    for inst in instruments:
        name = inst["standard_name"]
        out_doc = fitz.open()

        # 遮罩上下边界（保留10px过渡带，避免切断谱线）
        mask_top = inst["y_start"] - 10
        mask_bottom = inst["y_end"] + 10

        for page_num in range(total_pages):
            src_page = doc[page_num]
            src_rect = src_page.rect

            # 渲染整页为灰度PNG
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = src_page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)

            # 缩放遮罩边界到渲染坐标
            scale = dpi / 72
            y1_scaled = int(mask_top * scale)
            y2_scaled = int(mask_bottom * scale)

            # 遮罩：非目标区域设为白色(255)
            masked = img.copy()
            if y1_scaled > 0:
                masked[:y1_scaled, :] = 255
            if y2_scaled < masked.shape[0]:
                masked[y2_scaled:, :] = 255

            # 保存为临时PNG
            temp_png = output_dir / f"_temp_{name}_page{page_num:03d}.png"
            cv2.imwrite(str(temp_png), masked)

            # 创建PDF页面（保持原始尺寸）
            new_page = out_doc.new_page(
                width=src_rect.width,
                height=src_rect.height,
            )
            new_page.insert_image(new_page.rect, filename=str(temp_png))

            # 清理临时PNG
            temp_png.unlink()

        out_path = output_dir / f"{name}.pdf"
        out_doc.save(str(out_path))
        out_doc.close()
        instrument_pdfs[name] = str(out_path)
        logger.info(f"  {name}: {out_path} ({total_pages} 页)")

    doc.close()
    return instrument_pdfs


def run_audiveris_on_pdf(pdf_path: str, output_dir: str) -> Optional[str]:
    """
    对单个乐器PDF运行Audiveris OMR

    复用 piano_pdf_to_musicxml.py 的成熟方案。
    """
    logger.info(f"  Audiveris识别: {pdf_path}")

    script = PROJECT_ROOT / "piano_pdf_to_musicxml.py"
    if not script.exists():
        logger.error(f"  转谱脚本不存在: {script}")
        return None

    base_name = Path(pdf_path).stem
    out_musicxml = Path(output_dir) / f"{base_name}.musicxml"

    cmd = [
        sys.executable,
        str(script),
        "--input", pdf_path,
        "--output", output_dir,
        "--dpi", "300",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )

        if result.returncode != 0:
            logger.error(f"  Audiveris失败 (code={result.returncode})")
            if result.stderr:
                logger.error(f"  stderr: {result.stderr[-500:]}")
            return None

        if out_musicxml.exists():
            return str(out_musicxml)
        else:
            logger.error(f"  未找到输出: {out_musicxml}")
            return None

    except subprocess.TimeoutExpired:
        logger.error(f"  Audiveris超时: {pdf_path}")
        return None
    except Exception as e:
        logger.error(f"  Audiveris异常: {e}")
        return None


def merge_ensemble_musicxml(
    musicxml_files: Dict[str, str],
    output_path: str,
    title: str = "Ensemble Score",
) -> bool:
    """
    合并多个乐器MusicXML为多声部合奏谱

    Args:
        musicxml_files: {instrument_name: musicxml_path}
        output_path: 输出路径
        title: 曲目标题

    Returns:
        是否成功
    """
    from lxml import etree

    logger.info(f"合并 {len(musicxml_files)} 个乐器MusicXML")

    if not musicxml_files:
        logger.error("没有可合并的MusicXML文件")
        return False

    # 构建MusicXML框架
    root = etree.Element("score-partwise", version="4.0")
    root.append(etree.Element("movement-title"))
    root.find("movement-title").text = title

    # part-list
    part_list = etree.SubElement(root, "part-list")

    # 为每个乐器创建score-part
    part_ids = {}
    for idx, (inst_name, xml_path) in enumerate(sorted(musicxml_files.items())):
        pid = f"P{idx + 1}"
        part_ids[inst_name] = pid

        score_part = etree.SubElement(part_list, "score-part", id=pid)
        part_name = etree.SubElement(score_part, "part-name")
        part_name.text = inst_name.replace("_", " ").title()

    # 合并各乐器的measure
    for inst_name, xml_path in sorted(musicxml_files.items()):
        pid = part_ids[inst_name]

        try:
            tree = etree.parse(xml_path)
            other_root = tree.getroot()

            # 查找同名part
            other_parts = other_root.findall(".//part")
            if not other_parts:
                logger.warning(f"  {inst_name}: 无part数据")
                continue

            # 创建新part
            part = etree.SubElement(root, "part", id=pid)

            # 复制所有measure
            for other_part in other_parts[:1]:  # 取第一个part
                for measure in other_part.findall("measure"):
                    # 深拷贝measure及其子元素
                    part.append(measure)

            logger.info(f"  + {inst_name} ({pid})")

        except Exception as e:
            logger.error(f"  {inst_name} 合并失败: {e}")

    # 写入
    tree = etree.ElementTree(root)
    tree.write(output_path, encoding="utf-8", xml_declaration=True, pretty_print=True)
    logger.info(f"合并完成: {output_path}")

    return True


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="合奏/乐队总谱PDF转MusicXML（多声部独立识别方案）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ensemble_pdf_to_musicxml.py --input band_score.pdf -o ./output
  python ensemble_pdf_to_musicxml.py --input band_score.pdf -o ./output --title "Song Name"
  python ensemble_pdf_to_musicxml.py --input band_score.pdf -o ./output --skip-drums
        """,
    )
    parser.add_argument("--input", "-i", required=True, help="输入PDF路径")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--title", default=None, help="曲目标题")
    parser.add_argument("--skip-drums", action="store_true", help="跳过打击乐声部")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    args = parser.parse_args()

    input_pdf = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.title is None:
        args.title = input_pdf.stem

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info("合奏/乐队总谱PDF转MusicXML（多声部独立识别）")
    logger.info("=" * 60)
    logger.info(f"输入: {input_pdf}")
    logger.info(f"输出: {output_dir}")
    logger.info(f"标题: {args.title}")

    # ========== 阶段1：检测乐器布局 ==========
    logger.info("\n[1/4] 检测乐器布局...")
    doc = fitz.open(str(input_pdf))
    instruments = get_instrument_layout(doc)
    total_pages = len(doc)
    doc.close()

    if args.skip_drums:
        instruments = [i for i in instruments if not i["is_drum"]]
        logger.info(f"跳过打击乐，剩余 {len(instruments)} 个旋律声部")

    # ========== 阶段2：按乐器切割PDF ==========
    logger.info("\n[2/4] 按乐器切割PDF...")
    temp_dir = output_dir / "_ensemble_temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir()

    instrument_pdfs = split_pdf_by_instruments(
        str(input_pdf), instruments, str(temp_dir)
    )

    # ========== 阶段3：逐乐器OMR识别 ==========
    logger.info(f"\n[3/4] 逐乐器OMR识别 ({len(instrument_pdfs)} 个)...")
    omr_output_dir = output_dir / "_omr_output"
    omr_output_dir.mkdir(exist_ok=True)

    musicxml_files = {}
    for inst_name, pdf_path in instrument_pdfs.items():
        logger.info(f"\n--- {inst_name} ---")
        xml_path = run_audiveris_on_pdf(pdf_path, str(omr_output_dir))
        if xml_path:
            musicxml_files[inst_name] = xml_path
            logger.info(f"  ✅ {inst_name}: {xml_path}")
        else:
            logger.warning(f"  ❌ {inst_name}: 识别失败")

    if not musicxml_files:
        logger.error("所有乐器识别失败")
        sys.exit(1)

    # ========== 阶段4：合并多声部MusicXML ==========
    logger.info(f"\n[4/4] 合并 {len(musicxml_files)} 个声部MusicXML...")
    output_path = output_dir / f"{args.title}.musicxml"
    success = merge_ensemble_musicxml(musicxml_files, str(output_path), args.title)

    # 清理临时文件
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    if success:
        logger.info("\n" + "=" * 60)
        logger.info("合奏谱转谱完成!")
        logger.info(f"输出: {output_path}")
        logger.info(f"声部数: {len(musicxml_files)}")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("转谱失败")
        sys.exit(1)


if __name__ == "__main__":
    main()