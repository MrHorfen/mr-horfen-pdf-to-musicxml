"""
文件: batch_convert_piano_exam.py
用途: 批量转码国内考级钢琴练习曲（多页PDF支持），钢琴双声部模式，自动识别节拍和调号
调用方: 命令行手动执行
输入: 国内考级钢琴 PDF
输出: 双声部 MusicXML
依赖: PyMuPDF(fitz), cv2, numpy
作者: Meandro(菠萝的自我救赎）
创建时间: 2026-06-10
最后更新: 2026-07-31
更新说明: 补充标准化头部注释
"""
import fitz
import os
import subprocess
import zipfile
import re
import cv2
import numpy as np
import shutil
from PIL import Image


def parse_filename(filename):
    """解析文件名提取级别、编号、曲名"""
    name = os.path.splitext(filename)[0]
    
    # 匹配模式: "八级02《练习曲op.76 no.2》西贝柳斯"
    match = re.match(r'([一二三四五六七八九十]+)级(\d+)[《](.+?)[》](.*)', name)
    if match:
        return match.group(1), match.group(2), match.group(3), match.group(4).strip()
    
    # 匹配模式: "九级10阿拉木汗-张朝编曲"
    match = re.match(r'([一二三四五六七八九十]+)级(\d+)(.*?)-(.*)', name)
    if match:
        return match.group(1), match.group(2), match.group(3).strip(), match.group(4).strip()
    
    return None, None, name, ""


def is_staff_page(image_path):
    """检测图片页面是否包含五线谱"""
    import warnings
    Image.MAX_IMAGE_PIXELS = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)

    if img is None:
        return False

    h, w = img.shape
    max_dim = 1200
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
        h, w = img.shape

    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)

    horizontal_ratio = np.sum(horizontal_lines > 0) / (h * w)
    
    if horizontal_ratio < 0.015:
        return False

    y_projection = np.sum(horizontal_lines > 0, axis=1)
    line_rows = np.where(y_projection > 0)[0]
    
    if len(line_rows) < 10:
        return False
    
    gaps = np.diff(line_rows)
    small_gaps = np.sum(gaps < 15)
    
    return small_gaps >= 8


def enhance_image_for_omr(image_path, output_path=None):
    """图像增强以提高OMR识别率 - 输出增强灰度图"""
    img = cv2.imread(image_path)
    if img is None:
        return image_path
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # CLAHE 对比度增强 - Audiveris 需要灰度图
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    
    if output_path:
        cv2.imwrite(output_path, enhanced)
        return output_path
    return enhanced


def run_omr_single_page(image_path, output_dir, java_home, audiveris_exe):
    """对单页图片执行OMR识别"""
    # Audiveris.exe 会自动找到 app 目录下的 jar 文件
    cmd = [
        audiveris_exe,
        '-batch',
        '-transcribe',
        '-export',
        '-output', output_dir,
        image_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    
    # 调试：输出Audiveris的执行结果
    if result.returncode != 0:
        print(f"      Audiveris错误: returncode={result.returncode}")
        if result.stderr:
            print(f"      stderr: {result.stderr[:500]}")
        if result.stdout:
            print(f"      stdout: {result.stdout[:500]}")
    
    xml_files = [f for f in os.listdir(output_dir) if f.endswith('.xml') or f.endswith('.mxl')]
    if xml_files:
        xml_path = os.path.join(output_dir, xml_files[0])
        if xml_path.endswith('.mxl'):
            with zipfile.ZipFile(xml_path, 'r') as z:
                z.extractall(output_dir)
            xml_path = os.path.join(output_dir, [f for f in z.namelist() if f.endswith('.xml')][0])
        return xml_path
    return None


def merge_musicxml_pages(xml_paths, output_path):
    """合并多个单页 MusicXML 为一个完整文件
    
    乐理原则（第一原则性）：
    - 从所有 part 收集所有音符，按 staff 编号重新分组
    - 最终输出：1个 Piano part，2个 staff（高音+低音）
    - 用 <backup> 标签实现声部回退，避免时长叠加
    """
    try:
        all_pages_content = []
        for xp in xml_paths:
            with open(xp, 'r', encoding='utf-8') as f:
                all_pages_content.append(f.read())

        first_content = all_pages_content[0]
        
        # 提取 header（不含 part-list）
        # 找到 <part-list> 开始位置
        part_list_start = first_content.find('<part-list>')
        if part_list_start < 0:
            part_list_start = first_content.find('<part-list')
        
        # 找到最后一个 </part-list> 的位置（可能有多个part-list）
        last_part_list_end = first_content.rfind('</part-list>')
        if last_part_list_end < 0:
            last_part_list_end = first_content.find('</defaults>')
            header = first_content[:last_part_list_end]
        else:
            header = first_content[:part_list_start]
        
        # 提取第一页的 attributes（调号、拍号、divisions等）
        first_attrs_match = re.search(r'<attributes>(.*?)</attributes>', first_content, re.DOTALL)
        first_attrs = first_attrs_match.group(0) if first_attrs_match else ""

        # 提取拍号信息（用于乐理校验）
        time_match = re.search(r'<time>.*?<beats>(\d+)</beats>.*?<beat-type>(\d+)</beat-type>.*?</time>', first_content, re.DOTALL)
        if time_match:
            beats = int(time_match.group(1))
            beat_type = int(time_match.group(2))
            print(f"    拍号: {beats}/{beat_type}")
        else:
            beats = None
            beat_type = None
            print(f"    警告: 无法识别拍号，跳过乐理校验")

        # 从所有页面的所有 part 收集 measures
        all_page_measures = []  # [(staff1_notes_xml, staff2_notes_xml, attributes_xml), ...]
        
        for page_idx, page_content in enumerate(all_pages_content):
            # 找到所有 part
            parts = re.findall(r'<part id="([^"]+)">(.*?)</part>', page_content, re.DOTALL)
            
            # 收集每个 part 的 measures
            part_measures = {}  # {part_id: [measure_xml, ...]}
            for part_id, part_content in parts:
                measures = re.findall(r'(<measure[^>]*>.*?</measure>)', part_content, re.DOTALL)
                part_measures[part_id] = measures
            
            # 确定最大 measure 数量
            max_measures = max(len(m) for m in part_measures.values()) if part_measures else 0
            
            # 对每个 measure，从所有 part 收集音符，按 staff 分组
            for m_idx in range(max_measures):
                staff1_notes = []
                staff2_notes = []
                attributes_xml = ""
                has_backup = False
                
                for part_id, measures in part_measures.items():
                    if m_idx >= len(measures):
                        continue
                    
                    measure_xml = measures[m_idx]
                    
                    # 提取 attributes（只在第一页的第一个 measure 保留）
                    if page_idx == 0 and m_idx == 0 and not attributes_xml:
                        attr_match = re.search(r'<attributes>.*?</attributes>', measure_xml, re.DOTALL)
                        if attr_match:
                            attributes_xml = attr_match.group(0)
                    
                    # 提取所有 note，按 staff 分组
                    notes = re.findall(r'<note[^>]*>.*?</note>', measure_xml, re.DOTALL)
                    
                    # 提取非 note/attributes/backup/print 的 measure 级元素（如 direction, barline 等）
                    # 但只从第一个 part 取，避免重复
                    if part_id == list(part_measures.keys())[0]:
                        # 保留 direction, barline, print 等
                        other_elements = re.findall(r'(<(?:direction|barline|print|harmony|figured-bass)[^>]*>.*?</(?:direction|barline|print|harmony|figured-bass)>)', measure_xml, re.DOTALL)
                    
                    for note_xml in notes:
                        # 确定这个 note 属于哪个 staff
                        staff_match = re.search(r'<staff>(\d+)</staff>', note_xml)
                        staff_num = int(staff_match.group(1)) if staff_match else 1
                        
                        # 确保 note 有 staff 标签
                        if not staff_match:
                            # 在 </note> 前插入 <staff>
                            note_xml = note_xml.replace('</note>', f'<staff>{staff_num}</staff>\n      </note>')
                        
                        # 移除 voice 标签中的原始 voice 编号，统一为 voice 1 或 voice 2
                        note_xml = re.sub(r'<voice>\d+</voice>', f'<voice>{"1" if staff_num == 1 else "2"}</voice>', note_xml)
                        
                        if staff_num <= 1:
                            staff1_notes.append(note_xml)
                        else:
                            staff2_notes.append(note_xml)
                
                all_page_measures.append((staff1_notes, staff2_notes, attributes_xml))
        
        # 构建合并后的 measures
        merged_part_content = ""
        measure_counter = 0
        total_notes = 0
        duration_warnings = []

        for staff1_notes, staff2_notes, attributes_xml in all_page_measures:
            measure_counter += 1
            
            measure_xml = f'    <measure number="{measure_counter}">\n'
            
            # 第一个 measure 添加 attributes
            if measure_counter == 1 and attributes_xml:
                # 确保 attributes 中有 <staves>2</staves>
                if '<staves>' not in attributes_xml:
                    attributes_xml = attributes_xml.replace('</attributes>', '<staves>2</staves>\n      </attributes>')
                # 确保有正确的 clef
                if '<clef number="2">' not in attributes_xml:
                    # 检查是否有 F 谱号
                    if '<sign>F</sign>' not in attributes_xml:
                        attributes_xml = attributes_xml.replace('</attributes>', 
                            '<clef number="2">\n          <sign>F</sign>\n          <line>4</line>\n        </clef>\n      </attributes>')
                measure_xml += f'      {attributes_xml}\n'
            
            # Staff 1 的音符
            for note_xml in staff1_notes:
                # 缩进
                note_xml = '\n'.join('      ' + line.strip() if line.strip() else line for line in note_xml.split('\n'))
                measure_xml += f'      {note_xml}\n'
                total_notes += 1
            
            # Backup 回退到小节开头
            if staff2_notes:
                # 计算 staff1 的总 duration
                staff1_duration = 0
                for note_xml in staff1_notes:
                    dur_match = re.search(r'<duration>(\d+)</duration>', note_xml)
                    if dur_match:
                        # 和弦音符不重复计算
                        if '<chord/>' not in note_xml:
                            staff1_duration += int(dur_match.group(1))
                
                measure_xml += f'      <backup>\n        <duration>{staff1_duration}</duration>\n      </backup>\n'
                
                # Staff 2 的音符
                for note_xml in staff2_notes:
                    note_xml = '\n'.join('      ' + line.strip() if line.strip() else line for line in note_xml.split('\n'))
                    measure_xml += f'      {note_xml}\n'
                    total_notes += 1
            
            measure_xml += '    </measure>\n'
            merged_part_content += measure_xml
            
            # 乐理校验
            if beats and beat_type:
                div_match = re.search(r'<divisions>(\d+)</divisions>', attributes_xml) if attributes_xml else None
                div = int(div_match.group(1)) if div_match else 1
                
                staff1_total = sum(int(m.group(1)) for n in staff1_notes if '<chord/>' not in n for m in [re.search(r'<duration>(\d+)</duration>', n)] if m)
                staff2_total = sum(int(m.group(1)) for n in staff2_notes if '<chord/>' not in n for m in [re.search(r'<duration>(\d+)</duration>', n)] if m)
                
                expected = beats * div * (4 / beat_type)
                tolerance = div * 0.25  # 25% 容差
                
                if staff1_total > 0 and abs(staff1_total - expected) > tolerance:
                    actual = staff1_total / (div * (4 / beat_type))
                    duration_warnings.append(f"小节{measure_counter} staff1: {actual:.1f}拍 (期望{beats}拍)")
                if staff2_total > 0 and abs(staff2_total - expected) > tolerance:
                    actual = staff2_total / (div * (4 / beat_type))
                    duration_warnings.append(f"小节{measure_counter} staff2: {actual:.1f}拍 (期望{beats}拍)")

        # 构建最终文件
        part_list = """  <part-list>
    <score-part id="P1">
      <part-name>Piano</part-name>
      <part-abbreviation>Pno.</part-abbreviation>
      <score-instrument id="P1-I1">
        <instrument-name>Acoustic Grand Piano</instrument-name>
      </score-instrument>
      <midi-instrument id="P1-I1">
        <midi-channel>1</midi-channel>
        <midi-program>1</midi-program>
        <volume>80</volume>
      </midi-instrument>
    </score-part>
  </part-list>"""

        final = f"""{header}
{part_list}
  <part id="P1">
{merged_part_content}  </part>
</score-partwise>"""

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(final)

        # 添加 DOCTYPE
        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if '<!DOCTYPE' not in content:
            content = content.replace(
                '?>',
                '?>\n<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0.3 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">',
                1
            )
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)

        print(f"    合并完成: {measure_counter}小节, {total_notes}音符")
        
        if duration_warnings:
            print(f"    ⚠️ 乐理警告: {len(duration_warnings)}个小节时值不正确")
            for w in duration_warnings[:5]:
                print(f"       {w}")
            if len(duration_warnings) > 5:
                print(f"       ... 还有{len(duration_warnings)-5}个警告")
        else:
            print(f"    ✓ 乐理校验通过")
        
        return True

    except Exception as e:
        print(f"    合并失败: {e}")
        import traceback
        traceback.print_exc()
        if os.path.exists(xml_paths[0]):
            shutil.copy(xml_paths[0], output_path)
            return True
        return False


def analyze_musicxml(xml_path):
    """分析 MusicXML 提取调号、拍号、小节数等信息"""
    info = {
        "key_signature": "未知",
        "time_signature": "未知",
        "measures": 0,
        "notes": 0,
        "staves": 0,
        "pages": 0,
    }

    try:
        with open(xml_path, 'r', encoding='utf-8') as f:
            content = f.read()

        key_match = re.search(r'<key>.*?<fifths>(-?\d+)</fifths>.*?</key>', content, re.DOTALL)
        if key_match:
            fifths = int(key_match.group(1))
            sharp_keys = {0: "C大调/a小调", 1: "G大调/e小调", 2: "D大调/b小调",
                          3: "A大调/f#小调", 4: "E大调/c#小调", 5: "B大调/g#小调",
                          6: "F#大调/d#小调", 7: "C#大调/a#小调",
                          -1: "F大调/d小调", -2: "Bb大调/g小调", -3: "Eb大调/c小调",
                          -4: "Ab大调/f小调", -5: "Db大调/bb小调", -6: "Gb大调/eb小调",
                          -7: "Cb大调/ab小调"}
            info["key_signature"] = sharp_keys.get(fifths, f"{fifths}个升降号")

        time_match = re.search(r'<time>.*?<beats>(\d+)</beats>.*?<beat-type>(\d+)</beat-type>.*?</time>', content, re.DOTALL)
        if time_match:
            info["time_signature"] = f"{time_match.group(1)}/{time_match.group(2)}"

        measures = re.findall(r'<measure[\s>]', content)
        info["measures"] = len(measures)

        notes = re.findall(r'<note[\s>]', content)
        info["notes"] = len(notes)

        staves_match = re.search(r'<staves>(\d+)</staves>', content)
        if staves_match:
            info["staves"] = int(staves_match.group(1))

        parts = re.findall(r'<part\s', content)
        info["parts"] = len(parts)

    except Exception as e:
        info["error"] = str(e)

    return info


def process_pdf(pdf_path, output_path, log_entries):
    """处理单个多页 PDF 文件"""
    pdf_name = os.path.basename(pdf_path)
    grade, number, title, composer = parse_filename(pdf_name)
    
    JAVA_HOME = os.environ.get("JAVA_HOME", "")
    AUDIVERIS_EXE = os.environ.get("AUDIVERIS", "Audiveris")
    # Audiveris 最大支持 20,000,000 像素
    # DPI=100 约 15,520,045 像素，DPI=200 约 62,080,182 像素
    # DPI=110 约 18,780,000 像素，在限制内且识别质量更好
    DPI = 110
    
    # 临时目录放到纯英文路径下，避免cv2读取中文路径失败
    safe_name = f"pdf_{hash(pdf_path) & 0xFFFFFFFF:08x}"
    temp_dir = os.path.join(os.environ.get('TEMP', os.path.join(os.getcwd(), 'temp')), 'omr_piano', safe_name)
    os.makedirs(temp_dir, exist_ok=True)
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    # 1. PDF -> 图片
    print(f"  1/4 PDF -> {total_pages}页图片...")
    image_paths = []
    for i in range(total_pages):
        page = doc[i]
        mat = fitz.Matrix(DPI / 72, DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(temp_dir, f'page_{i+1}.png')
        pix.save(img_path)
        image_paths.append(img_path)
    doc.close()
    print(f"     OK ({total_pages}页)")
    
    # 2. 检测乐谱页面
    print(f"  2/5 检测乐谱页面...")
    staff_pages = []
    for i, img_path in enumerate(image_paths):
        if is_staff_page(img_path):
            staff_pages.append(i + 1)
    
    non_staff_pages = [i + 1 for i in range(total_pages) if i + 1 not in staff_pages]
    if non_staff_pages:
        print(f"跳过非乐谱页: {non_staff_pages}，保留{len(staff_pages)}页")
    else:
        print(f"全部{total_pages}页均为乐谱")
    
    if not staff_pages:
        print(f"  警告: 未检测到任何乐谱页面！")
        return False
    
    # 只保留乐谱页面
    valid_image_paths = [image_paths[i - 1] for i in staff_pages]
    
    # 3. 图像增强
    print(f"  3/5 图像增强...")
    enhanced_paths = []
    for img_path in valid_image_paths:
        out = os.path.join(temp_dir, f'enhanced_{os.path.basename(img_path)}')
        enhance_image_for_omr(img_path, out)
        enhanced_paths.append(out)
    print(f"     OK ({len(enhanced_paths)}页)")
    
    # 4. OMR识别
    print(f"  4/5 OMR识别（双声部，{len(enhanced_paths)}页）...")
    omr_dir = os.path.join(temp_dir, 'omr')
    os.makedirs(omr_dir, exist_ok=True)
    
    xml_paths = []
    failed_pages = []
    for i, img_path in enumerate(enhanced_paths):
        print(f"     第{i+1}页...", end=' ')
        page_omr_dir = os.path.join(omr_dir, f'page_{i+1}')
        os.makedirs(page_omr_dir, exist_ok=True)
        
        xml_path = run_omr_single_page(img_path, page_omr_dir, JAVA_HOME, AUDIVERIS_EXE)
        if xml_path:
            xml_paths.append(xml_path)
            print(f"OK")
        else:
            failed_pages.append(i + 1)
            print(f"失败")
    
    if not xml_paths:
        print(f"  错误: 所有页面均识别失败！")
        return False
    
    # 5. 合并MusicXML
    print(f"  5/5 合并{len(xml_paths)}页MusicXML...")
    success = merge_musicxml_pages(xml_paths, output_path)
    
    if not success:
        print(f"  错误: 合并失败！")
        return False
    
    # 分析结果
    info = analyze_musicxml(output_path)
    
    file_size = os.path.getsize(output_path)
    size_str = f"{file_size / 1024:.1f}KB" if file_size < 1024 * 1024 else f"{file_size / (1024*1024):.1f}MB"
    
    status = "成功" if not failed_pages else "部分成功"
    error_msg = f" (部分页失败: {failed_pages})" if failed_pages else ""
    
    log_entries.append({
        "grade": grade or "",
        "number": number or "",
        "title": title or pdf_name,
        "composer": composer or "",
        "pages": len(staff_pages),
        "key": info.get("key_signature", "未知"),
        "time": info.get("time_signature", "未知"),
        "measures": info.get("measures", 0),
        "notes": info.get("notes", 0),
        "staves": info.get("staves", 2),
        "status": status,
        "error": error_msg,
        "file_size": size_str,
    })
    
    print(f"    -> 调号:{info.get('key_signature','未知')} 拍号:{info.get('time_signature','未知')} "
          f"小节:{info.get('measures',0)} 音符:{info.get('notes',0)} 声部:{info.get('staves',2)} {error_msg}")
    
    return True


def main():
    INPUT_DIR = os.environ.get("INPUT_DIR", "input")
    OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    pdf_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith('.pdf')]
    pdf_files.sort()
    
    print(f"找到 {len(pdf_files)} 个PDF文件")
    print(f"模式: 钢琴双声部 | DPI: 100")
    print(f"输出: {OUTPUT_DIR}")
    print("=" * 70)
    
    log_entries = []
    success = 0
    total = 0
    skipped_pages = []
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(INPUT_DIR, pdf_file)
        
        output_name = os.path.splitext(pdf_file)[0] + ".musicxml"
        output_path = os.path.join(OUTPUT_DIR, output_name)
        
        # 强制重新处理所有文件
        total += 1
        if process_pdf(pdf_path, output_path, log_entries):
            success += 1
    
    print("\n" + "=" * 70)
    print(f"转换完成! 成功: {success}/{total}")
    print("=" * 70)
    
    print(f"\n{'级别':<8} {'曲名':<30} {'页数':<5} {'调号':<16} {'拍号':<6} {'小节':<5} {'音符':<6} {'声部':<4} {'耗时':<6} {'大小'}")
    print("-" * 110)
    for entry in log_entries:
        if entry["status"] in ("成功", "部分成功"):
            print(f"{entry['grade']:<8} {entry['title']:<30} {entry['pages']:<5} {entry['key']:<16} {entry['time']:<6} "
                  f"{entry['measures']:<5} {entry['notes']:<6} {entry['staves']:<4} "
                  f"{entry.get('time_elapsed',''):<6} {entry['file_size']}")
        else:
            print(f"{entry.get('grade',''):<8} {entry.get('title',''):<30} {entry.get('pages',''):<5} 失败: {entry.get('error','')}")
    
    # 保存报告
    report_path = os.path.join(OUTPUT_DIR, "conversion_report.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# PDF五线谱转MusicXML转换报告\n\n")
        f.write(f"## 转换统计\n\n")
        f.write(f"- 总文件数: {total}\n")
        f.write(f"- 成功: {success}\n")
        f.write(f"- 失败: {total - success}\n\n")
        f.write(f"## 详细结果\n\n")
        f.write(f"| 级别 | 曲名 | 页数 | 调号 | 拍号 | 小节 | 音符 | 声部 | 状态 | 大小 |\n")
        f.write(f"|------|------|------|------|------|------|------|------|------|------|\n")
        for entry in log_entries:
            f.write(f"| {entry.get('grade','')} | {entry.get('title','')} | {entry.get('pages','')} | "
                   f"{entry.get('key','')} | {entry.get('time','')} | {entry.get('measures','')} | "
                   f"{entry.get('notes','')} | {entry.get('staves','')} | {entry.get('status','')} | "
                   f"{entry.get('file_size','')} |\n")
    
    print(f"\n报告已保存: {report_path}")


if __name__ == '__main__':
    main()
