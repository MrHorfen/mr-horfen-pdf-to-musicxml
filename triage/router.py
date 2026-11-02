"""
文件: router.py
用途: 方案路由原型——把诊断指标匹配到病例库，输出归类结论、处方与用户提示（独立运行，不依赖生产代码）
调用方: 人工回放 / 后续可接入 converter_service
输入: --diag diagnose.py 产出的诊断 JSON；--cases 病例库 cases.json
输出: 控制台诊断报告 + 可选 JSON（--json-out）
依赖: 标准库
创建时间: 2026-10-21
最后更新: 2026-10-21
更新说明: 初版，覆盖 C01-C12
"""

import argparse
import json
import re
import sys

GENERIC_NAMES = {"", "voice", "unknown", "part", "staff", "piano."}


def page_no(name, fallback):
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else fallback


def _each_page(diag, fn):
    return [fn(p, page_no(p["file"], i)) for i, p in enumerate(diag.get("pages", []), 1)]


def match_c01(d):
    merged = d.get("merged")
    if merged and not merged.get("part_list_complete", True):
        return {
            "part_count": merged["part_count"],
            "part_list_count": merged["part_list_count"],
            "part_names": merged.get("part_names", []),
        }
    return None


def match_c02(d):
    render = d.get("render")
    merged = d.get("merged")
    evidence = {}
    if render and render.get("blank_pages"):
        evidence["blank_pages"] = render["blank_pages"]
    if merged is not None and merged.get("non_rest_notes", 1) == 0:
        evidence["merged_non_rest_notes"] = 0
    return evidence or None


def match_c03(d):
    cross = d.get("cross_page")
    if cross and not cross.get("structure_consistent", True):
        counts = [len(s) for s in cross.get("structure_signatures", [])]
        return {
            "page_count": cross.get("page_count"),
            "part_counts_across_pages": counts,
            "signatures": cross.get("structure_signatures"),
        }
    return None


def match_c04(d):
    hits = []
    for p, no in [(p, page_no(p["file"], i)) for i, p in enumerate(d.get("pages", []), 1)]:
        names = [n for n in p.get("part_names", []) if n and n.strip().lower() not in GENERIC_NAMES]
        dup = sorted({n for n in names if names.count(n) >= 2})
        if dup:
            hits.append({"page": no, "duplicate_names": dup, "part_count": p["part_count"]})
    return {"pages": hits} if hits else None


def match_c05(d):
    hits = []
    for p, no in [(p, page_no(p["file"], i)) for i, p in enumerate(d.get("pages", []), 1)]:
        counts = p.get("measure_counts", [])
        if len(counts) > 1:
            hits.append({"page": no, "measure_counts": counts})
    return {"pages": hits} if hits else None


def match_c06(d):
    generic_pages, empty_pages = [], []
    for p, no in [(p, page_no(p["file"], i)) for i, p in enumerate(d.get("pages", []), 1)]:
        names = p.get("part_names", [])
        if any(n and n.strip().lower() in ("voice", "unknown") for n in names):
            generic_pages.append(no)
        if any(not n.strip() for n in names):
            empty_pages.append(no)
    union = {n.strip() for p in d.get("pages", []) for n in p.get("part_names", [])}
    specific = {n for n in union if n and n.strip().lower() not in GENERIC_NAMES}
    mixed_systems = bool(specific) and any(n.strip().lower() in ("voice", "unknown") for n in union)
    if generic_pages or empty_pages or mixed_systems:
        return {
            "generic_name_pages": generic_pages,
            "empty_name_pages": empty_pages,
            "mixed_naming_systems": mixed_systems,
            "names_seen": sorted(union),
        }
    return None


def match_c07(d):
    pdf = d.get("pdf")
    if not pdf:
        return None
    pages = d.get("pages", [])
    by_no = {page_no(p["file"], i): p for i, p in enumerate(pages, 1)}
    hits = []
    for row in pdf.get("pages", []):
        no = row["page"]
        labels = [t for t in row.get("voice_labels", []) if t in ("S.", "A.", "T.", "B.")]
        if not labels:
            continue
        p = by_no.get(no)
        if not p:
            continue
        recognized = len([n for n in p.get("part_names", []) if n and n.strip().lower() not in GENERIC_NAMES])
        recognized -= 1  # 扣掉钢琴声部
        if len(labels) > max(recognized, 0):
            hits.append({"page": no, "labels": labels, "recognized_voice_parts": max(recognized, 0)})
    return {"pages": hits} if hits else None


def match_c08(d):
    hits = []
    for p, no in [(p, page_no(p["file"], i)) for i, p in enumerate(d.get("pages", []), 1)]:
        if p.get("non_rest_notes", 1) == 0:
            hits.append({"page": no, "part_count": p["part_count"]})
    return {"pages": hits} if hits else None


def match_c09(d):
    logs = d.get("logs")
    if logs and logs.get("jvm_memory_files"):
        return {"log_files": logs["jvm_memory_files"]}
    return None


def match_c10(d):
    pdf = d.get("pdf")
    if pdf and pdf.get("oversize_pages"):
        return {"pages": pdf["oversize_pages"]}
    return None


def match_c11(d):
    pdf = d.get("pdf")
    if not pdf:
        return None
    hits = [r["page"] for r in pdf.get("pages", []) if any(t in ("S.", "A.", "T.", "B.") for t in r.get("voice_labels", []))]
    return {"pages": hits} if hits else None


MATCHERS = {
    "C01": match_c01,
    "C02": match_c02,
    "C03": match_c03,
    "C04": match_c04,
    "C05": match_c05,
    "C06": match_c06,
    "C07": match_c07,
    "C08": match_c08,
    "C09": match_c09,
    "C10": match_c10,
    "C11": match_c11,
}


def format_user_message(template, evidence):
    pages = evidence.get("pages")
    page = None
    page_evidence = {}
    if isinstance(pages, list) and pages:
        first = pages[0]
        if isinstance(first, dict):
            page = first.get("page")
            page_evidence = first
        else:
            page = first
    elif isinstance(pages, dict):
        page = next(iter(pages), None)
    merged_evidence = dict(evidence)
    merged_evidence.update(page_evidence)
    return (template
            .replace("{page}", str(page if page is not None else "?"))
            .replace("{counts}", str(merged_evidence.get("measure_counts", "")))
            .replace("{labels}", str(merged_evidence.get("labels", "")))
            .replace("{parts}", str(merged_evidence.get("recognized_voice_parts", ""))))


def route(diag, cases):
    findings = []
    for case in cases["cases"]:
        matcher = MATCHERS.get(case["id"])
        if not matcher:
            continue
        evidence = matcher(diag)
        if not evidence:
            continue
        findings.append({
            "case_id": case["id"],
            "title": case["title"],
            "category": case["category"],
            "severity": case["severity"],
            "evidence": evidence,
            "root_cause": case["root_cause"],
            "prescription": case["prescription"],
            "acceptance": case["acceptance"],
            "user_message": format_user_message(case["user_message"], evidence),
        })
    severities = {f["severity"] for f in findings}
    verdict = "blocked" if "blocker" in severities else ("needs_review" if "warning" in severities else "healthy")
    return {"verdict": verdict, "findings": findings}


def render_text(report):
    label = {"blocked": "阻断交付", "needs_review": "可交付，需复核", "healthy": "通过"}
    lines = ["=== 方案路由诊断（原型） ===", f"结论: {label.get(report['verdict'], report['verdict'])}"]
    if not report["findings"]:
        lines.append("未命中任何已登记的问题签名。")
    for f in report["findings"]:
        lines.append("")
        lines.append(f"[{f['severity'].upper()}] {f['case_id']} {f['title']}（{f['category']}）")
        lines.append(f"  证据: {json.dumps(f['evidence'], ensure_ascii=False)}")
        lines.append(f"  根因: {f['root_cause']}")
        lines.append(f"  处方: {f['prescription']['action']}（{f['prescription']['mode']}）")
        lines.append(f"  验收: {f['acceptance']}")
        lines.append(f"  用户提示: {f['user_message']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="方案路由原型")
    parser.add_argument("--diag", required=True, help="diagnose.py 产出的诊断 JSON")
    parser.add_argument("--cases", default="cases.json", help="病例库 JSON")
    parser.add_argument("--json-out", help="路由结果 JSON 输出路径")
    args = parser.parse_args()

    with open(args.diag, "r", encoding="utf-8") as f:
        diag = json.load(f)
    with open(args.cases, "r", encoding="utf-8") as f:
        cases = json.load(f)

    report = route(diag, cases)
    print(render_text(report))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n路由结果已写入: {args.json_out}")
    return 0 if report["verdict"] != "blocked" else 3


if __name__ == "__main__":
    sys.exit(main())
