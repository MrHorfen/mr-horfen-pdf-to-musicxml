"""
文件: diagnose.py
用途: 转码结果体检——采集页级/跨页/合并后/渲染层结构指标，输出诊断 JSON（独立原型，不依赖生产代码）
调用方: router.py（独立环境）/ 人工回放
输入: --pages 每页 MusicXML 目录；--merged 合并后 MusicXML；--rendered 渲染 PDF（可选）
输出: 诊断指标 JSON（stdout 或 --out 指定文件）
依赖: lxml、PyMuPDF(fitz)、numpy（仅渲染体检时需要）
创建时间: 2026-10-21
最后更新: 2026-10-21
更新说明: 初版，用于方案路由原型的指标采集
"""

import argparse
import glob
import json
import os
import sys

from lxml import etree


def read_root(path):
    return etree.parse(path).getroot()


def part_names(root):
    """part-list 中 id → part-name"""
    names = {}
    part_list = root.find("part-list")
    if part_list is None:
        return names
    for sp in part_list.findall("score-part"):
        name_el = sp.find("part-name")
        names[sp.get("id")] = (name_el.text or "").strip() if name_el is not None else ""
    return names


def page_metrics(path):
    root = read_root(path)
    names = part_names(root)
    parts = root.findall("part")

    part_rows = []
    for part in parts:
        pid = part.get("id")
        measures = part.findall("measure")
        notes = part.findall(".//note")
        non_rest = [n for n in notes if n.find("rest") is None]
        empty_measures = 0
        for m in measures:
            m_notes = m.findall("note")
            if not m_notes or all(n.find("rest") is not None for n in m_notes):
                empty_measures += 1
        staves = set()
        for n in notes:
            s = n.find("staff")
            staves.add(int(s.text) if s is not None and s.text else 1)
        part_rows.append({
            "id": pid,
            "name": names.get(pid, ""),
            "measures": len(measures),
            "notes": len(notes),
            "non_rest_notes": len(non_rest),
            "empty_measures": empty_measures,
            "staves": sorted(staves),
            "density": round(len(non_rest) / len(measures), 2) if measures else 0.0,
        })

    missing_score_parts = sorted({p.get("id") for p in parts} - set(names))
    orphan_score_parts = sorted(set(names) - {p.get("id") for p in parts})

    return {
        "file": os.path.basename(path),
        "part_count": len(parts),
        "part_ids": [p.get("id") for p in parts],
        "part_names": [names.get(p.get("id"), "") for p in parts],
        "duplicate_names": sorted({n for n in names.values() if list(names.values()).count(n) > 1}),
        "missing_score_parts": missing_score_parts,
        "orphan_score_parts": orphan_score_parts,
        "measure_counts": sorted({r["measures"] for r in part_rows}),
        "note_count": sum(r["notes"] for r in part_rows),
        "non_rest_notes": sum(r["non_rest_notes"] for r in part_rows),
        "parts": part_rows,
    }


def cross_page_metrics(pages):
    signatures = {tuple(sorted(p["part_names"])) for p in pages}
    # 按声部名称取并集（part.id 只是页内局部编号，不能当全局主键）
    union_names = sorted({n for p in pages for n in p["part_names"] if n})
    missing = {}
    for idx, p in enumerate(pages, 1):
        present = {n for n in p["part_names"] if n}
        absent = [n for n in union_names if n not in present]
        if absent:
            missing[idx] = absent
    measure_span = {}
    for name in union_names:
        counts = [r["measures"] for p in pages for r in p["parts"] if r["name"] == name]
        if counts:
            measure_span[name] = [min(counts), max(counts)]
    return {
        "page_count": len(pages),
        "structure_signatures": [list(s) for s in signatures],
        "structure_consistent": len(signatures) == 1,
        "union_part_names": union_names,
        "missing_by_page": missing,
        "measure_span_by_name": measure_span,
    }


def merged_metrics(path):
    root = read_root(path)
    names = part_names(root)
    parts = root.findall("part")
    counts = [len(p.findall("measure")) for p in parts]
    rest_only = []
    for p in parts:
        notes = p.findall(".//note")
        if notes and all(n.find("rest") is not None for n in notes):
            rest_only.append(p.get("id"))
    return {
        "file": os.path.basename(path),
        "part_count": len(parts),
        "part_list_count": len(names),
        "part_list_complete": set(names) == {p.get("id") for p in parts},
        "part_names": [names.get(p.get("id"), "") for p in parts],
        "measure_counts": counts,
        "measures_consistent": len(set(counts)) <= 1,
        "note_count": sum(len(p.findall(".//note")) for p in parts),
        "non_rest_notes": sum(len([n for n in p.findall(".//note") if n.find("rest") is None]) for p in parts),
        "rest_only_parts": rest_only,
    }


def render_metrics(path, dpi=100, ink_threshold=200, blank_ratio=1.0):
    import fitz
    import numpy as np

    doc = fitz.open(path)
    rows = []
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=dpi)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        ink = float(((arr[:, :, :3].mean(axis=2) < ink_threshold)).mean() * 100)
        rows.append({"page": i, "ink_ratio": round(ink, 2)})
    doc.close()
    blank = [r["page"] for r in rows if r["ink_ratio"] <= blank_ratio]
    return {"file": os.path.basename(path), "pages": rows, "blank_pages": blank}


def pdf_metrics(path, dpi=300):
    """PDF 层指标：页数、页面尺寸、按目标 DPI 估算像素数、文本层声部标签"""
    import fitz

    doc = fitz.open(path)
    rows = []
    for i, page in enumerate(doc, 1):
        rect = page.rect
        text = page.get_text("text") or ""
        labels = sorted({t for t in ("Pno.", "S.", "A.", "T.", "B.") if t in text})
        rows.append({
            "page": i,
            "width_pt": round(rect.width, 1),
            "height_pt": round(rect.height, 1),
            "pixels_at_dpi": int(rect.width * dpi / 72) * int(rect.height * dpi / 72),
            "text_chars": len(text.strip()),
            "voice_labels": labels,
        })
    doc.close()
    return {
        "file": os.path.basename(path),
        "page_count": len(rows),
        "pages": rows,
        "oversize_pages": [r["page"] for r in rows if r["pixels_at_dpi"] > 20_000_000],
        "scanned_pages": [r["page"] for r in rows if r["text_chars"] < 20],
    }


def log_metrics(path):
    """扫描 OMR 日志，提取环境/引擎类确定性错误特征"""
    patterns = {
        "jvm_memory": ("errno=1455", "OutOfMemoryError", "insufficient memory"),
        "audiveris_crash": ("Audiveris failed", "exit code 1", "code=1"),
    }
    hits = {k: [] for k in patterns}
    files = sorted(glob.glob(os.path.join(path, "**", "*.log"), recursive=True))
    for f in files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue
        for key, needles in patterns.items():
            for needle in needles:
                if needle.lower() in content.lower():
                    hits[key].append(os.path.basename(f))
                    break
    return {
        "log_files": len(files),
        "jvm_memory_files": sorted(set(hits["jvm_memory"])),
        "audiveris_crash_files": sorted(set(hits["audiveris_crash"])),
    }


def main():
    parser = argparse.ArgumentParser(description="转码结果体检（原型）")
    parser.add_argument("--pages", help="每页 MusicXML 所在目录")
    parser.add_argument("--merged", help="合并后的 MusicXML 文件")
    parser.add_argument("--rendered", help="渲染后的 PDF 文件")
    parser.add_argument("--pdf", help="原始 PDF（用于版面/图像质量指标）")
    parser.add_argument("--logs", help="OMR 日志目录")
    parser.add_argument("--out", help="诊断 JSON 输出路径")
    args = parser.parse_args()

    if not any([args.pages, args.merged, args.pdf]):
        parser.error("至少需要 --pages / --merged / --pdf 之一")

    result = {}
    if args.pages:
        files = sorted(
            glob.glob(os.path.join(args.pages, "**", "*.xml"), recursive=True)
            + glob.glob(os.path.join(args.pages, "**", "*.musicxml"), recursive=True)
        )
        if args.merged:
            merged_abs = os.path.abspath(args.merged)
            files = [f for f in files if os.path.abspath(f) != merged_abs]
        if not files:
            print(f"[ERROR] 目录里没有 XML：{args.pages}")
            return 2
        pages = [page_metrics(f) for f in files]
        result["pages"] = pages
        result["cross_page"] = cross_page_metrics(pages)
    if args.merged:
        result["merged"] = merged_metrics(args.merged)
    if args.rendered:
        result["render"] = render_metrics(args.rendered)
    if args.pdf:
        result["pdf"] = pdf_metrics(args.pdf)
    if args.logs:
        result["logs"] = log_metrics(args.logs)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"诊断指标已写入: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
