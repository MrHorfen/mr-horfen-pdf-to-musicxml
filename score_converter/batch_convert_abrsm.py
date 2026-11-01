"""
文件: batch_convert_abrsm.py
用途: 批量转码 ABRSM 钢琴考级曲目，按等级分文件夹输出双声部 MusicXML
调用方: 命令行手动执行
输入: ABRSM PDF 考级曲目
输出: 按等级分目录的双声部 MusicXML
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
import cv2
import numpy as np
from PIL import Image

# 配置
INPUT_BASE = os.environ.get("INPUT_BASE", "input")
OUTPUT_BASE = os.environ.get("OUTPUT_BASE", "output")
TEMP_DIR = os.environ.get("TEMP_DIR", os.path.join("temp", "cache", "abrsm"))
JAVA_HOME = os.environ.get("JAVA_HOME", "")
AUDIVERIS = os.environ.get("AUDIVERIS", "Audiveris")
DPI = 200


def pdf_to_image(pdf_path, output_path):
    """PDF 转图片"""
    doc = fitz.open(pdf_path)
    page = doc[0]
    mat = fitz.Matrix(DPI/72, DPI/72)
    pix = page.get_pixmap(matrix=mat)
    pix.save(output_path)
    doc.close()


def enhance_image(image_path, output_path):
    """图像增强（二值化+去噪+缩小）"""
    pil_img = Image.open(image_path)
    
    # Audiveris 限制 2000 万像素，缩小到安全范围
    max_pixels = 15000000
    w, h = pil_img.size
    if w * h > max_pixels:
        scale = (max_pixels / (w * h)) ** 0.5
        new_w = int(w * scale)
        new_h = int(h * scale)
        pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
    
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    pil_out = Image.fromarray(binary)
    pil_out.save(output_path)


def run_omr(image_path, output_dir):
    """运行 Audiveris OMR 识别"""
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA_HOME
    env["Path"] = f"{JAVA_HOME}\\bin;{env.get('Path', '')}"

    cmd = [
        AUDIVERIS,
        "-batch",
        "-export",
        "-output", output_dir,
        "-option", "org.audiveris.omr.sheet.BookManager.twoStaves=true",
        image_path
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="ignore",
            env=env
        )

        # 检查是否生成了 MXL 文件
        mxl_files = []
        for root, dirs, files in os.walk(output_dir):
            for f in sorted(files):
                if f.endswith(".mxl"):
                    mxl_files.append(os.path.join(root, f))

        return len(mxl_files) > 0
    except subprocess.TimeoutExpired:
        print("    OMR 超时")
        return False
    except Exception as e:
        print(f"    OMR 错误: {e}")
        return False


def extract_mxl_to_musicxml(omr_dir, output_path):
    """从 OMR 输出提取 MusicXML"""
    mxl_files = []
    for root, dirs, files in os.walk(omr_dir):
        for f in sorted(files):
            if f.endswith('.mxl'):
                mxl_files.append(os.path.join(root, f))

    if not mxl_files:
        return False

    mxl_path = mxl_files[0]

    # 解压 .mxl（实际是 ZIP）
    with zipfile.ZipFile(mxl_path, 'r') as z:
        xml_files = [f for f in z.namelist() if f.endswith('.xml')]
        if xml_files:
            z.extract(xml_files[0], omr_dir)
            extracted_xml = os.path.join(omr_dir, xml_files[0])
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(extracted_xml, output_path)
            return True

    return False


def process_pdf(pdf_path, output_path):
    """处理单个 PDF 文件"""
    pdf_name = os.path.basename(pdf_path).replace(".pdf", "")
    print(f"  处理: {pdf_name}")

    # 创建临时目录
    temp_work = os.path.join(TEMP_DIR, pdf_name)
    os.makedirs(temp_work, exist_ok=True)

    image_path = os.path.join(temp_work, "image.png")
    enhanced_path = os.path.join(temp_work, "enhanced.png")
    omr_dir = os.path.join(temp_work, "omr")
    os.makedirs(omr_dir, exist_ok=True)

    # 1. PDF 转图片
    pdf_to_image(pdf_path, image_path)

    # 2. 图像增强
    enhance_image(image_path, enhanced_path)

    # 3. OMR 识别
    if not run_omr(enhanced_path, omr_dir):
        print("    失败")
        return False

    # 4. 提取 MusicXML
    if extract_mxl_to_musicxml(omr_dir, output_path):
        print(f"    成功 -> {os.path.basename(output_path)}")
        return True

    print("    失败")
    return False


def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(OUTPUT_BASE, exist_ok=True)

    total = 0
    success = 0

    for grade in sorted(os.listdir(INPUT_BASE)):
        grade_dir = os.path.join(INPUT_BASE, grade)
        if not os.path.isdir(grade_dir):
            continue

        # 创建输出子目录
        grade_output = os.path.join(OUTPUT_BASE, grade)
        os.makedirs(grade_output, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"等级: {grade}")
        print(f"{'='*60}")

        pdfs = sorted([f for f in os.listdir(grade_dir) if f.endswith('.pdf')])

        for pdf_file in pdfs:
            pdf_path = os.path.join(grade_dir, pdf_file)
            output_path = os.path.join(grade_output, pdf_file.replace('.pdf', '.musicxml'))

            if os.path.exists(output_path):
                print(f"  跳过: {pdf_file.replace('.pdf', '')} (已存在)")
                continue

            total += 1
            if process_pdf(pdf_path, output_path):
                success += 1

    print(f"\n{'='*60}")
    print(f"全部完成! 成功: {success}/{total}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
