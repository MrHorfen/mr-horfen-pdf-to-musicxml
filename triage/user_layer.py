"""
文件: user_layer.py
用途: 把后端详细诊断翻译成用户能看懂的 1~2 句提示（用户层 / 技术层严格分离）
调用方: 方案路由人工回放；后续接入 converter_service 的响应组装
输入: router.py 输出的路由 JSON（--route）；病例库 cases.json（--cases）
输出: 用户层 JSON（state/headline/items/actions/feedback）+ 技术层 JSON（完整 findings）
依赖: 标准库
创建时间: 2026-10-22
最后更新: 2026-10-22
更新说明: 初版。定义"后端诊断 → 用户文案"的显式转换规则与上屏白名单
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime

# 用户层禁止出现的词：出现即视为翻译失败，直接报错，避免技术归因泄漏到前端
BANNED_TERMS = [
    "musicxml", "part-list", "part", "audiveris", "dpi", "jvm", "errno",
    "outofmemory", "渲染", "墨迹", "谱表", "识别引擎", "合并层", "结构校验",
    "签名", "多声部", "声部登记", "像素",
]
# 用户层允许的字段（白名单），技术字段一律不得出现在该结构内
USER_KEYS = {"state", "headline", "items", "actions", "feedback"}
ITEM_KEYS = {"page", "text", "actions"}
MAX_SENTENCES = 2
MAX_CHARS = 80

# 同一页问题过多时，按此顺序保留最能指导用户操作的一条
PAGE_PRIORITY = ["C08", "C04", "C05", "C07"]


def _sentences(text):
    return [s for s in re.split(r"[。！？]", text) if s.strip()]


def _pages_count(evidence):
    pages = set(evidence.get("generic_name_pages") or [])
    pages |= set(evidence.get("empty_name_pages") or [])
    return len(pages)


def _page_of(evidence):
    page = evidence.get("page")
    if page is None and evidence.get("pages"):
        first = evidence["pages"][0]
        page = first.get("page") if isinstance(first, dict) else first
    return page


def _format(text, evidence):
    page = _page_of(evidence)
    return (text
            .replace("{page}", str(page if page is not None else "?"))
            .replace("{pages_count}", str(_pages_count(evidence))))


def _validate(text, case_id):
    """用户层文案守门：1~2 句、不超长、不含技术词。违反即拒绝上屏。"""
    if len(_sentences(text)) > MAX_SENTENCES:
        raise ValueError(f"{case_id} 用户文案超过 {MAX_SENTENCES} 句：{text}")
    if len(text) > MAX_CHARS:
        raise ValueError(f"{case_id} 用户文案超过 {MAX_CHARS} 字：{text}")
    low = text.lower()
    for term in BANNED_TERMS:
        if term in low:
            raise ValueError(f"{case_id} 用户文案含技术词“{term}”：{text}")


def _priority(case_id):
    return PAGE_PRIORITY.index(case_id) if case_id in PAGE_PRIORITY else len(PAGE_PRIORITY)


def _make_item(case, evidence):
    text = _format(case["user_message"], evidence)
    _validate(text, case["id"])
    actions = []
    if case.get("user_action"):
        actions.append(_format(case["user_action"], evidence))
    return {"page": _page_of(evidence), "text": text, "actions": actions}


def build_user_view(route, cases, diagnosis_id):
    """路由结果 → 用户负载。技术字段全部剥离，另存技术层。"""
    by_id = {c["id"]: c for c in cases["cases"]}
    findings = route.get("findings", [])
    verdict = route.get("verdict", "healthy")

    visible = []
    for f in findings:
        case = by_id.get(f["case_id"])
        if not case or case.get("user_show", "never") == "never":
            continue
        visible.append((case, f["evidence"], f["severity"]))

    items = []
    if verdict == "blocked":
        # 阻断时用户只能做一件事：重来。只给一条最可执行的说明
        for case, evidence, severity in visible:
            if severity != "blocker":
                continue
            item = _make_item(case, evidence)
            if case.get("user_show") == "global" and not evidence.get("page"):
                item["actions"] = ["重新转换"]
            items.append(item)
            break
        headline = "这次转换没有成功"
        actions = ["重新转换", "更换更清晰的扫描件"]
    elif verdict == "needs_review":
        page_items, global_items = {}, []
        for case, evidence, severity in visible:
            if case.get("user_show") == "page" and _page_of(evidence) is not None:
                page = _page_of(evidence)
                if page not in page_items or _priority(case["id"]) < _priority(page_items[page][0]["id"]):
                    page_items[page] = (case, evidence)
            else:
                global_items.append((case, evidence))
        for page in sorted(page_items):
            items.append(_make_item(*page_items[page]))
        for case, evidence in global_items:
            items.append(_make_item(case, evidence))
        items = items[:3]
        headline = f"转换完成，有 {len(items)} 处建议你确认"
        actions = ["下载"]
    else:
        headline = "转换完成，没有发现明显问题"
        actions = ["下载"]

    user_view = {
        "state": verdict,
        "headline": headline,
        "items": items,
        "actions": actions,
        "feedback": None if verdict == "blocked" else {
            "diagnosis_id": diagnosis_id,
            "prompt": "下载后发现哪里不对？",
            "types": ["整页空白", "缺声部", "声部错位", "音符错乱", "节奏不对", "没问题"],
            "snapshot": "由后端按 diagnosis_id 关联完整诊断快照",
        },
    }
    assert set(user_view) <= USER_KEYS
    for item in user_view["items"]:
        assert set(item) <= ITEM_KEYS
    return user_view


def build_tech_view(route, diagnosis_id):
    return {
        "diagnosis_id": diagnosis_id,
        "verdict": route.get("verdict"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "findings": route.get("findings", []),
    }


def render_preview(user_view):
    lines = [f"【{user_view['headline']}】"]
    for item in user_view["items"]:
        lines.append(f"- {item['text']}")
        if item["actions"]:
            lines.append(f"  动作：{' / '.join(item['actions'])}")
    lines.append(f"全局动作：{' / '.join(user_view['actions'])}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="诊断结果 → 用户层文案翻译")
    parser.add_argument("--route", required=True, help="router.py 输出的路由 JSON")
    parser.add_argument("--cases", default="cases.json", help="病例库 JSON")
    parser.add_argument("--user-out", help="用户层 JSON 输出路径（准备给前端）")
    parser.add_argument("--tech-out", help="技术层 JSON 输出路径（仅后端/日志）")
    args = parser.parse_args()

    with open(args.route, "r", encoding="utf-8") as f:
        route = json.load(f)
    with open(args.cases, "r", encoding="utf-8") as f:
        cases = json.load(f)

    diagnosis_id = uuid.uuid4().hex[:12]
    user_view = build_user_view(route, cases, diagnosis_id)
    print("=== 用户层预览 ===")
    print(render_preview(user_view))

    if args.user_out:
        with open(args.user_out, "w", encoding="utf-8") as f:
            json.dump(user_view, f, ensure_ascii=False, indent=2)
    if args.tech_out:
        with open(args.tech_out, "w", encoding="utf-8") as f:
            json.dump(build_tech_view(route, diagnosis_id), f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
