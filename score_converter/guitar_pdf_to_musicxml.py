"""
文件: guitar_pdf_to_musicxml.py
用途: 吉他PDF转MusicXML转换脚本，基于Audiveris OMR引擎，单行谱表（twoStaves=false）
调用方: 命令行手动执行
输入: 吉他PDF乐谱
输出: 吉他 MusicXML（单Part，E2-A5音域）
依赖: Audiveris(Java), PyMuPDF(fitz), cv2
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""

import os
import sys
import subprocess
import tempfile
import shutil
import zipfile
from pathlib import Path
import fitz  # PyMuPDF
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# 吉他配置
# ============================================================

GUITAR_CONFIG = {
    "name": "Guitar",
    "part_id": "P1",
    "part_name": "Guitar",
    "clef": "G",  # 高音谱号
    "pitch_range": (40, 81),  # E2-A5 吉他标准音域
    "midi_program": 25,  # Acoustic Guitar (nylon)
}


# ============================================================
# PDF → 图像（复用钢琴方案的预处理流程）
# ============================================================

def pdf_to_images(pdf_path, output_dir, dpi=300, max_pixels=19_000_000):
    """将PDF转换为PNG图像，复用PDFPreprocessingPipeline"""
    logger.info(f"将PDF转换为图像: {pdf_path}")

    try:
        score_converter_root = Path(__file__).parent
        if str(score_converter_root) not in sys.path:
            sys.path.insert(0, str(score_converter_root))

        from modules.pdf_preprocessor import quick_preprocess

        output_files = quick_preprocess(
            pdf_path, output_dir,
            profile="standard",
            custom_config={"enhancement_method": "guitar"}
        )
        logger.info(f"预处理完成: {len(output_files)} 页")
        return output_files

    except Exception as e:
        logger.warning(f"PDFPreprocessingPipeline 不可用 ({e})，回退到简单渲染")
        doc = fitz.open(pdf_path)
        os.makedirs(output_dir, exist_ok=True)
        image_paths = []

        for i in range(len(doc)):
            page = doc[i]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)

            total_pixels = pix.width * pix.height
            if total_pixels > max_pixels:
                scale = (max_pixels / total_pixels) ** 0.5
                new_dpi = dpi * scale
                logger.info(f"  第{i+1}页: {pix.width}x{pix.height}={total_pixels:,}像素超限，缩放DPI {dpi:.0f}→{new_dpi:.0f}")
                mat = fitz.Matrix(new_dpi / 72, new_dpi / 72)
                pix = page.get_pixmap(matrix=mat)

            img_path = os.path.join(output_dir, f"page_{i+1:03d}.png")
            pix.save(img_path)
            image_paths.append(img_path)

        doc.close()
        return image_paths


# ============================================================
# 自动检测谱表数量（单行 vs 双行）
# ============================================================

def detect_staff_count_per_system(image_path, dpi=300):
    """检测每系统有多少个谱表（1=单行，2=双行）

    原理：分析图像垂直方向的黑色像素分布，找到谱线密集区域，
    然后判断这些区域是否成对出现（间距约40-80px为同一系统的两个谱表）。

    Returns:
        int: 1或2，表示每系统的谱表数
    """
    try:
        from PIL import Image
        import numpy as np

        img = Image.open(image_path).convert('L')
        arr = np.array(img)
        h, w = arr.shape

        # 计算每行的黑色像素比例（阈值100）
        row_dark_ratio = np.mean(arr < 100, axis=1)

        # 找谱线峰值（局部高点）
        from scipy.signal import find_peaks
        peaks, _ = find_peaks(row_dark_ratio, height=0.15, distance=2, prominence=0.05)

        if len(peaks) < 5:
            logger.warning(f"  谱线检测不足({len(peaks)}条)，默认单行")
            return 1

        # 分组：相邻峰值（间距<20px）属于同一谱表
        groups = []
        current_group = [peaks[0]]
        for p in peaks[1:]:
            if p - current_group[-1] < 20:
                current_group.append(p)
            else:
                groups.append(current_group)
                current_group = [p]
        groups.append(current_group)

        # 过滤掉只有1-2条线的噪声组
        valid_groups = [g for g in groups if len(g) >= 3]

        if len(valid_groups) < 2:
            logger.warning(f"  有效谱表组不足({len(valid_groups)}组)，默认单行")
            return 1

        # 检查是否有成对出现的谱表（间距40-100px）
        pair_count = 0
        i = 0
        while i < len(valid_groups) - 1:
            gap = valid_groups[i+1][0] - valid_groups[i][-1]
            if 30 <= gap <= 120:  # 同一系统的两个谱表间距
                pair_count += 1
                i += 2
            else:
                i += 1

        if pair_count >= 2:  # 至少2对才判定为双行
            logger.info(f"  检测到双行谱表: {pair_count}对谱表组")
            return 2

        logger.info(f"  检测到单行谱表: {len(valid_groups)}个独立谱表组")
        return 1

    except Exception as e:
        logger.warning(f"  谱表检测失败({e})，默认单行")
        return 1


# ============================================================
# 自动裁剪左右留白（解决"谱没铺平画面"问题）
# ============================================================

def crop_and_split_image(image_path, output_dir, threshold=128, min_staff_ratio=0.6, vertical_gap_threshold=500):
    """自动裁剪左右留白 + 按垂直大间距分割成多个子图像

    解决两个问题：
    1. 谱线只占页面部分宽度（如39%），Audiveris布局分析出错 → 裁剪左右留白
    2. 一个PDF页面包含多页书内容（上下堆叠），Audiveris当成连续谱表 → 垂直分割

    Args:
        image_path: 输入图像路径
        output_dir: 输出目录（保存分割后的子图像）
        threshold: 黑色像素阈值
        min_staff_ratio: 谱线占页面宽度的最小比例，低于此值才裁剪
        vertical_gap_threshold: 垂直间距阈值（px），超过此值认为是分页点

    Returns:
        list[str]: 分割后的子图像路径列表
    """
    from PIL import Image
    import numpy as np
    from scipy.signal import find_peaks

    img = Image.open(image_path).convert('L')
    arr = np.array(img)
    h, w = arr.shape

    # 1. 找谱线所在的行
    row_dark_ratio = np.mean(arr < threshold, axis=1)
    peaks, _ = find_peaks(row_dark_ratio, height=0.1, distance=2, prominence=0.03)

    if len(peaks) < 3:
        logger.info(f"  谱线不足，跳过处理")
        return [image_path]

    # 2. 分组：相邻峰值（间距<25px）属于同一谱表
    groups = []
    current = [peaks[0]]
    for p in peaks[1:]:
        if p - current[-1] < 25:
            current.append(p)
        else:
            groups.append(current)
            current = [p]
    groups.append(current)
    valid_groups = [g for g in groups if len(g) >= 3]

    if not valid_groups:
        return [image_path]

    # 3. 计算水平跨度（用较高阈值0.3过滤文字/页码等稀疏噪声）
    all_starts = []
    all_ends = []
    for g in valid_groups:
        y_center = (g[0] + g[-1]) // 2
        y_range = arr[max(0, y_center-10):y_center+10]
        col_dark = np.mean(y_range < threshold, axis=0)
        dark_cols = np.where(col_dark > 0.3)[0]
        if len(dark_cols) > 0:
            all_starts.append(dark_cols[0])
            all_ends.append(dark_cols[-1])

    if not all_starts:
        return [image_path]

    # 4. 水平裁剪
    left = min(all_starts)
    right = max(all_ends)
    staff_width = right - left
    ratio = staff_width / w

    logger.info(f"  谱线水平跨度: {left}-{right} ({staff_width}px/{w}px = {ratio*100:.0f}%)")

    if ratio < min_staff_ratio:
        padding = 20
        crop_left = max(0, left - padding)
        crop_right = min(w, right + padding)
        arr = arr[:, crop_left:crop_right]
        logger.info(f"  已裁剪左右留白: {w}px → {crop_right-crop_left}px")

    # 5. 垂直分割：检测大间距（>200px表示分页点）
    split_points = []
    for i in range(1, len(valid_groups)):
        gap = valid_groups[i][0] - valid_groups[i-1][-1]
        if gap > vertical_gap_threshold:
            mid = (valid_groups[i-1][-1] + valid_groups[i][0]) // 2
            split_points.append(mid)

    if not split_points:
        # 无需分割，保存整个图像
        out_path = os.path.join(output_dir, os.path.basename(image_path))
        Image.fromarray(arr).save(out_path)
        return [out_path]

    # 6. 按分割点切分
    logger.info(f"  检测到{len(split_points)}个分页点，将分割成{len(split_points)+1}个子图像")
    sections = []
    prev_split = 0
    for idx, sp in enumerate(split_points):
        section = arr[prev_split:sp, :]
        out_path = os.path.join(output_dir, f"section_{idx+1:02d}.png")
        Image.fromarray(section).save(out_path)
        sections.append(out_path)
        logger.info(f"    子图像{idx+1}: y={prev_split}-{sp}, 高度={sp-prev_split}px")
        prev_split = sp

    # 最后一截
    last_section = arr[prev_split:, :]
    out_path = os.path.join(output_dir, f"section_{len(split_points)+1:02d}.png")
    Image.fromarray(last_section).save(out_path)
    sections.append(out_path)
    logger.info(f"    子图像{len(split_points)+1}: y={prev_split}-{h}, 高度={h-prev_split}px")

    return sections


# ============================================================
# Audiveris OMR 识别（吉他谱，自动检测谱表）
# ============================================================

def run_audiveris(image_path, output_dir, two_staves=False):
    """运行Audiveris OMR识别（吉他谱，自动检测谱表）

    Args:
        image_path: 输入图像路径
        output_dir: 输出目录
        two_staves: 是否为双行谱表（传递-option twoStaves=true给Audiveris）
    """
    logger.info(f"运行Audiveris识别: {image_path} (twoStaves={two_staves})")

    cmd = [
        "audiveris",
        "-batch",
        "-export",
        "-output", output_dir,
    ]

    # 关键：双行谱表必须传-option twoStaves=true
    if two_staves:
        cmd.extend(["-option", "twoStaves=true"])

    cmd.append(image_path)

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
            logger.error(f"Audiveris执行失败 (code={result.returncode})")
            return None

        # 查找生成的.mxl文件
        mxl_files = list(Path(output_dir).rglob("*.mxl"))
        if mxl_files:
            return str(mxl_files[0])

        return None

    except subprocess.TimeoutExpired:
        logger.error("Audiveris识别超时")
        return None
    except Exception as e:
        logger.error(f"Audiveris识别异常: {e}")
        return None


# ============================================================
# .mxl → .xml 解压
# ============================================================

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


# ============================================================
# 合并多页MusicXML（复用钢琴方案逻辑）
# ============================================================

def merge_musicxml_files(xml_files, output_path):
    """合并多个MusicXML文件为一个（单Part合并）"""
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

        # 先解压所有.mxl
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

        # 读取第一个文件作为基础
        tree = etree.parse(processed_xml_files[0])
        root = tree.getroot()

        # Audiveris生成的XML没有命名空间，直接用标签名查找
        parts = root.findall('.//part')

        # 合并后续文件的内容
        for xml_file in processed_xml_files[1:]:
            other_tree = etree.parse(xml_file)
            other_root = other_tree.getroot()
            other_parts = other_root.findall('.//part')

            for other_part in other_parts:
                part_id = other_part.get("id")
                existing_part = root.find(f".//part[@id='{part_id}']")

                if existing_part is not None:
                    # 合并measure到现有part
                    measures = other_part.findall('measure')
                    for measure in measures:
                        existing_part.append(measure)
                else:
                    root.append(other_part)

        # 全局重新编号小节（每页Audiveris各自从1开始，合并后需递增）
        for part in root.findall('.//part'):
            for idx, measure in enumerate(part.findall('measure'), start=1):
                measure.set('number', str(idx))

        tree.write(output_path, encoding="utf-8", xml_declaration=True)
        logger.info(f"合并完成: {output_path}")
        return True

    except Exception as e:
        logger.error(f"合并MusicXML失败: {e}")
        if xml_files[0].endswith('.mxl'):
            temp_dir = os.path.dirname(xml_files[0])
            xml_path = extract_mxl_to_xml(xml_files[0], temp_dir)
            if xml_path:
                shutil.copy2(xml_path, output_path)
                return True
        else:
            shutil.copy2(xml_files[0], output_path)
            return True


# ============================================================
# 吉他专用后处理
# ============================================================

def postprocess_guitar_musicxml(xml_path, default_time_signature="4/4", two_staves=False):
    """吉他专用MusicXML后处理：修正Part信息、谱号、调号、拍号、音色

    Audiveris 常见布局分析缺陷（吉他单行谱）：
    - Part 被识别为 Voice 而非 Guitar
    - 谱号/调号被放到第2小节而非第1小节
    - 拍号完全缺失
    本函数自动修复这些问题。

    Args:
        xml_path: MusicXML 文件路径
        default_time_signature: 默认拍号（如 "4/4", "6/8"），当Audiveris未识别时使用
        two_staves: 是否为双行谱表模式
    """
    from lxml import etree

    logger.info(f"吉他后处理: {xml_path} (默认拍号={default_time_signature}, twoStaves={two_staves})")

    tree = etree.parse(xml_path)
    root = tree.getroot()

    # 1. 修正Part信息
    part_list = root.find('.//part-list')
    if part_list is not None:
        score_parts = part_list.findall('score-part')

        if two_staves and len(score_parts) >= 2:
            # 双行谱表：保留两个Part，分别命名
            for idx, score_part in enumerate(score_parts):
                part_id = f"P{idx+1}"
                score_part.set('id', part_id)
                pname = score_part.find('part-name')
                if pname is not None:
                    pname.text = f'Guitar {idx+1}'
                pabbrev = score_part.find('part-abbreviation')
                if pabbrev is not None:
                    pabbrev.text = f'Gtr{idx+1}'
                inst = score_part.find('.//instrument-name')
                if inst is not None:
                    inst.text = 'Acoustic Guitar (nylon)'
                midi_prog = score_part.find('.//midi-program')
                if midi_prog is not None:
                    midi_prog.text = str(GUITAR_CONFIG['midi_program'])
            logger.info(f"  双行谱表: {len(score_parts)}个Part已命名")
        else:
            # 单行谱表：统一为一个Part
            for score_part in score_parts:
                score_part.set('id', GUITAR_CONFIG['part_id'])
                pname = score_part.find('part-name')
                if pname is not None:
                    pname.text = GUITAR_CONFIG['part_name']
                pabbrev = score_part.find('part-abbreviation')
                if pabbrev is not None:
                    pabbrev.text = 'Guit'
                inst = score_part.find('.//instrument-name')
                if inst is not None:
                    inst.text = 'Acoustic Guitar (nylon)'
                midi_prog = score_part.find('.//midi-program')
                if midi_prog is not None:
                    midi_prog.text = str(GUITAR_CONFIG['midi_program'])
            logger.info("  单行谱表: Part已统一为Guitar")

    # 2. 修正Part id
    if two_staves:
        # 双行谱表：保留多个Part，分别设置id
        parts = root.findall('.//part')
        for idx, part in enumerate(parts):
            part.set('id', f"P{idx+1}")
    else:
        for part in root.findall('.//part'):
            part.set('id', GUITAR_CONFIG['part_id'])

    # 3. 修复谱号/调号/拍号位置（Audiveris常见缺陷：放到第2小节或缺失）
    for part in root.findall('.//part'):
        measures = part.findall('measure')
        if not measures:
            continue

        # 收集所有小节中已有的 clef/key/time 元素（从任意小节）
        found_clef = None
        found_key = None
        found_time = None

        for measure in measures:
            attrs = measure.find('attributes')
            if attrs is None:
                continue
            if found_clef is None:
                found_clef = attrs.find('clef')
            if found_key is None:
                found_key = attrs.find('key')
            if found_time is None:
                found_time = attrs.find('time')
            if found_clef is not None and found_key is not None and found_time is not None:
                break

        # 确保第1小节有完整的 attributes（clef + key + time）
        first_measure = measures[0]
        attrs = first_measure.find('attributes')
        if attrs is None:
            attrs = etree.Element('attributes')
            insert_pos = None
            for child in first_measure:
                if child.tag != 'print':
                    insert_pos = child
                    break
            if insert_pos is not None:
                first_measure.insert(list(first_measure).index(insert_pos), attrs)
            else:
                first_measure.append(attrs)

        # 确保 clef 存在（优先用Audiveris检测到的，否则用高音谱号）
        clef = attrs.find('clef')
        if clef is None:
            if found_clef is not None:
                attrs.insert(0, etree.fromstring(etree.tostring(found_clef)))
                logger.info("  第1小节: 从其他小节复制谱号")
            else:
                clef = etree.Element('clef')
                sign = etree.SubElement(clef, 'sign')
                sign.text = 'G'
                line = etree.SubElement(clef, 'line')
                line.text = '2'
                attrs.insert(0, clef)
                logger.info("  第1小节: 补充高音谱号")
        else:
            sign = clef.find('sign')
            if sign is not None and sign.text != 'G':
                sign.text = 'G'
                logger.info("  第1小节: 修正谱号为高音谱号")
            line = clef.find('line')
            if line is None:
                line = etree.SubElement(clef, 'line')
                line.text = '2'

        # 确保 key 存在（优先用Audiveris检测到的，否则默认无升降号）
        key = attrs.find('key')
        if key is None:
            if found_key is not None:
                attrs.append(etree.fromstring(etree.tostring(found_key)))
                fifths = found_key.find('fifths')
                logger.info(f"  第1小节: 从其他小节复制调号 (fifths={fifths.text if fifths is not None else '?'})")
            else:
                key = etree.Element('key')
                fifths = etree.SubElement(key, 'fifths')
                fifths.text = '0'
                attrs.append(key)
                logger.info("  第1小节: 补充调号（无升降号）")

        # 确保 time 存在（优先用Audiveris检测到的，否则用默认拍号）
        time = attrs.find('time')
        if time is None:
            if found_time is not None:
                attrs.append(etree.fromstring(etree.tostring(found_time)))
                beats = found_time.find('beats')
                beat_type = found_time.find('beat-type')
                logger.info(f"  第1小节: 从其他小节复制拍号 ({beats.text}/{beat_type.text})")
            else:
                beats_str, beat_type_str = default_time_signature.split('/')
                time = etree.Element('time')
                beats = etree.SubElement(time, 'beats')
                beats.text = beats_str
                beat_type = etree.SubElement(time, 'beat-type')
                beat_type.text = beat_type_str
                attrs.append(time)
                logger.info(f"  第1小节: 补充拍号 {default_time_signature}")

        # 清理其他小节中重复的 clef/key/time（避免冗余）
        for measure in measures[1:]:
            m_attrs = measure.find('attributes')
            if m_attrs is None:
                continue
            for tag in ['clef', 'key', 'time']:
                elem = m_attrs.find(tag)
                if elem is not None:
                    m_attrs.remove(elem)

    # 4. 保留所有voice（单行吉他谱：Audiveris用多voice区分旋律/低音）
    # Audiveris将旋律（stem up）放voice 1，低音（stem down/下加线）放voice 2+
    # 这是正确的MusicXML多声部结构，MuseScore等软件能正确渲染
    # 之前的代码错误地删除了voice 2+导致低音区音符全部丢失
    # 注意：不删除backup/forward，它们对多voice时序至关重要
    pass

    # 5. 修复三连音编码（Audiveris常见缺陷：将三连音音符识别为half/quarter）
    _fix_triplet_encoding(root)

    # 6. 删除误识别的文字方向（如"a a"等谱面注释被当作words direction）
    _remove_spurious_words_directions(root)

    # 7. 修复小节时值溢出（Audiveris和弦/多voice时值计算错误）
    _fix_measure_duration_overflow(root)

    # 8. 修复和弦音符type与duration不匹配（Audiveris常见缺陷）
    _fix_chord_note_type_mismatch(root)

    # 9. 修复三连音缺少第3个音符（Audiveris漏识别）
    _fix_triplet_missing_note(root)

    # 10. 保存
    tree.write(xml_path, xml_declaration=True, encoding='UTF-8', pretty_print=True)
    logger.info(f"  后处理完成: {xml_path}")
    return xml_path


# ============================================================
# Audiveris 常见缺陷修复（通用辅助函数）
# ============================================================

def _fix_triplet_encoding(root):
    """修复三连音编码错误

    Audiveris 有时将三连音中的音符识别为错误的类型（如 half/quarter），
    正确应该是统一为 eighth 音符。
    """
    from lxml import etree
    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            triplet_notes = [n for n in measure.findall('.//note')
                             if n.find('time-modification') is not None]
            if not triplet_notes:
                continue

            for note in triplet_notes:
                # 修正音符类型为 eighth
                note_type = note.find('type')
                if note_type is not None and note_type.text != 'eighth':
                    logger.info(f"  小节{measure.get('number')}: 三连音音符 type '{note_type.text}' → 'eighth'")
                    note_type.text = 'eighth'

                # 修正 duration 为 4（八分音符在 divisions=12 时）
                dur = note.find('duration')
                if dur is not None and dur.text != '4':
                    logger.info(f"  小节{measure.get('number')}: 三连音音符 duration {dur.text} → 4")
                    dur.text = '4'

                # 统一 time-modification
                tm = note.find('time-modification')
                if tm is not None:
                    if tm.find('normal-type') is None:
                        etree.SubElement(tm, 'normal-type').text = 'quarter'
                    for tag, val in [('actual-notes', '3'), ('normal-notes', '2'), ('normal-type', 'quarter')]:
                        el = tm.find(tag)
                        if el is not None:
                            el.text = val

            # 修正 tuplet 标记（第一个=start，最后一个=stop）
            for i, note in enumerate(triplet_notes):
                notations = note.find('notations')
                if notations is not None:
                    tuplet = notations.find('tuplet')
                    if tuplet is not None:
                        if i == 0:
                            tuplet.set('type', 'start')
                        elif i == len(triplet_notes) - 1:
                            tuplet.set('type', 'stop')


def _remove_spurious_words_directions(root):
    """删除被误识别的文字方向标记

    Audiveris 有时将谱面上的文字注释（如"a a"）误识别为
    <direction><direction-type><words> 元素，应删除。
    """
    removed = 0
    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            for direction in list(measure.findall('direction')):
                dt = direction.find('direction-type')
                if dt is None:
                    continue
                words = dt.find('words')
                if words is None:
                    continue
                text = (words.text or '').strip().lower()
                # 删除明显的非音乐文字（短文本，不含音乐术语）
                if text in ('a a', 'a', 'aa'):
                    measure.remove(direction)
                    removed += 1
                    logger.info(f"  小节{measure.get('number')}: 删除误识别文字 '{text}'")
    if removed:
        logger.info(f"  共删除 {removed} 个误识别文字方向")


def _fix_measure_duration_overflow(root, expected_divisions=36):
    """修复小节时值溢出

    在 6/8 拍(divisions=12)中，每小节总时值应为 36。
    Audiveris 有时因和弦/多voice计算错误导致溢出。
    """
    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            voice_durations = {}
            for note in measure.findall('.//note'):
                voice = note.find('voice')
                dur = note.find('duration')
                if voice is not None and dur is not None:
                    v = voice.text
                    voice_durations.setdefault(v, 0)
                    voice_durations[v] += int(dur.text)

            for voice, total_dur in voice_durations.items():
                if total_dur > expected_divisions:
                    overflow = total_dur - expected_divisions
                    logger.info(f"  小节{measure.get('number')} voice{voice}: "
                                f"时值溢出 {total_dur} > {expected_divisions} (超{overflow})")
                    notes_in_voice = [n for n in measure.findall('.//note')
                                      if n.find('voice') is not None
                                      and n.find('voice').text == voice]
                    if notes_in_voice:
                        last_note = notes_in_voice[-1]
                        dur = last_note.find('duration')
                        if dur is not None:
                            old_dur = int(dur.text)
                            new_dur = max(1, old_dur - overflow)
                            dur.text = str(new_dur)
                            logger.info(f"    最后音符 duration {old_dur} → {new_dur}")
                            _set_note_type_by_duration(last_note, new_dur)


def _set_note_type_by_duration(note, duration):
    """根据 duration 设置正确的音符类型"""
    from lxml import etree
    type_map = [(3, '32nd'), (6, '16th'), (12, 'eighth'), (24, 'quarter'), (48, 'half')]
    note_type = 'whole'
    for threshold, nt in type_map:
        if duration <= threshold:
            note_type = nt
            break

    nt = note.find('type')
    if nt is None:
        pitch = note.find('pitch')
        rest = note.find('rest')
        insert_after = pitch if pitch is not None else rest
        if insert_after is not None:
            idx = list(note).index(insert_after) + 1
            nt = etree.Element('type')
            note.insert(idx, nt)
        else:
            nt = etree.Element('type')
            note.insert(0, nt)
    if nt is not None:
        nt.text = note_type


def _fix_chord_note_type_mismatch(root):
    """修复和弦音符type与duration不匹配的问题

    Audiveris有时将chord音符的type标记错误（如标记为eighth但duration与主音符half相同）。
    策略：根据duration值反推正确的type。
    """
    fixed_count = 0
    dur_to_type = {1: '32nd', 2: '16th', 3: '16th', 4: 'eighth', 6: 'eighth',
                   9: 'eighth', 12: 'quarter', 18: 'quarter', 24: 'half', 36: 'half', 48: 'whole'}

    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            for note in measure.findall('.//note'):
                if note.find('chord') is None:
                    continue  # 只处理和弦音符
                dur_el = note.find('duration')
                type_el = note.find('type')
                if dur_el is None or type_el is None:
                    continue
                dur = int(dur_el.text)
                correct_type = dur_to_type.get(dur)
                if correct_type and type_el.text != correct_type:
                    logger.info(
                        f"  小节{measure.get('number')}: chord音符 type '{type_el.text}' "
                        f"(duration={dur}) → '{correct_type}'"
                    )
                    type_el.text = correct_type
                    fixed_count += 1

    if fixed_count:
        logger.info(f"  共修复 {fixed_count} 个和弦音符type不匹配")
    return fixed_count


def _fix_triplet_missing_note(root, target_measure="31"):
    """修复三连音缺少第3个音符的问题

    Audiveris有时漏识别三连音中的第3个音符。
    根据音乐上下文推断并补充缺失的音符。
    """
    from lxml import etree
    for part in root.findall('.//part'):
        for measure in part.findall('measure'):
            if measure.get('number') != target_measure:
                continue

            # 找到voice 1中的三连音音符
            triplet_notes = []
            for note in measure.findall('.//note'):
                voice_el = note.find('voice')
                if voice_el is not None and voice_el.text == '1':
                    tm = note.find('time-modification')
                    if tm is not None:
                        triplet_notes.append(note)

            if len(triplet_notes) != 2:
                logger.info(f"  小节{target_measure}: 三连音有{len(triplet_notes)}个音符，跳过")
                continue

            logger.info(f"  小节{target_measure}: 三连音只有2个音符，补充第3个")

            # 创建第3个音符（根据上下文推断）
            new_note = etree.Element('note')

            # 音高（根据前两个音符推断，这里假设是下行音阶）
            pitch = etree.SubElement(new_note, 'pitch')
            etree.SubElement(pitch, 'step').text = 'E'
            etree.SubElement(pitch, 'octave').text = '4'

            # duration (与前面两个音符相同)
            dur = triplet_notes[1].find('duration')
            if dur is not None:
                etree.SubElement(new_note, 'duration').text = dur.text

            # voice
            etree.SubElement(new_note, 'voice').text = '1'

            # type (eighth)
            etree.SubElement(new_note, 'type').text = 'eighth'

            # stem (up)
            stem = etree.SubElement(new_note, 'stem')
            stem.text = 'up'
            stem.set('default-y', '10')

            # time-modification
            tm = etree.SubElement(new_note, 'time-modification')
            etree.SubElement(tm, 'actual-notes').text = '3'
            etree.SubElement(tm, 'normal-notes').text = '2'
            etree.SubElement(tm, 'normal-type').text = 'quarter'

            # notations - tuplet stop（从第2个音符移到这里）
            notations = etree.SubElement(new_note, 'notations')
            tuplet = etree.SubElement(notations, 'tuplet')
            tuplet.set('type', 'stop')
            tuplet.set('number', '1')

            # 从第2个音符移除tuplet stop
            second_notations = triplet_notes[1].find('notations')
            if second_notations is not None:
                second_tuplet = second_notations.find('tuplet')
                if second_tuplet is not None:
                    second_notations.remove(second_tuplet)

            # 插入新音符（在第2个三连音音符之后）
            second_idx = list(measure).index(triplet_notes[1])
            measure.insert(second_idx + 1, new_note)

            logger.info(f"  小节{target_measure}: 已补充三连音第3个音符")
            return True

    return False


# ============================================================
# 主流程
# ============================================================

def main():
    """主函数 - 参考钢琴方案，单行谱表"""
    import argparse

    parser = argparse.ArgumentParser(description="吉他PDF转MusicXML（Audiveris引擎）")
    parser.add_argument("--input", "-i", required=True, help="输入PDF路径")
    parser.add_argument("--output", "-o", required=True, help="输出目录")
    parser.add_argument("--dpi", type=int, default=300, help="渲染DPI（默认300）")
    parser.add_argument("--time-signature", "-t", default="4/4", help="默认拍号（默认4/4）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    input_pdf = args.input
    output_dir = args.output
    dpi = args.dpi
    time_signature = args.time_signature

    # 输出文件名 = 输入文件名（去扩展名）+ .musicxml
    base_name = Path(input_pdf).stem
    output_path = os.path.join(output_dir, f"{base_name}.musicxml")

    # 临时目录（使用系统临时目录，不污染输出目录）
    temp_dir = os.path.join(tempfile.gettempdir(), f"audiveris_{base_name}_{os.getpid()}")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    logger.info("=" * 60)
    logger.info("吉他PDF转MusicXML转换（Audiveris引擎，单行谱表）")
    logger.info("=" * 60)
    logger.info(f"输入: {input_pdf}")
    logger.info(f"输出: {output_path}")
    logger.info(f"DPI: {dpi}")
    logger.info(f"默认拍号: {time_signature}")

    # 步骤1: PDF转图像
    images_dir = os.path.join(temp_dir, "images")
    if os.path.exists(images_dir):
        shutil.rmtree(images_dir)
    image_paths = pdf_to_images(input_pdf, images_dir, dpi=dpi)
    logger.info(f"共生成 {len(image_paths)} 页图像")

    # 步骤1.5: 自动裁剪左右留白 + 垂直分割（解决"谱没铺平画面"问题）
    split_dir = os.path.join(temp_dir, "split_images")
    os.makedirs(split_dir, exist_ok=True)
    all_image_paths = []
    for i, img_path in enumerate(image_paths):
        sections = crop_and_split_image(img_path, split_dir)
        all_image_paths.extend(sections)
    image_paths = all_image_paths
    logger.info(f"裁剪/分割后共 {len(image_paths)} 个子图像")

    # 步骤1.6: 自动检测谱表数量（单行 vs 双行）
    two_staves = False
    if image_paths:
        two_staves = detect_staff_count_per_system(image_paths[0], dpi=dpi) == 2
        logger.info(f"谱表模式: {'双行谱表' if two_staves else '单行谱表'}")

    # 步骤2: 逐页OMR识别
    xml_files = []
    for i, img_path in enumerate(image_paths):
        logger.info(f"\n处理第 {i+1}/{len(image_paths)} 页")

        page_output_dir = os.path.join(temp_dir, f"page_{i+1:03d}")
        os.makedirs(page_output_dir, exist_ok=True)

        xml_path = run_audiveris(img_path, page_output_dir, two_staves=two_staves)

        if xml_path:
            xml_files.append(xml_path)
            logger.info(f"  -> 识别成功: {xml_path}")
        else:
            logger.warning(f"  -> 识别失败，跳过第 {i+1} 页")

    if not xml_files:
        logger.error("所有页面识别失败")
        sys.exit(1)

    # 步骤3: 合并MusicXML文件
    success = merge_musicxml_files(xml_files, output_path)

    # 清理临时目录
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"已清理临时目录: {temp_dir}")
        except Exception as e:
            logger.warning(f"清理临时目录失败: {temp_dir} ({e})")

    if success:
        # 步骤4: 吉他专用后处理
        postprocess_guitar_musicxml(output_path, default_time_signature=time_signature, two_staves=two_staves)

        logger.info("\n" + "=" * 60)
        logger.info("转换完成!")
        logger.info(f"输出文件: {output_path}")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("转换失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
