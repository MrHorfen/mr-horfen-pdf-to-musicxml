"""
文件: musicxml_merge.py
用途: 多页 MusicXML 合并的共用结构层——按全局小节序组装、补齐缺失小节、补登记 score-part、
      并在交付前做结构校验，确保不会产出 part-list 为空的非法文件（MuseScore 显示空白页）
调用方: score_converter/piano_pdf_to_musicxml.py、score_converter/piano_multi_pdf_to_musicxml.py
输入: 各分页的 score-partwise 根元素列表（Audiveris 单页导出结果）
输出: 结构合法的单个 score-partwise 根元素；校验失败抛 MusicXMLMergeError（中文可读消息）
依赖: lxml、标准库 copy
创建时间: 2026-10-21
最后更新: 2026-10-21
更新说明: 首次创建。修复跨页 part.id 被当作全局主键导致的声部丢失与空白页问题
"""

from copy import deepcopy

from lxml import etree


# 需要从基准页原样继承的头部元素（版面缩放 scaling 等参数都在 defaults 内）
HEADER_TAGS = (
    "work",
    "movement-number",
    "movement-title",
    "identification",
    "defaults",
    "credit",
)


class MusicXMLMergeError(Exception):
    """合并结果结构非法。消息为面向用户的中文描述，不带技术栈信息。"""


def _direct_parts(root):
    """只取根下的 part（避免 .//part 误抓嵌套同名节点）"""
    return root.findall("part")


def _part_names(root):
    """读取该页 part-list 里的 id → 乐器/声部名称"""
    names = {}
    part_list = root.find("part-list")
    if part_list is None:
        return names
    for sp in part_list.findall("score-part"):
        pid = sp.get("id")
        if pid:
            names[pid] = (sp.findtext("part-name") or "").strip()
    return names


def _page_metrics(parts):
    """取该页的小节数、divisions、拍号（用于生成补齐用的休止小节）"""
    measure_count = max((len(p.findall("measure")) for p in parts), default=0)
    divisions, beats, beat_type = 1, 4, 4
    for p in parts:
        attrs = p.find(".//attributes")
        if attrs is None:
            continue
        d = attrs.findtext("divisions")
        b = attrs.findtext("time/beats")
        bt = attrs.findtext("time/beat-type")
        if d:
            divisions = int(d)
        if b:
            beats = int(b)
        if bt:
            beat_type = int(bt)
        break
    return measure_count, divisions, beats, beat_type


def _rest_measure(number, divisions, beats, beat_type):
    """生成整小节休止，用于补齐某页缺失的声部"""
    measure = etree.Element("measure", number=str(number))
    note = etree.SubElement(measure, "note")
    etree.SubElement(note, "rest", measure="yes")
    duration = max(int(round(divisions * 4 * beats / beat_type)), divisions)
    etree.SubElement(note, "duration").text = str(duration)
    etree.SubElement(note, "voice").text = "1"
    return measure


def _default_attributes(divisions=1):
    """该声部全局都没有 attributes 时的最小可用属性"""
    attrs = etree.Element("attributes")
    etree.SubElement(attrs, "divisions").text = str(max(int(divisions), 1))
    key = etree.SubElement(attrs, "key")
    etree.SubElement(key, "fifths").text = "0"
    time = etree.SubElement(attrs, "time")
    etree.SubElement(time, "beats").text = "4"
    etree.SubElement(time, "beat-type").text = "4"
    clef = etree.SubElement(attrs, "clef")
    etree.SubElement(clef, "sign").text = "G"
    etree.SubElement(clef, "line").text = "2"
    return attrs


def _ensure_first_attributes(out_parts, roots):
    """每个声部的首小节必须有 attributes，否则 MuseScore 会用默认 divisions 画错时值"""
    src_attrs = {}
    default_div = 1
    for root in roots:
        for p in _direct_parts(root):
            pid = p.get("id")
            for m in p.findall("measure"):
                attrs = m.find("attributes")
                if attrs is not None:
                    src_attrs.setdefault(pid, attrs)
                    break
    for root in roots:
        for p in _direct_parts(root):
            for m in p.findall("measure"):
                attrs = m.find("attributes")
                if attrs is not None:
                    if attrs.findtext("divisions"):
                        default_div = attrs.findtext("divisions")
                    break
            break
        break

    for pid, part in out_parts.items():
        measures = part.findall("measure")
        if not measures:
            continue
        first = measures[0]
        if first.find("attributes") is not None:
            continue
        attrs = src_attrs.get(pid)
        first.insert(0, deepcopy(attrs) if attrs is not None else _default_attributes(default_div))


def assemble_pages(roots):
    """按页序把多页 score-partwise 组装成一份结构合法的 score-partwise。

    规则：
    1. 声部集合取所有页的并集，首个出现顺序决定输出顺序；
    2. 每个声部在所有页上都占满等量小节，某页缺该声部时补整小节休止；
    3. 每页都会补登记 score-part——跨页新出现的声部不再是"没有身份证"的 Part；
    4. 小节号按全局顺序统一重编，所有声部严格对齐。
    """
    if not roots:
        raise MusicXMLMergeError("没有可用于合并的分页识别结果")

    base = roots[0]
    out = etree.Element(base.tag, attrib=dict(base.attrib))
    for child in base:
        if child.tag in HEADER_TAGS:
            out.append(deepcopy(child))

    # 声部并集（首见顺序）与名称（任一分页提供即可）
    ids, names = [], {}
    for root in roots:
        for pid, name in _part_names(root).items():
            if name and pid not in names:
                names[pid] = name
        for p in _direct_parts(root):
            pid = p.get("id")
            if pid and pid not in ids:
                ids.append(pid)

    if not ids:
        raise MusicXMLMergeError("识别结果里没有找到任何声部")

    part_list = etree.SubElement(out, "part-list")
    for index, pid in enumerate(ids, start=1):
        sp = etree.SubElement(part_list, "score-part", id=pid)
        etree.SubElement(sp, "part-name").text = names.get(pid) or "Part %d" % index

    out_parts = {}
    for pid in ids:
        out_parts[pid] = etree.SubElement(out, "part", id=pid)

    counter = 0
    for root in roots:
        parts = _direct_parts(root)
        count, divisions, beats, beat_type = _page_metrics(parts)
        by_id = {p.get("id"): p for p in parts}
        for index in range(count):
            counter += 1
            for pid in ids:
                src = by_id.get(pid)
                src_measures = src.findall("measure") if src is not None else []
                if index < len(src_measures):
                    measure = deepcopy(src_measures[index])
                else:
                    measure = _rest_measure(counter, divisions, beats, beat_type)
                measure.set("number", str(counter))
                out_parts[pid].append(measure)

    _ensure_first_attributes(out_parts, roots)
    return out


def validate_merged(root, title="合并结果"):
    """交付前结构校验；不合法直接抛 MusicXMLMergeError（消息面向用户）"""
    part_list = root.find("part-list")
    ids = [sp.get("id") for sp in part_list.findall("score-part")] if part_list is not None else []
    parts = _direct_parts(root)
    part_ids = [p.get("id") for p in parts]

    if not ids or not part_ids:
        raise MusicXMLMergeError(
            "%s里没有任何声部信息，这份谱很可能没有被正确识别。请更换更清晰的扫描件，"
            "或勾选“多声部”模式后重新转换。" % title
        )

    missing = [pid for pid in part_ids if pid not in ids]
    orphan = [pid for pid in ids if pid not in part_ids]
    if missing or orphan:
        raise MusicXMLMergeError(
            "%s的声部登记不完整（有 %d 个声部没有对应的乐器信息），继续输出会得到空白谱面。"
            "请勾选“多声部”模式重新转换；若仍失败，请更换更清晰的扫描件。" % (title, len(missing) or len(orphan))
        )

    counts = {p.get("id"): len(p.findall("measure")) for p in parts}
    if len(set(counts.values())) > 1:
        detail = "、".join("%s=%d" % (pid, n) for pid, n in counts.items())
        raise MusicXMLMergeError(
            "%s的各声部小节数不一致（%s），无法对齐成一份完整乐谱。"
            "通常是某一页的声部识别分组异常，请勾选“多声部”模式重试，或更换扫描件。"
            % (title, detail)
        )

    total_notes = sum(1 for n in root.iter("note") if n.find("rest") is None)
    if total_notes == 0:
        raise MusicXMLMergeError(
            "%s没有识别出任何音符。常见原因是扫描件太模糊、分辨率过低或谱线间距过小，"
            "请更换更清晰的乐谱后重试。" % title
        )
    return counts
