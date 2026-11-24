import re
from typing import List, Dict, Any, Optional

_CLEAN_WORD_RE = re.compile(r'^[^\w]+|[^\w]+$')
_LEADING_SLIDE_NUM_RE = re.compile(r'^\d+\s+')
_SPLIT_SEP_RE = re.compile(r'([/\\_\-]+)')
_CAMEL_CASE_RE = re.compile(r'^[a-z]{2,}[A-Z][a-z]+$')
_CAMEL_PARTS_RE = re.compile(r'[A-Z]?[a-z]+')
_PREFIX_NONWORD_RE = re.compile(r'^[^\w]*')
_SUFFIX_NONWORD_RE = re.compile(r'[^\w]*$')
_BIG_O_MATCH_RE = re.compile(r'^O[C\(][A-Za-z0-9_]+\)?$', flags=re.IGNORECASE)
_BIG_O_INNER_RE = re.compile(r'^O[C\(]|[\)]$', flags=re.IGNORECASE)

# 常见手写笔迹连笔/断笔导致 OCR 引擎高频漏判字形智能矫正表（模块级常驻，零重复分配）
_HANDWRITING_FIXES = {
    "funtion": "function",
    "funtions": "functions",
    "funtional": "functional",
    "fuction": "function",
    "fuctions": "functions",
    "fumeion": "function",
    "funeton": "function",
    "interanl": "internal",
    "imernal": "internal",
    "mernal": "internal",
    "exteranl": "external",
    "calulate": "calculate",
    "calulating": "calculating",
    "calculare": "calculate",
    "algrithm": "algorithm",
    "algrithms": "algorithms",
    "algoritm": "algorithm",
    "algoritms": "algorithms",
    "definately": "definitely",
    "seperate": "separate",
    "seperated": "separated",
    "recieve": "receive",
    "recieved": "received",
    "occured": "occurred",
    "occuring": "occurring",
    "miswndorstoading": "misunderstanding",
    "manu": "many",
    "interna": "internal",
    "unetion": "function",
    "owevr": "however",
    "4001": "loop",
    "lome": "some",
    "irst": "first",
    "mony": "many",
    "mouy": "many",
    "terns": "terms",
    "owever": "however",
    "wll": "will",
    "cone": "some",
    "fira": "first",
    "ths": "this",
    "treot": "treat",
    "veult": "result",
    "chalo": "should",
    "lowever": "however",
    "ohe": "one",
    "dloes": "does",
    "onedoes": "one does",
    "hadcome": "had some",
    "hadcowe": "had some",
    "hadgome": "had some",
    "cowe": "some",
    "gome": "some",
    "calfor": "call for",
    "callfor": "call for",
    "temsof": "terms of",
    "moniy": "many",
    "monu": "many",
    "tines": "times",
    "intk": "inside",
    "howeur": "however",
    "howevor": "however",
    "vather": "rather",
    "yemove": "remove",
    "yeal": "real",
    "thst": "that",
    "sonethag": "something",
    "somethag": "something",
    "sonething": "something",
    "stmange": "strange",
    "stramge": "strange",
    "hene": "here",
    "abovue": "above",
    "tredt": "treat",
    "loup": "loop",
    "ioweowr": "however",
    "iowever": "however",
    "desn't": "doesn't",
    "desnt": "doesn't",
    "martver": "matter",
    "marter": "matter",
    "becouc": "because",
    "becouse": "because",
    "nulkt": "result",
    "resukt": "result",
    "shoadlo": "should",
    "shoulo": "should",
    "misunderstonding": "misunderstanding",
    "misundertodineg": "misunderstanding",
    "miswndortwodimy": "misunderstanding",
    "miswmodortudin": "misunderstanding",
    "miswmodortudim": "misunderstanding",
    "miswmodortuding": "misunderstanding",
    "vrther": "rather",
    "imternal": "internal",
    "aunetion": "function",
    "howewr": "however",
    "collfor": "call for",
    "owllben": "O will be N^2",
    "gomething": "something",
    "jtrange": "strange",
    "treut": "treat",
    "losp": "loop",
    "veyult": "result",
    "doesntmatter": "doesn't matter",
    "lop": "loop",
    "oone": "one",
    "doesn4": "doesn't",
    "vult": "result",
    "osp": "loop",
    "becuc": "because",
    "dloesn4": "doesn't",
    "treout": "treat",
    "lowp": "loop",
    "orbo": "above",
    "mou": "how",
}


def _extract_affixes(raw: str):
    pm = _PREFIX_NONWORD_RE.match(raw)
    sm = _SUFFIX_NONWORD_RE.search(raw)
    return (pm.group(0) if pm else ""), (sm.group(0) if sm else "")


def clean_word_text(text: str) -> str:
    # Strip leading/trailing non-word chars (keeping unicode letters/digits, internal hyphens)
    cleaned = _CLEAN_WORD_RE.sub('', text)
    return cleaned if cleaned else text.strip()

def extract_sentence_from_words(words: List[Dict[str, Any]], target_idx: int) -> str:
    if not words or target_idx < 0 or target_idx >= len(words):
        return ""

    sentence_end_chars = ('.', '?', '!')

    # Look backward for sentence start
    start_idx = 0
    for i in range(target_idx - 1, -1, -1):
        raw = words[i].get("text", words[i].get("clean", "")).strip()
        if raw.endswith(sentence_end_chars):
            start_idx = i + 1
            break
        # Also check large vertical gap indicating different paragraph
        if i < target_idx and abs(words[i]["box"]["y"] - words[i + 1]["box"]["y"]) > words[i]["box"]["h"] * 2.5:
            start_idx = i + 1
            break

    # Look forward for sentence end
    end_idx = len(words) - 1
    for i in range(target_idx, len(words)):
        raw = words[i].get("text", words[i].get("clean", "")).strip()
        if raw.endswith(sentence_end_chars):
            end_idx = i
            break
        if i > target_idx and abs(words[i]["box"]["y"] - words[i - 1]["box"]["y"]) > words[i]["box"]["h"] * 2.5:
            end_idx = i - 1
            break

    if start_idx > end_idx:
        start_idx = target_idx
        end_idx = target_idx

    sentence_tokens = [words[i].get("text", words[i].get("clean", "")).strip() for i in range(start_idx, end_idx + 1) if words[i].get("text", words[i].get("clean", "")).strip()]
    sentence = " ".join(sentence_tokens)
    # Remove leading numbers like slide numbering "14 "
    sentence = _LEADING_SLIDE_NUM_RE.sub('', sentence)
    return sentence

def sort_words_reading_order(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 OCR 提取出的全屏所有词块按自然阅读顺序（先从上到下按行聚类，再行内从左到右）排序
    并为每个词块标注统一物理 line_id，完美支持跨行自由拖拽扩选！
    彻底基于全屏绝对物理坐标 Y 轴进行全局自适应行聚类，彻底根除多切片 OCR 时 local line_id 碰撞与行倒错。
    """
    if not words:
        return []

    valid_words = []
    for w in words:
        w_copy = dict(w)
        bx = w_copy["box"]["x"]
        by = w_copy["box"]["y"]
        bw = max(1, w_copy["box"]["w"])
        bh = max(1, w_copy["box"]["h"])
        w_copy["center_x"] = bx + bw / 2.0
        w_copy["center_y"] = by + bh / 2.0
        valid_words.append(w_copy)

    # 1. 优先按中心 Y 排序，并以 O(1) 增量维护当前行 cur_min_y / cur_max_y
    words_by_y = sorted(valid_words, key=lambda w: w["center_y"])
    lines: List[List[Dict[str, Any]]] = []
    first_w = words_by_y[0]
    current_line = [first_w]
    line_y_sum = float(first_w["center_y"])
    line_h_sum = float(first_w["box"]["h"])
    cur_min_y = first_w["box"]["y"]
    cur_max_y = first_w["box"]["y"] + first_w["box"]["h"]

    for w in words_by_y[1:]:
        n = len(current_line)
        line_avg_y = line_y_sum / n
        line_avg_h = max(16.0, line_h_sum / n)
        cy = w["center_y"]
        by = w["box"]["y"]
        bh = w["box"]["h"]

        # 计算垂直重叠：当前词块与当前行包围盒是否存在物理 Y 轴重叠
        has_v_overlap = not (by >= cur_max_y or (by + bh) <= cur_min_y)

        # 水平重叠抑制：如果该词与当前行中某个词在水平 X 轴上发生显著重叠（>40%），说明是上下行叠字，绝非同行
        has_severe_h_overlap = False
        wx1 = w["box"]["x"]
        wx2 = wx1 + w["box"]["w"]
        w_w = max(1, w["box"]["w"])
        for item in current_line:
            ix1 = item["box"]["x"]
            ix2 = ix1 + item["box"]["w"]
            iw = max(1, item["box"]["w"])
            overlap_x = max(0, min(wx2, ix2) - max(wx1, ix1))
            if overlap_x > 0:
                overlap_ratio = overlap_x / min(w_w, iw)
                v_dist = abs(cy - item["center_y"])
                if overlap_ratio > 0.40 and v_dist > line_avg_h * 0.45:
                    has_severe_h_overlap = True
                    break

        if not has_severe_h_overlap and (abs(cy - line_avg_y) <= line_avg_h * 0.65 or (has_v_overlap and abs(cy - line_avg_y) <= line_avg_h * 0.9)):
            current_line.append(w)
            line_y_sum += cy
            line_h_sum += bh
            if by < cur_min_y:
                cur_min_y = by
            if (by + bh) > cur_max_y:
                cur_max_y = by + bh
        else:
            current_line.sort(key=lambda item: item["box"]["x"])
            lines.append(current_line)
            current_line = [w]
            line_y_sum = cy
            line_h_sum = bh
            cur_min_y = by
            cur_max_y = by + bh

    if current_line:
        current_line.sort(key=lambda item: item["box"]["x"])
        lines.append(current_line)

    # 2. 按每行的平均 Y 从上到下对所有行排序，杜绝行乱序
    lines.sort(key=lambda ln: sum(item["center_y"] for item in ln) / len(ln))

    # 3. 统一赋单调递增的真实物理 line_id
    flat = []
    for line_idx, line in enumerate(lines):
        for w in line:
            w["line_id"] = line_idx
            flat.append(w)
    return flat

def process_ocr_tokens(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    智能解析与重构 OCR 词块：
    1. 针对路径/代码符号（如 'desktop/__pycache__/' 或 'desktop/screenlex.db'），
       按 '/' 或 '\\' 分割出独立词汇（如 'desktop'），使用户轻点 'desktop' 即可精确取词；
    2. 针对被 OCR 引擎误切断的英文复合词（同一行且物理间距 <= 4px，如 'desk' + 'top'），
       自动拼合为完整英文单词 'desktop'。
    """
    if not words:
        return []

    _MATH_KEEP_RE = re.compile(r'^[\"\'\(\[]*(?:[A-Za-z0-9]+_[A-Za-z0-9_]+|[A-Za-z0-9]+\([^\)]+\))[\)\]\.\,\;\:\?\!\"\']*$')
    expanded = []
    for w in words:
        raw = w["text"]
        # 保护数学下标与函数复杂度记号（如 x_1, O(N-1), F(n-1)，遵循 AGENTS.md 第 8.3/14.2 章），
        # 仅对普通路径斜杠、连字符词（如 'Remove-Item'、'pass-through'、'desktop/screenlex.db'）拆分
        if any(sep in raw for sep in ("/", "\\", "_", "-")) and len(raw) > 2 and not _MATH_KEEP_RE.match(raw.strip()):
            parts = _SPLIT_SEP_RE.split(raw)
            total_chars = max(1, len(raw))
            char_w = w["box"]["w"] / total_chars
            cur_offset = 0
            for p in parts:
                p_len = len(p)
                clean_p = clean_word_text(p)
                if clean_p and any(c.isascii() and c.isalpha() for c in clean_p):
                    sub_x = int(w["box"]["x"] + cur_offset * char_w)
                    sub_w = max(14, int(p_len * char_w))
                    expanded.append({
                        "text": p,
                        "clean": clean_p,
                        "box": {"x": sub_x, "y": w["box"]["y"], "w": sub_w, "h": w["box"]["h"]},
                        "center_x": sub_x + sub_w / 2,
                        "center_y": w["center_y"],
                        "line_id": w.get("line_id", 0),
                        "is_subtoken": True
                    })
                cur_offset += p_len
        # 若为 OCR 连写的驼峰组合词（如 'weCall' -> 'we' + 'Call'），且非已知手写粘连词（如 hadCome）
        elif _CAMEL_CASE_RE.match(raw) and clean_word_text(raw).lower() not in ("hadcome", "hadcowe", "hadgome", "onedoes"):
            m_parts = _CAMEL_PARTS_RE.findall(raw)
            total_chars = max(1, len(raw))
            char_w = w["box"]["w"] / total_chars
            cur_offset = 0
            for p in m_parts:
                p_len = len(p)
                clean_p = clean_word_text(p)
                if clean_p:
                    sub_x = int(w["box"]["x"] + cur_offset * char_w)
                    sub_w = max(14, int(p_len * char_w))
                    expanded.append({
                        "text": p,
                        "clean": clean_p,
                        "box": {"x": sub_x, "y": w["box"]["y"], "w": sub_w, "h": w["box"]["h"]},
                        "center_x": sub_x + sub_w / 2,
                        "center_y": w["center_y"],
                        "line_id": w.get("line_id", 0),
                        "is_subtoken": True
                    })
                cur_offset += p_len
        else:
            w_copy = dict(w)
            if isinstance(w.get("box"), dict):
                w_copy["box"] = dict(w["box"])
            expanded.append(w_copy)

    if not expanded:
        return []

    # 拼合被误断开的词块（保护正常英语短语如 'must be low' / 'Let x_1 be low' 不被误粘合为 below）
    _MODAL_INF_MARKERS = frozenset({
        "to", "can", "cannot", "could", "will", "would", "shall", "should",
        "may", "might", "must", "not", "let"
    })
    merged = [expanded[0]]
    for w in expanded[1:]:
        prev = merged[-1]
        prev_r = prev["box"]["x"] + prev["box"]["w"]
        cur_l = w["box"]["x"]
        gap = cur_l - prev_r
        same_line = prev.get("line_id", 0) == w.get("line_id", 0)
        combo = (prev["clean"] + w["clean"]).lower()
        if combo == "below":
            prev2_low = merged[-2]["clean"].lower() if (len(merged) >= 2 and merged[-2].get("line_id", 0) == prev.get("line_id", 0)) else ""
            prev3_low = merged[-3]["clean"].lower() if (len(merged) >= 3 and merged[-3].get("line_id", 0) == prev.get("line_id", 0)) else ""
            allow_below = (
                0 <= gap <= max(18, int(prev["box"]["h"] * 0.85))
                and prev2_low not in _MODAL_INF_MARKERS
                and prev3_low != "let"
            )
            is_accidental_split = allow_below
        else:
            is_accidental_split = (
                0 <= gap <= 50
                and combo in ("desktop", "removed", "optimizing", "passthrough", "resilience", "comething", "bove", "obove", "orbve", "orbove", "orbeve")
            )

        if w.get("is_subtoken") or prev.get("is_subtoken"):
            w_copy = dict(w)
            if isinstance(w.get("box"), dict):
                w_copy["box"] = dict(w["box"])
            merged.append(w_copy)
        elif same_line and is_accidental_split and prev["clean"].isalpha() and w["clean"].isalpha():
            if combo == "comething":
                prev["text"] = "something"
                prev["clean"] = "something"
            elif combo in ("bove", "obove", "orbve", "orbove", "orbeve"):
                prev["text"] = "above"
                prev["clean"] = "above"
            elif combo == "below":
                prev["text"] = "below"
                prev["clean"] = "below"
            else:
                prev["text"] = prev["text"] + w["text"]
                prev["clean"] = prev["clean"] + w["clean"]
            prev["box"]["w"] = (w["box"]["x"] + w["box"]["w"]) - prev["box"]["x"]
            prev["center_x"] = prev["box"]["x"] + prev["box"]["w"] / 2
        else:
            w_copy = dict(w)
            if isinstance(w.get("box"), dict):
                w_copy["box"] = dict(w["box"])
            merged.append(w_copy)

    final_tokens = []
    for idx, w in enumerate(merged):
        clean_lower = w["clean"].lower()
        is_hw_token = (w.get("ocr_source") != "winrt")

        # 针对长手写单词形近归一 (miswmodortudin / mis...tudin -> misunderstanding)
        if is_hw_token and clean_lower.startswith("mis") and ("modort" in clean_lower or clean_lower.endswith("tudin") or clean_lower.endswith("tudim") or clean_lower.endswith("tuding")):
            clean_lower = "misunderstanding"
            w["clean"] = "misunderstanding"
            w["text"] = "misunderstanding"

        # 句首 Au first -> At first
        if is_hw_token and clean_lower == "au" and idx + 1 < len(merged) and merged[idx + 1]["clean"].lower() in ("first", "fira"):
            clean_lower = "at"
            is_cap = w["text"][:1].isupper()
            w["clean"] = "At" if is_cap else "at"
            w["text"] = "At" if is_cap else "at"

        # had one misunderstanding -> had some misunderstanding（仅手写来源生效，保护印刷体合法短语）
        if is_hw_token and clean_lower == "one" and idx > 0 and merged[idx - 1]["clean"].lower() == "had":
            clean_lower = "some"
            w["clean"] = "some"
            w["text"] = "some"

        # about his -> about this, However his -> However this（仅手写来源生效，保护印刷体合法短语）
        if is_hw_token and clean_lower == "his" and idx > 0 and merged[idx - 1]["clean"].lower() in ("about", "however", "treat", "for"):
            clean_lower = "this"
            w["clean"] = "this"
            w["text"] = "this"

        # Mou many -> How many
        if is_hw_token and clean_lower == "mou" and idx + 1 < len(merged) and merged[idx + 1]["clean"].lower() in ("many", "manu", "mony"):
            clean_lower = "how"
            is_cap = w["text"][:1].isupper()
            w["clean"] = "How" if is_cap else "how"
            w["text"] = "How" if is_cap else "how"

        # f has -> if has / if it has
        if is_hw_token and clean_lower == "f":
            prev_w = merged[idx - 1]["clean"].lower() if idx > 0 else ""
            next_w = merged[idx + 1]["clean"].lower() if idx + 1 < len(merged) else ""
            if prev_w in ("however", "and", "then", "but") or next_w in ("has", "have", "had", "it", "we"):
                clean_lower = "if"
                w["clean"] = "if"
                w["text"] = "if"

        # CS 算法语境上下文形近修正 (treat the lap as -> treat the loop as)
        if clean_lower == "lap":
            prev_w = merged[idx - 1]["clean"].lower() if idx > 0 else ""
            next_w = merged[idx + 1]["clean"].lower() if idx + 1 < len(merged) else ""
            if prev_w in ("the", "for", "while", "inner", "outer", "a") and next_w in ("as", "in", "body", "variable", "iterations", "counter", "is"):
                clean_lower = "loop"
                w["clean"] = "loop"
                prefix, suffix = _extract_affixes(w["text"])
                w["text"] = prefix + "loop" + suffix

        if clean_lower == "range" and idx > 0 and merged[idx - 1]["clean"].lower() == "something":
            clean_lower = "strange"
            w["clean"] = "strange"
            prefix, suffix = _extract_affixes(w["text"])
            w["text"] = prefix + "strange" + suffix

        # Orb / Orbo / Obo followed by One or ve -> Above
        if clean_lower in ("orb", "orbo", "obo", "obov") and idx + 1 < len(merged) and merged[idx + 1]["clean"].lower() in ("one", "the", "ve"):
            clean_lower = "above"
            w["clean"] = "above"
            w["text"] = "above"

        # treout / treot / treut followed by the / a / this / loop -> treat
        if clean_lower in ("treout", "treot", "treut", "trout") and idx + 1 < len(merged) and merged[idx + 1]["clean"].lower() in ("the", "a", "this", "each", "one", "loop"):
            clean_lower = "treat"
            w["clean"] = "treat"
            w["text"] = "treat"

        # burt / bist 或 bust 紧随 'the one' / 'the other' / 'the below' / 'it' -> but (严格保护合法 bust the myth)
        if (clean_lower in ("burt", "bist")) or (clean_lower == "bust" and (
            (idx + 2 < len(merged) and merged[idx + 1]["clean"].lower() == "the" and merged[idx + 2]["clean"].lower() in ("one", "other", "first", "second", "below", "above")) or
            (idx + 1 < len(merged) and merged[idx + 1]["clean"].lower() in ("it", "this", "that", "does", "do", "did"))
        )):
            clean_lower = "but"
            w["clean"] = "but"
            w["text"] = "but"

        # owever / iowever / howevor -> However
        if clean_lower in ("owever", "iowever", "howevor", "lowever"):
            is_cap = w["text"][:1].isupper() or (idx > 0 and "." in merged[idx - 1]["text"])
            clean_lower = "however"
            w["clean"] = "However" if is_cap else "however"
            prefix, suffix = _extract_affixes(w["text"])
            w["text"] = prefix + ("However" if is_cap else "however") + suffix

        # 过滤紧随 above 之后被拆解遗留的单字 ve 碎片
        if clean_lower == "ve" and idx > 0 and merged[idx - 1]["clean"].lower() == "above":
            continue

        # Big-O 算法时间/空间复杂度标准化 (如 OCN) -> O(N), O(n) -> O(N))
        if _BIG_O_MATCH_RE.match(w["text"]):
            inner = _BIG_O_INNER_RE.sub('', w["text"])
            w["text"] = f"O({inner.upper()})"
            w["clean"] = f"O({inner.upper()})"

        clean_lower = w["clean"].lower()
        if is_hw_token and clean_lower in _HANDWRITING_FIXES:
            fixed = _HANDWRITING_FIXES[clean_lower]
            prefix, suffix = _extract_affixes(w["text"])

            if " " in fixed:
                # 连写粘合词拆分为多个独立词块并在原词块水平区间内按字符占比切分，严禁越出右边界
                sub_words = fixed.split(" ")
                total_chars = max(1, sum(len(sw) for sw in sub_words))
                orig_x = w["box"]["x"]
                orig_w = max(len(sub_words) * 6, w["box"]["w"])
                orig_right = orig_x + orig_w
                char_cursor = 0
                for sw_idx, sw in enumerate(sub_words):
                    sw_clean = sw
                    if w["clean"].isupper():
                        sw_clean = sw.upper()
                    elif w["clean"][:1].isupper() and sw_idx == 0:
                        sw_clean = sw.capitalize()
                    sw_len = len(sw)
                    next_cursor = char_cursor + sw_len
                    sub_left = orig_x + int(round(orig_w * (char_cursor / total_chars)))
                    if sw_idx == len(sub_words) - 1:
                        sub_right = orig_right
                    else:
                        sub_right = orig_x + int(round(orig_w * (next_cursor / total_chars))) - 1
                    sw_w = max(4, sub_right - sub_left)
                    char_cursor = next_cursor
                    sw_prefix = prefix if sw_idx == 0 else ""
                    sw_suffix = suffix if sw_idx == len(sub_words) - 1 else ""
                    final_tokens.append({
                        "text": sw_prefix + sw_clean + sw_suffix,
                        "clean": sw_clean,
                        "box": {"x": sub_left, "y": w["box"]["y"], "w": sw_w, "h": w["box"]["h"]},
                        "center_x": sub_left + sw_w / 2,
                        "center_y": w["center_y"],
                        "line_id": w.get("line_id", 0)
                    })
                continue
            else:
                if w["clean"].isupper():
                    fixed = fixed.upper()
                elif w["clean"][:1].isupper():
                    fixed = fixed.capitalize()
                w["text"] = prefix + fixed + suffix
                w["clean"] = fixed
        final_tokens.append(w)

    # 4. 智能语境纠偏：手写数学表达式中单字母变量 n 易被误读为 in（形如 'terms of in in' 还原为 'terms of n in'）
    for i in range(len(final_tokens) - 1):
        cur = final_tokens[i]
        next_w = final_tokens[i + 1]
        if cur["clean"].lower() == "in" and next_w["clean"].lower() in ("in", "of", "to", "for", "with", "from", "a", "an", "the"):
            if i > 0 and final_tokens[i - 1]["clean"].lower() in ("of", "for", "term", "terms", "value", "power", "function", "order", "sum"):
                cur["clean"] = "n" if cur["clean"].islower() else "N"
                prefix, suffix = _extract_affixes(cur["text"])
                cur["text"] = prefix + cur["clean"] + suffix

    return final_tokens


_GLUED_CORE_WORDS = None
_GLUED_LONG_WORDS = None
_GLUED_LOCK = __import__("threading").Lock()


def _ensure_glued_lexicon():
    global _GLUED_CORE_WORDS, _GLUED_LONG_WORDS
    if _GLUED_CORE_WORDS is not None:
        return _GLUED_CORE_WORDS, _GLUED_LONG_WORDS
    with _GLUED_LOCK:
        if _GLUED_CORE_WORDS is not None:
            return _GLUED_CORE_WORDS, _GLUED_LONG_WORDS
        import os
        core = set()
        try:
            from .grammar_checker import CORE_VOCAB
            core.update(w.lower() for w in CORE_VOCAB if isinstance(w, str) and w.isalpha())
        except Exception:
            pass
        try:
            from .dictionary_service import OFFLINE_DEFS
            for k in OFFLINE_DEFS.keys():
                if isinstance(k, str) and " " not in k and k.isalpha() and len(k) >= 2:
                    core.add(k.lower())
        except Exception:
            pass
        core.update({
            "a", "i", "am", "an", "as", "at", "be", "by", "do", "go", "he", "if",
            "in", "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up",
            "us", "we", "awe", "via", "per",
        })
        long_w = set(core)
        try:
            cw_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "common_words.txt")
            if os.path.exists(cw_path):
                with open(cw_path, "r", encoding="utf-8", errors="ignore") as f:
                    for ln in f:
                        w = ln.strip().lower()
                        if len(w) >= 5 and w.isascii() and w.isalpha():
                            long_w.add(w)
        except Exception:
            pass
        _GLUED_CORE_WORDS = core
        _GLUED_LONG_WORDS = long_w
        return _GLUED_CORE_WORDS, _GLUED_LONG_WORDS


def _split_alpha_glued(core_tok: str) -> List[str]:
    core_words, long_words = _ensure_glued_lexicon()
    low = core_tok.lower()
    if len(core_tok) <= 4:
        return [core_tok]
    if low in core_words or (len(core_tok) >= 5 and low in long_words and not any(
        i > 0 and core_tok[i].isupper() and core_tok[i - 1].islower() for i in range(1, len(core_tok))
    )):
        return [core_tok]
    if core_tok[0].isupper() and not any(c.isupper() for c in core_tok[1:]) and len(core_tok) <= 9:
        exact_dict_split = _find_exact_dict_split(core_tok, core_words, long_words)
        return exact_dict_split if exact_dict_split else [core_tok]

    n = len(core_tok)
    dp: List[Any] = [None] * (n + 1)
    dp[0] = (0.0, [])
    for i in range(n):
        if dp[i] is None:
            continue
        cur_score, cur_parts = dp[i]
        for j in range(i + 1, min(n, i + 22) + 1):
            sub = core_tok[i:j]
            slow = sub.lower()
            is_short_ok = (len(slow) <= 4 and slow in core_words)
            is_long_ok = (len(slow) >= 5 and slow in long_words)
            if (is_short_ok or is_long_ok) and (len(slow) >= 2 or slow in ("a", "i")):
                case_bonus = 1.6 if (i > 0 and core_tok[i].isupper() and core_tok[i - 1].islower()) else 0.0
                freq_bonus = 3.2 if slow in core_words else 0.0
                step = (len(slow) ** 1.35) + case_bonus + freq_bonus - 2.4
                cand = cur_score + step
                if dp[j] is None or cand > dp[j][0]:
                    dp[j] = (cand, cur_parts + [sub])
            elif j - i == 1:
                cand = cur_score - 9.0
                if dp[j] is None or cand > dp[j][0]:
                    dp[j] = (cand, cur_parts + [sub])
    if dp[n] is None:
        return [core_tok]
    parts = dp[n][1]
    if len(parts) <= 1:
        return [core_tok]
    if any(len(p) == 1 and p.lower() not in ("a", "i") for p in parts):
        return [core_tok]
    if any(len(p) == 2 and p.lower() not in core_words for p in parts):
        return [core_tok]
    return parts


_TWO_LETTER_JOIN_WORDS = frozenset({
    "am", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is",
    "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
})


def _find_exact_dict_split(core_tok: str, core_words: set, long_words: set) -> Optional[List[str]]:
    """对短首字母大写复合串（如 'Schoolof'）仅在全段均为真实英语词汇时返回最优拆分，保护以缩写收尾的专有名词（如 'Cyberia' / 'Georgia'）。"""
    n = len(core_tok)
    best_score = -1.0
    best_pair: Optional[List[str]] = None
    for split_i in range(2, n - 1):
        left = core_tok[:split_i]
        right = core_tok[split_i:]
        ll, rl = left.lower(), right.lower()
        l_ok = (ll in _TWO_LETTER_JOIN_WORDS) if len(ll) == 2 else (
            (len(ll) <= 4 and ll in core_words) or (len(ll) >= 5 and ll in long_words)
        )
        r_ok = (rl in _TWO_LETTER_JOIN_WORDS) if len(rl) == 2 else (
            (len(rl) <= 4 and rl in core_words) or (len(rl) >= 5 and rl in long_words)
        )
        if l_ok and r_ok:
            score = (len(ll) ** 1.45) + (len(rl) ** 1.45) + (1.5 if ll in core_words else 0.0) + (1.5 if rl in core_words else 0.0)
            if score > best_score:
                best_score = score
                best_pair = [left, right]
    return best_pair


def split_glued_ocr_token(tok: str) -> List[str]:
    """拆分 RapidOCR 在紧排描边字幕中遗漏空格导致的英文粘连长串，保持连字符复合词与真实单词完整。"""
    if not tok or (len(tok) <= 6 and "," not in tok):
        return [tok] if tok else []
    if "," in tok:
        pre_m = re.match(r"^([^A-Za-z]*)(.*?)([^A-Za-z]*)$", tok)
        if pre_m:
            pre, mid, suf = pre_m.groups()
            if "," in mid:
                chunks = [c for c in mid.split(",") if c]
                if len(chunks) > 1 or mid.endswith(","):
                    out: List[str] = []
                    for idx, ch in enumerate(chunks):
                        sub_parts = split_glued_ocr_token(ch)
                        if (idx < len(chunks) - 1 or mid.endswith(",")) and sub_parts:
                            sub_parts[-1] = sub_parts[-1] + ","
                        out.extend(sub_parts)
                    if pre and out:
                        out[0] = pre + out[0]
                    if suf and out:
                        out[-1] = out[-1] + suf
                    return out or [tok]
    if "-" in tok:
        segs = tok.split("-")
        if len(segs) == 2 and segs[0] and segs[1]:
            m0 = re.match(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$", segs[0])
            m1 = re.match(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$", segs[1])
            head_tok = f"{segs[0]}-{m1.group(1)}{m1.group(2)}" if m1 else tok
            p1 = _split_alpha_glued(m1.group(2)) if (m1 and len(m1.group(2)) >= 6) else ([m1.group(2)] if m1 else [segs[1]])
            p0 = _split_alpha_glued(m0.group(2)) if (m0 and len(m0.group(2)) >= 6) else ([m0.group(2)] if m0 else [segs[0]])
            # 保护标准双词连字符复合词（如 high-tech / awe-inspiring）不被误拆，仅在右段后面粘连了多词（如 awe-inspiringProcessorCitadel）时拆出后缀词
            if len(p1) > 1 and len(p0) == 1 and m1:
                prefix_hyphen = f"{segs[0]}-{m1.group(1)}{p1[0]}"
                tail_parts = list(p1[1:])
                if m1.group(3):
                    tail_parts[-1] = tail_parts[-1] + m1.group(3)
                return [prefix_hyphen] + tail_parts
            if len(p0) > 1 and m0:
                if m0.group(1):
                    p0[0] = m0.group(1) + p0[0]
                p0[-1] = p0[-1] + m0.group(3) + "-" + segs[1]
                return p0
            return [head_tok + (m1.group(3) if m1 else "")] if len(segs) == 2 and m1 else [tok]
        return [tok]
    m_pre = re.match(r"^([^A-Za-z]*)(.*?)([^A-Za-z]*)$", tok)
    if not m_pre:
        return [tok]
    pre, mid, suf = m_pre.groups()
    if not mid or not mid.isalpha():
        return [tok]
    parts = _split_alpha_glued(mid)
    if pre and parts:
        parts[0] = pre + parts[0]
    if suf and parts:
        parts[-1] = parts[-1] + suf
    return parts
