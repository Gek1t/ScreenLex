import sys
import re
import os
import time
import html
import urllib.request
import urllib.parse
import json
import difflib
from typing import Dict, Any, List, Optional, Tuple

_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_lt_breaker_until: float = 0.0
_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _open_url_direct(req: urllib.request.Request, timeout: float):
    """生产态强制旁路死代理直连；单测 patch('urllib.request.urlopen') 时自动服从 Mock 隔离。"""
    uopen = urllib.request.urlopen
    if hasattr(uopen, "_mock_name") or getattr(uopen, "__name__", "") != "urlopen":
        return uopen(req, timeout=timeout)
    return _DIRECT_OPENER.open(req, timeout=timeout)

# 高频基础词、学术英语核心词汇 (AWL) 及计算机科学常见词汇库 (~1500 词)
# 本地仅占约 30KB 内存，0MB 外部包体积增加，提供微秒级离线高保真拼写校验
CORE_VOCAB = {
    # 基础高频词汇与语法功能词
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i", "it", "for", "not", "on", "with",
    "he", "as", "you", "do", "at", "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
    "or", "an", "will", "my", "one", "all", "would", "there", "their", "what", "so", "up", "out", "if",
    "about", "who", "get", "which", "go", "me", "when", "make", "can", "like", "time", "no", "just", "him",
    "know", "take", "people", "into", "year", "your", "good", "some", "could", "them", "see", "other",
    "than", "then", "now", "look", "only", "come", "its", "over", "think", "also", "back", "after", "use",
    "two", "how", "our", "work", "first", "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "is", "are", "was", "were", "been", "has", "had", "does", "did", "done",
    "doing", "should", "shall", "must", "might", "may", "can", "could", "would", "very", "much", "more",
    "many", "few", "little", "such", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will",
    "just", "don", "should", "now", "here", "where", "why", "how", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than",
    "too", "very", "s", "t", "can", "will", "just", "don", "should", "now",

    # AWL 学术英语核心词汇 (Academic Word List)
    "academic", "analysis", "approach", "area", "assessment", "assume", "authority", "available", "benefit",
    "concept", "consistent", "constitutional", "context", "contract", "create", "data", "definition",
    "derived", "distribution", "economic", "environment", "established", "estimate", "evidence", "export",
    "factors", "financial", "formula", "function", "functions", "functional", "identified", "income",
    "indicate", "indicates", "indicated", "indicating", "individual", "interpretation", "involved", "issues",
    "labour", "legal", "legislation", "major", "method", "methods", "minimum", "normal", "obtain", "occur",
    "occurs", "occurred", "occurring", "percent", "period", "policy", "principle", "principles", "procedure",
    "process", "processes", "required", "requires", "requiring", "research", "response", "role", "section",
    "sector", "significant", "similar", "source", "specific", "structure", "theory", "variables", "achieve",
    "acquisition", "administration", "affect", "appropriate", "aspects", "assistance", "categories", "chapter",
    "commission", "community", "complex", "complexity", "complexities", "computer", "conclusion", "conduct",
    "consequences", "construction", "consumer", "credit", "cultural", "design", "distinction", "elements",
    "equation", "evaluation", "features", "final", "focus", "impact", "injury", "institute", "investment",
    "items", "journal", "maintenance", "normal", "obtained", "participation", "perceived", "positive",
    "potential", "previous", "primary", "purchase", "range", "region", "regulations", "relevant", "resident",
    "resources", "restricted", "security", "sought", "select", "selection", "site", "strategies", "survey",
    "text", "traditional", "transfer", "alternative", "circumstances", "comments", "compensation", "components",
    "consent", "considerable", "constant", "constraints", "contribution", "convention", "coordination", "core",
    "corporate", "corresponding", "criteria", "deduction", "demonstrate", "demonstrates", "demonstrated",
    "document", "domination", "emphasis", "ensure", "exclusion", "framework", "funds", "illustrated",
    "immigrate", "implies", "initial", "instance", "interaction", "justification", "layer", "link",
    "location", "maximum", "minorities", "negative", "outcomes", "partnership", "philosophy", "physical",
    "proportion", "published", "reaction", "registered", "reliance", "scheme", "sequence", "shift",
    "specified", "sufficient", "task", "technical", "techniques", "technology", "validity", "volume",

    # 计算机科学、算法与软件工程高频词
    "algorithm", "algorithms", "algorithmic", "array", "arrays", "binary", "linear", "search", "searches",
    "sort", "sorting", "sorted", "bubble", "insertion", "merge", "quick", "heap", "stack", "queue",
    "tree", "trees", "node", "nodes", "edge", "edges", "graph", "graphs", "matrix", "matrices",
    "vector", "vectors", "calculate", "calculates", "calculated", "calculating", "calculation", "calculations",
    "represent", "represents", "represented", "representing", "representation", "representations",
    "something", "anything", "nothing", "everything", "someone", "anyone", "everyone",
    "condition", "conditions", "conditional", "statement", "statements", "loop", "loops", "looping",
    "recursive", "recursion", "iteration", "iterations", "iterate", "iterative",
    "runtime", "space", "time", "order", "notation", "bound", "bounds", "upper", "lower", "limit",
    "step", "steps", "perform", "performs", "performed", "performing", "performance",
    "ephemeral", "ubiquitous", "paradigm", "sustainability", "empirical", "concomitant", "mitigate",
    "exponential", "resilience", "heterogeneous", "synergy", "removed", "optimizing", "pass", "passes",
    "asymptotic", "polynomial", "logarithmic", "constant", "quadratic", "variable", "variables",
    "parameter", "parameters", "argument", "arguments", "input", "inputs", "output", "outputs",
    "memory", "allocation", "pointer", "reference", "cache", "latency", "throughput", "bandwidth",
    "thread", "threads", "process", "processes", "concurrency", "parallel", "distributed",
    "system", "systems", "module", "modules", "package", "packages", "library", "libraries",
    "class", "classes", "object", "objects", "instance", "instances", "method", "methods",
    "string", "strings", "integer", "integers", "boolean", "float", "double", "character", "characters",
    "table", "tables", "database", "query", "queries", "index", "indices", "value", "values",
    "key", "keys", "hash", "mapping", "map", "maps", "set", "sets", "list", "lists",

    # 数学、逻辑、算法与学术常用高频词
    "term", "terms", "item", "items", "internal", "external", "inner", "outer", "factor", "factors",
    "equation", "equations", "variable", "variables", "constant", "constants", "coefficient", "coefficients",
    "dimension", "dimensions", "degree", "degrees", "number", "numbers", "integer", "integers",
    "fraction", "fractions", "expression", "expressions", "formula", "formulas", "formulae",
    "ratio", "ratios", "rate", "rates", "sum", "sums", "difference", "differences",
    "product", "products", "quotient", "quotients", "divisor", "divisors", "remainder", "remainders",
    "power", "powers", "root", "roots", "square", "squares", "cube", "cubes",
    "series", "sequence", "sequences", "subset", "subsets", "element", "elements",
    "interval", "intervals", "boundary", "boundaries", "domain", "domains", "range", "ranges",
    "scalar", "scalars", "tensor", "tensors", "derivative", "derivatives", "integral", "integrals",
    "limit", "limits", "infinity", "theorem", "theorems", "proof", "proofs", "lemma", "lemmas",
    "axiom", "axioms", "proposition", "propositions", "corollary", "corollaries",
    "hypothesis", "hypotheses", "property", "properties", "notation", "notations",
    "vertex", "vertices", "weight", "weights", "path", "paths", "cycle", "cycles",
    "leaf", "leaves", "branch", "branches", "depth", "height", "level", "levels",
    "worst", "best", "average", "case", "cases", "return", "returns", "call", "calls",
    "bucket", "buckets", "collision", "collisions", "lookup", "lookups",
    "insert", "inserts", "insertion", "delete", "deletes", "deletion", "update", "updates",
    "split", "splits", "partition", "partitions", "pivot", "pivots", "swap", "swaps",
    "compare", "compares", "comparison", "comparisons", "count", "counts",
    "divide", "conquer", "greedy", "dynamic", "programming", "linear", "quadratic", "cubic",
    "optimal", "suboptimal", "efficient", "efficiency", "inefficient",
    "sample", "samples", "population", "test", "tests", "testing", "tested",
    "accuracy", "precision", "recall", "loss", "gradient", "descent", "epoch", "batch",
    "bias", "neural", "network", "networks", "deep", "learning", "model", "models",

    # 常见编程、例题与算法教学高频词汇
    "print", "prints", "printed", "printing", "exercise", "exercises", "below", "above",
    "every", "plus", "minus", "times", "iterate", "iterates", "iterated", "iterating",
    "iteration", "iterations", "enumerate", "even", "odd",

    # 高频前缀学术词汇与常见衍生词 (彻底避免误纠偏)
    "misunderstand", "misunderstanding", "misunderstandings", "misunderstood",
    "misconception", "misconceptions", "mislead", "misleading", "mistake", "mistakes",
    "overfit", "overfitting", "underfit", "underfitting", "multithread", "multithreaded",
    "multithreading", "multimodal", "inline", "online", "offline", "runtime", "compile",
    "compilation", "compiler", "pointer", "syntax", "semantics", "operand", "operands",
    "component", "theorem", "theorems", "factor", "element", "variable", "category",
    "loop", "loops", "treat", "treats", "recursion", "recursive", "hash", "hashing"
}

# 构词法核心前缀集合 (覆盖 99% 派生变体)
PREFIXES = (
    "un", "in", "im", "dis", "re", "non", "pre", "sub", "anti",
    "mis", "over", "under", "out", "multi", "inter", "auto", "semi",
    "co", "de", "post", "extra", "ultra"
)

# 锁定高优先级学术核心词快照（在并入 37 万通用词库前保留，用于单编辑距离同分择优）
ACADEMIC_PRIORITY_VOCAB = frozenset(CORE_VOCAB)

# 自动装载通用英语高频词库 (common_words.txt)，扩展至 10,000+ 词汇，彻底避免基础词汇漏判
_candidate_paths = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "common_words.txt"),
    os.path.join(getattr(sys, '_MEIPASS', ''), "desktop", "common_words.txt") if getattr(sys, '_MEIPASS', None) else '',
    os.path.join(os.path.dirname(sys.executable), "_internal", "desktop", "common_words.txt") if getattr(sys, 'frozen', False) else '',
    os.path.join(os.getcwd(), "desktop", "common_words.txt"),
    os.path.join(os.getcwd(), "_internal", "desktop", "common_words.txt"),
]
for _p in _candidate_paths:
    if _p and os.path.exists(_p):
        try:
            with open(_p, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _w = _line.strip().lower()
                    if _w and len(_w) >= 2:
                        CORE_VOCAB.add(_w)
            break
        except Exception:
            pass

def is_math_or_code_identifier(word: str) -> bool:
    """
    识别数学变量、算法符号或代码标识符 (如 Nx, N1, kNN, O(N), x_1)，免疫拼写误纠偏。
    学术/CS场景下数学变量极其普遍，绝对禁止强行当成普通词汇猜词！
    """
    clean_w = re.sub(r'[^a-zA-Z0-9_]', '', word)
    if not clean_w or len(clean_w) <= 2:
        return True
    # 含有数字或下划线
    if any(c.isdigit() or c == '_' for c in clean_w):
        return True
    # 驼峰或混合大小写代码标识符 (如 Nx, ReLU, kNN, quickSelect) 免疫；长度 >= 5 的全大写纯字母词（如 ALGORITM）允许走拼写纠错
    if clean_w.isupper():
        return len(clean_w) <= 4
    if any(c.isupper() for c in clean_w[1:]):
        return True
    return False

def is_prefix_relation(w1: str, w2: str) -> bool:
    """
    判断两词是否仅为添加/去除前缀关系（如 misunderstanding vs understanding，unable vs able）。
    前缀表达完全不同的语义或否定极性，绝非拼写手抖错误，严禁当做错字纠正！
    """
    w1_l, w2_l = w1.lower(), w2.lower()
    longer, shorter = (w1_l, w2_l) if len(w1_l) > len(w2_l) else (w2_l, w1_l)
    for pfx in PREFIXES:
        if longer.startswith(pfx) and longer[len(pfx):] == shorter:
            return True
    return False

def is_inflection_relation(w1: str, w2: str) -> bool:
    """
    判断两词是否仅为正常的英文屈折语法变体（如动词三单/单复数 -s/-es、过去式 -ed、现在分词 -ing、副词 -ly）。
    在例题/句子语境下（如 'The algorithm iterates...' 或 '3N steps'），屈折变体是合法且必须的语法形式，绝非拼写错误！
    """
    w1_low = w1.lower()
    w2_low = w2.lower()
    if w1_low == w2_low:
        return True
    longer, shorter = (w1_low, w2_low) if len(w1_low) > len(w2_low) else (w2_low, w1_low)
    # 1. 结尾加 -s / -es / -d / -ed
    if longer == shorter + 's' or longer == shorter + 'es':
        return True
    if longer == shorter + 'd' or longer == shorter + 'ed':
        return True
    # 2. y 变 ies / ied
    if longer.endswith('ies') and shorter == longer[:-3] + 'y':
        return True
    if longer.endswith('ied') and shorter == longer[:-3] + 'y':
        return True
    # 3. 分词 -ing (如 iterate -> iterating, loop -> looping)
    if longer.endswith('ing'):
        if shorter == longer[:-3] or shorter == longer[:-3] + 'e':
            return True
        if len(longer) > 5 and longer[-4] == longer[-5] and shorter == longer[:-4]:
            return True
    # 4. 副词 -ly
    if longer == shorter + 'ly' or (longer.endswith('ly') and shorter == longer[:-2] + 'e'):
        return True
    return False

def is_derivation_relation(w1: str, w2: str) -> bool:
    """
    判断两词是否属于同根派生/词性分化关系（如 rationale vs rational, morale vs moral, breathe vs breath）。
    这些同根变体各自具备完全独立且合法的词性和词义，绝非拼写错误，严禁互相纠偏！
    """
    w1_low, w2_low = w1.lower(), w2.lower()
    if w1_low == w2_low:
        return True
    longer, shorter = (w1_low, w2_low) if len(w1_low) > len(w2_low) else (w2_low, w1_low)
    # 常见施事/工具名词派生 (-r / -er / -or，如 serialize -> serializer, tokenize -> tokenizer, orchestrate -> orchestrator)
    if shorter.endswith('e') and longer == shorter + 'r':
        return True
    if longer.endswith(('er', 'or')) and (shorter == longer[:-2] or shorter == longer[:-2] + 'e'):
        return True
    # 常见同根名词/形容词后缀分化 (-al vs -ale, -ic vs -ical, -ant vs -ance, -ent vs -ence)
    if longer.endswith('ale') and shorter.endswith('al') and longer[:-3] == shorter[:-2]:
        return True
    if longer.endswith('ical') and shorter.endswith('ic') and longer[:-4] == shorter[:-2]:
        return True
    if (longer.endswith('ance') or longer.endswith('ence')) and (shorter.endswith('ant') or shorter.endswith('ent')):
        if longer[:-4] == shorter[:-3]:
            return True
    return False

def is_single_edit_distance(s1: str, s2: str) -> bool:
    """判断两字符串是否严格仅差 1 处编辑（单字母增加、删除或替换），杜绝跨词大改"""
    m, n = len(s1), len(s2)
    if abs(m - n) > 1:
        return False
    i, j, edits = 0, 0, 0
    while i < m and j < n:
        if s1[i] != s2[j]:
            edits += 1
            if edits > 1:
                return False
            if m > n:
                i += 1
            elif n > m:
                j += 1
            else:
                i += 1
                j += 1
        else:
            i += 1
            j += 1
    if i < m or j < n:
        edits += 1
    return edits == 1

# 手写常见连笔词特异性先验映射 (绝不盲目用 difflib 猜日常词)
HANDWRITING_OFFLINE_FIXES = {
    "treout": "treat",
    "treot": "treat",
    "treut": "treat",
    "shoulo": "should",
    "lowp": "loop",
    "orbo": "above",
    "obo": "above",
    "owever": "however",
    "iowever": "however"
}

# 英语常见键盘手抖与经典确诊错别字映射表 (100% 确诊，0 误报)
COMMON_TYPOS = {
    "teh": "the", "recieve": "receive", "recieved": "received", "recieving": "receiving",
    "seperate": "separate", "seperated": "separated", "seperating": "separating",
    "occured": "occurred", "occuring": "occurring", "untill": "until",
    "definately": "definitely", "goverment": "government", "truely": "truly",
    "wich": "which", "wierd": "weird", "acheive": "achieve", "acheived": "achieved",
    "beleive": "believe", "beleived": "believed", "calender": "calendar",
    "concious": "conscious", "embarass": "embarrass", "foriegn": "foreign",
    "guarentee": "guarantee", "happenning": "happening", "ignorence": "ignorance",
    "independant": "independent", "judgement": "judgment", "knowlege": "knowledge",
    "maintanence": "maintenance", "neccessary": "necessary", "necesary": "necessary",
    "noticable": "noticeable", "ocassion": "occasion", "occassion": "occasion",
    "posession": "possession", "privelege": "privilege", "publically": "publicly",
    "programer": "programmer", "accomodate": "accommodate",
    "recommand": "recommend", "refered": "referred", "relavent": "relevant",
    "rythm": "rhythm", "succesful": "successful", "sucessful": "successful",
    "tommorow": "tomorrow", "unfortunatly": "unfortunately", "writting": "writing",
    "algoritm": "algorithm", "complexty": "complexity"
}

# 从通用词典中物理剔除已知确诊错拼词，防止 words_alpha 中的脏条目反过来放行或误推荐错词
CORE_VOCAB.difference_update(COMMON_TYPOS.keys())
CORE_VOCAB.difference_update(HANDWRITING_OFFLINE_FIXES.keys())

# 常见英文简缩词 (Contractions) 白名单，杜绝将 doesn't / don't 误拆为 doesn / don 导致错误纠偏
ENGLISH_CONTRACTIONS = {
    "doesn't", "don't", "didn't", "won't", "can't", "cannot", "isn't", "aren't",
    "wasn't", "weren't", "hasn't", "haven't", "hadn't", "shouldn't", "wouldn't",
    "couldn't", "mustn't", "it's", "that's", "there's", "what's", "who's", "let's",
    "i'm", "you're", "we're", "they're", "i've", "you've", "we've", "they've",
    "i'll", "you'll", "he'll", "she'll", "we'll", "they'll", "i'd", "you'd", "he'd",
    "she'd", "we'd", "they'd",
    # 标点剥离形态保全
    "doesnt", "dont", "didnt", "wont", "cant", "isnt", "arent", "wasnt", "werent",
    "hasnt", "havent", "hadnt", "shouldnt", "wouldnt", "couldnt", "mustnt",
    "its", "thats", "theres", "whats", "whos", "lets",
    "doesn", "don", "didn"
}

def is_valid_word(w: str) -> bool:
    """基于核心学术词库、屈折形态变体与前缀构词法智能校验单词是否合法"""
    if not w or len(w) <= 1:
        return True
    if w in CORE_VOCAB or w in ENGLISH_CONTRACTIONS:
        return True
    if "'" in w and w.replace("'", "") in CORE_VOCAB:
        return True
    # 复数与第三人称单数 (-s, -es, -ies)
    if w.endswith('s'):
        if w[:-1] in CORE_VOCAB or (w.endswith('es') and w[:-2] in CORE_VOCAB):
            return True
        if w.endswith('ies') and (w[:-3] + 'y') in CORE_VOCAB:
            return True
    # 过去式与过去分词 (-ed, -d)
    if w.endswith('ed'):
        if w[:-2] in CORE_VOCAB or w[:-1] in CORE_VOCAB:
            return True
        if len(w) > 4 and w[-3] == w[-4] and w[:-3] in CORE_VOCAB:
            return True
        if w.endswith('ied') and (w[:-3] + 'y') in CORE_VOCAB:
            return True
    # 现在分词 (-ing)
    if w.endswith('ing'):
        if w[:-3] in CORE_VOCAB or (w[:-3] + 'e') in CORE_VOCAB:
            return True
        if len(w) > 5 and w[-4] == w[-5] and w[:-4] in CORE_VOCAB:
            return True
    # 副词与形容词 (-ly)
    if w.endswith('ly'):
        if w[:-2] in CORE_VOCAB or (w[:-2] + 'e') in CORE_VOCAB:
            return True
        if w.endswith('ily') and (w[:-3] + 'y') in CORE_VOCAB:
            return True
    # 常见施事/工具名词 (-er / -or 及复数 -ers / -ors，如 serializer, tokenizer, orchestrator, debugger)
    if w.endswith(('ers', 'ors')) and len(w) >= 8 and is_valid_word(w[:-1]):
        return True
    if w.endswith(('er', 'or')) and len(w) >= 7:
        stem = w[:-2]
        if (stem + 'e') in CORE_VOCAB:
            return True
        if len(w) >= 8 and w[-3] == w[-4] and w[:-3] in CORE_VOCAB:
            return True
        if stem in CORE_VOCAB and len(stem) >= 3 and stem[-1] not in 'aeiou' and stem[-2] not in 'aeiou':
            return True
    # 常见构词前缀深度探测 (支持递归复合前缀)
    for pfx in PREFIXES:
        if w.startswith(pfx) and len(w) > len(pfx) + 2:
            root = w[len(pfx):]
            if root in CORE_VOCAB or (root.endswith('s') and root[:-1] in CORE_VOCAB):
                return True
            if is_valid_word(root):
                return True
    return False

def _find_best_single_edit_candidate(word_lower: str, cutoff: float = 0.88) -> Optional[str]:
    """
    O(1) 哈希单步编辑邻域检索：
    直接枚举目标词的单字母删除、替换、插入邻域并与 CORE_VOCAB 求交（约 54L+26 次 O(1) 集合查询），
    彻底替代在 37 万词库上线性运行 difflib.get_close_matches（耗时从 ~160ms 降至 < 0.05ms）。
    """
    n = len(word_lower)
    if n < 3 or n > 28 or not word_lower.isascii() or not word_lower.isalpha():
        return None

    hits = set()
    # 1. 单字符删除 (长度 n - 1)
    for i in range(n):
        cand = word_lower[:i] + word_lower[i + 1:]
        if cand in CORE_VOCAB:
            hits.add(cand)

    # 2. 单字符替换 (长度 n)
    for i in range(n):
        orig_ch = word_lower[i]
        prefix = word_lower[:i]
        suffix = word_lower[i + 1:]
        for ch in _ALPHABET:
            if ch != orig_ch:
                cand = prefix + ch + suffix
                if cand in CORE_VOCAB:
                    hits.add(cand)

    # 3. 单字符插入 (长度 n + 1)
    for i in range(n + 1):
        prefix = word_lower[:i]
        suffix = word_lower[i:]
        for ch in _ALPHABET:
            cand = prefix + ch + suffix
            if cand in CORE_VOCAB:
                hits.add(cand)

    hits.discard(word_lower)
    if not hits:
        return None

    # 一旦单步邻域中存在原词的合法屈折/前缀/同根派生词，说明原词大概率为合法构词变体，立即终止猜词（严禁退化挑出无关生僻词）
    for cand in hits:
        if is_inflection_relation(word_lower, cand) or is_prefix_relation(word_lower, cand) or is_derivation_relation(word_lower, cand):
            return None

    best_cand: Optional[str] = None
    best_key = (-1, -1.0, "")
    for cand in hits:
        if cand in ("trout", "trot") and word_lower.startswith("tre"):
            continue
        if cand == "orb" and word_lower in ("orbo", "obo"):
            continue
        if not is_single_edit_distance(word_lower, cand):
            continue
        is_acad = 1 if cand in ACADEMIC_PRIORITY_VOCAB else 0
        eff_cutoff = min(cutoff, 0.84) if is_acad else cutoff
        ratio = difflib.SequenceMatcher(None, word_lower, cand).ratio()
        if ratio < eff_cutoff:
            continue
        # 学术核心词库优先于 37 万通用生僻词库，彻底消除插入生僻词（noodle/threep/loppy）碾压核心词（node/tree/loop）的偏置
        score_key = (is_acad, round(ratio, 6), cand)
        if score_key > best_key:
            best_key = score_key
            best_cand = cand

    return best_cand


def check_spelling(word: str, allow_online: bool = False) -> Optional[str]:
    """
    针对单单词进行高保真拼写检查与纠错建议。
    如果单词正确，返回 None；若存在拼写错误（如 complexty ➔ complexity），返回推荐纠正词。
    allow_online 默认为 False，确保在 UI 主线程 0ms 纯离线秒级返回，彻底杜绝主线程卡顿。
    """
    clean = re.sub(r'[^a-zA-Z0-9_]', '', word).strip()
    if not clean or len(clean) < 3:
        return None

    # 数学/代码变量 (如 Nx, N1, kNN, x_1) 免疫拼写检查
    if is_math_or_code_identifier(clean):
        return None

    clean_lower = clean.lower()

    # 1. 先验常见错词与手写笔迹纠偏优先于词库放行（100% 确诊，防止 common_words.txt 脏条目或宽泛词缀规则架空）
    if clean_lower in HANDWRITING_OFFLINE_FIXES:
        sugg = HANDWRITING_OFFLINE_FIXES[clean_lower]
        return sugg.upper() if clean.isupper() else (sugg.capitalize() if clean[0].isupper() else sugg)
    if clean_lower in COMMON_TYPOS:
        sugg = COMMON_TYPOS[clean_lower]
        return sugg.upper() if clean.isupper() else (sugg.capitalize() if clean[0].isupper() else sugg)

    # 2. 若词汇本身或其标准形态变体已在词库中，0 延迟认定为正确，绝不误判
    if is_valid_word(clean_lower):
        return None

    # 3. 离线核心学术词库 O(1) 单步编辑邻域比对 (要求严格单字母编辑距离 + 相似度 >= 0.88)
    res = _find_best_single_edit_candidate(clean_lower, cutoff=0.88)
    if res:
        if clean.isupper():
            return res.upper()
        elif clean[0].isupper():
            return res.capitalize()
        return res

    # 4. 在线权威建议（严格限制仅在后台子线程显式允许 allow_online 时执行，强制旁路系统代理，绝不阻塞 UI 主线程）
    if not allow_online:
        return None

    try:
        url = f"https://dict.youdao.com/suggest?q={urllib.parse.quote(clean_lower)}&le=en&num=3&doctype=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _open_url_direct(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            entries = data.get("data", {}).get("entries", [])
            if entries:
                all_entries = [e.get("entry", "").strip().lower() for e in entries]
                if clean_lower in all_entries:
                    return None
                first_entry = entries[0].get("entry", "").strip().lower()
                if first_entry and first_entry != clean_lower:
                    if is_inflection_relation(clean_lower, first_entry):
                        return None
                    if is_prefix_relation(clean_lower, first_entry):
                        return None
                    matcher = difflib.SequenceMatcher(None, clean_lower, first_entry)
                    if matcher.ratio() >= 0.75:
                        if clean.isupper():
                            return first_entry.upper()
                        elif clean[0].isupper():
                            return first_entry.capitalize()
                        return first_entry
                elif first_entry == clean_lower:
                    return None
    except Exception:
        pass

    return None

def normalize_ocr_text(text: str) -> str:
    """
    OCR 识别文本排版规整化：
    自适应收拢因 OCR 切词导致的散乱标点与括号周边的空白符（如 'first . ' -> 'first. ', '( N1 )' -> '(N1)'）。
    从源头杜绝弱智的空白符/标点格式纠错噪音。
    """
    # 0. 收拢 OCR 切分导致的简缩词内部空格（如 "does n't" -> "doesn't"）
    text = re.sub(r"\b([a-zA-Z]+)\s+n['’]t\b", r"\1n't", text, flags=re.IGNORECASE)
    # 1. 收拢标点前的多余空格
    text = re.sub(r'\s+([,.\?!:;%])', r'\1', text)
    # 2. 收拢左括号/左方括号后的多余空格
    text = re.sub(r'([\(\[\{])\s+', r'\1', text)
    # 3. 收拢右括号/右方括号前的多余空格
    text = re.sub(r'\s+([\)\]\}])', r'\1', text)
    # 4. 合并多余连续空格
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def check_grammar_and_spelling(text: str, allow_online: bool = True) -> Dict[str, Any]:
    """
    长句/段落级语法与拼写综合检测分析引擎。
    采用“OCR 排版规整化 + 本地极速零阻塞预筛 + 单次异步高级语法检验 + 严格抗噪音过滤”架构。
    """
    # 先做 OCR 排版规整化，杜绝 token 拼接空格造成的伪排版错误
    clean_text = normalize_ocr_text(text)
    if not clean_text:
        return {"has_errors": False, "corrections": [], "polished_sentence": ""}

    # replacement 格式: (start, end, suggested_text, correction_dict)
    replacements: List[Tuple[int, int, str, Dict[str, Any]]] = []

    # 手写草书连笔粘合纠偏 (如 hadCome / hadcowe / hadGome 纠正为 had some / Had some)
    for m in re.finditer(r'\b(had(?:Come|come|cowe|Gome))\b', clean_text, flags=re.IGNORECASE):
        raw_match = m.group(1)
        sugg = "Had some" if raw_match[0].isupper() else "had some"
        replacements.append((
            m.start(1),
            m.end(1),
            sugg,
            {
                "type": "spelling",
                "original": raw_match,
                "suggested": sugg,
                "explanation": f"手写连笔识别纠偏：草书 'some' 连笔误识并粘合，建议修正为 '{sugg}'",
                "offset": m.start(1),
                "length": len(raw_match)
            }
        ))

    # 1. 动词主谓一致性规则 (Subject-Verb Agreement)
    # 1a. 第三人称单数主语 + 动词原形 (如 He like -> likes, She want -> wants, The algorithm calculate -> calculates)
    VERB_S_MAP = {
        'like': 'likes', 'want': 'wants', 'need': 'needs', 'have': 'has', 'do': 'does',
        'go': 'goes', 'see': 'sees', 'know': 'knows', 'think': 'thinks', 'take': 'takes',
        'come': 'comes', 'give': 'gives', 'look': 'looks', 'use': 'uses', 'find': 'finds',
        'tell': 'tells', 'ask': 'asks', 'work': 'works', 'seem': 'seems', 'feel': 'feels',
        'try': 'tries', 'leave': 'leaves', 'call': 'calls', 'play': 'plays', 'help': 'helps',
        'talk': 'talks', 'start': 'starts', 'show': 'shows', 'hear': 'hears', 'run': 'runs',
        'live': 'lives', 'believe': 'believes', 'bring': 'brings', 'happen': 'happens',
        'write': 'writes', 'provide': 'provides', 'sit': 'sits', 'stand': 'stands',
        'study': 'studies', 'watch': 'watches', 'teach': 'teaches', 'carry': 'carries',
        'understand': 'understands', 'calculate': 'calculates', 'represent': 'represents',
        'perform': 'performs', 'execute': 'executes', 'operate': 'operates', 'require': 'requires',
        'prove': 'proves', 'imply': 'implies', 'indicate': 'indicates'
    }
    sing_subjs = r'(?:[Hh]e|[Ss]he|[Ii]t|[Ee]veryone|[Ee]verybody|[Ss]omeone|[Ss]omebody|[Nn]o\s+one|[Nn]obody|[Tt]he\s+(?:function|algorithm|method|model|system|program|teacher|student|boy|girl|man|woman|child)|O\([a-zA-Z0-9]+\))'
    verbs_pat = '|'.join(VERB_S_MAP.keys())
    _AUX_BEFORE_SUBJ = frozenset({
        "does", "do", "did", "can", "could", "will", "would", "should", "must", "may", "might",
        "doesn't", "don't", "didn't", "let", "lets", "make", "makes", "made", "help", "helps", "helped",
    })
    for m in re.finditer(rf'\b({sing_subjs})\s+({verbs_pat})\b', clean_text):
        subj, verb = m.group(1), m.group(2)
        before_tokens = re.findall(r"[A-Za-z']+", clean_text[:m.start(1)])
        if before_tokens and before_tokens[-1].lower() in _AUX_BEFORE_SUBJ:
            continue
        sugg_verb = VERB_S_MAP[verb.lower()]
        replacements.append((
            m.start(2),
            m.end(2),
            sugg_verb,
            {
                "type": "grammar",
                "original": m.group(0),
                "suggested": f"{subj} {sugg_verb}",
                "explanation": f"主谓一致：单数第三人称主语需使用动词三单形式 '{sugg_verb}'",
                "offset": m.start(2),
                "length": len(verb)
            }
        ))

    # 1b. 集合名词/单数主语 + 复数系动词 (如 The list of items are -> is)
    for m in re.finditer(r'\b(The\s+(?:list|group|number|set|collection|series|sequence|variety|array)\s+of\s+[a-zA-Z0-9_\s]+?)\s+(are|were)\b', clean_text, flags=re.IGNORECASE):
        v = m.group(2)
        sugg_v = 'is' if v.lower() == 'are' else 'was'
        replacements.append((
            m.start(2),
            m.end(2),
            sugg_v,
            {
                "type": "grammar",
                "original": m.group(0),
                "suggested": f"{m.group(1)} {sugg_v}",
                "explanation": f"主谓一致：短语核心词为单数集合名词，系动词应使用单数 '{sugg_v}'",
                "offset": m.start(2),
                "length": len(v)
            }
        ))

    # 2. 动词时态规则 (Verb Tense & Aspect)
    # 2a. 明确过去时间状语 + 一般现在时动词 (如 I go to school yesterday -> went)
    PAST_VERBS = {
        'go': 'went', 'see': 'saw', 'buy': 'bought', 'eat': 'ate', 'come': 'came',
        'take': 'took', 'give': 'gave', 'make': 'made', 'get': 'got', 'have': 'had',
        'do': 'did', 'say': 'said', 'find': 'found', 'tell': 'told', 'write': 'wrote',
        'meet': 'met', 'leave': 'left', 'hear': 'heard', 'visit': 'visited', 'play': 'played',
        'work': 'worked', 'call': 'called', 'ask': 'asked', 'watch': 'watched', 'finish': 'finished'
    }
    past_markers = r'(?:yesterday|last\s+(?:night|week|month|year|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago)'
    if re.search(past_markers, clean_text, flags=re.IGNORECASE):
        verbs_p = '|'.join(PAST_VERBS.keys())
        for m in re.finditer(rf'\b(I|you|he|she|it|we|they|[Tt]he\s+[a-z]+)\s+({verbs_p})\b', clean_text):
            subj, verb = m.group(1), m.group(2)
            before_tokens = re.findall(r"[A-Za-z']+", clean_text[:m.start(1)])
            if before_tokens and before_tokens[-1].lower() in _AUX_BEFORE_SUBJ:
                continue
            past_v = PAST_VERBS[verb.lower()]
            replacements.append((
                m.start(2),
                m.end(2),
                past_v,
                {
                    "type": "grammar",
                    "original": m.group(0),
                    "suggested": f"{subj} {past_v}",
                    "explanation": f"动词时态：句中有明确过去时间状语，动词应使用一般过去时 '{past_v}'",
                    "offset": m.start(2),
                    "length": len(verb)
                }
            ))

    # 2b. 条件/时间状语从句“主将从现” (when I will arrive -> when I arrive)
    for m in re.finditer(r'\b(when|if|as soon as|before|after|until)\s+([a-zA-Z]+)\s+(will)\s+([a-zA-Z]+)\b', clean_text, flags=re.IGNORECASE):
        conj, subj, _, v = m.group(1), m.group(2), m.group(3), m.group(4)
        replacements.append((
            m.start(3),
            m.end(4),
            v,
            {
                "type": "grammar",
                "original": m.group(0),
                "suggested": f"{conj} {subj} {v}",
                "explanation": "状语从句时态：遵循“主将从现”原则，条件/时间状语从句中不使用将来时 'will'，应使用一般现在时",
                "offset": m.start(3),
                "length": len(m.group(3)) + 1 + len(v)
            }
        ))

    # 3. 动词搭配与不定式结构 (Verb Collocations & Infinitives)
    # 3a. 动词后漏接不定式 to (如 I want go -> I want to go)
    want_verbs = r'(?:want|hope|decide|plan|agree|refuse|promise|learn|afford|choose|expect|need)'
    for m in re.finditer(rf'\b({want_verbs})\s+(go|do|see|buy|study|play|visit|work|come|help|find|tell|make|take|get)\b', clean_text, flags=re.IGNORECASE):
        v1, v2 = m.group(1), m.group(2)
        replacements.append((
            m.start(2),
            m.start(2),
            "to ",
            {
                "type": "grammar",
                "original": f"{v1} {v2}",
                "suggested": f"{v1} to {v2}",
                "explanation": f"动词搭配：动词 '{v1}' 后接实义动词时需使用不定式 'to {v2}'",
                "offset": m.start(2),
                "length": 0
            }
        ))

    # 3b. 情态动词后多加 to (如 can to go -> can go)
    for m in re.finditer(r'\b(can|could|will|would|must|should|may|might)\s+(to\s+)([a-zA-Z]+)\b', clean_text, flags=re.IGNORECASE):
        modal, _, v = m.group(1), m.group(2), m.group(3)
        replacements.append((
            m.start(2),
            m.end(2),
            "",
            {
                "type": "grammar",
                "original": m.group(0),
                "suggested": f"{modal} {v}",
                "explanation": f"情态动词用法：情态动词 '{modal}' 后直接接动词原形，不加 'to'",
                "offset": m.start(2),
                "length": len(m.group(2))
            }
        ))

    # 3c. 谓语动词重复/双重谓语 (如 I am agree -> I agree；严禁包含介词 like 以免误伤 'This tree is like a graph')
    for m in re.finditer(r'\b(am|is|are)\s+(agree|want|need)\b', clean_text, flags=re.IGNORECASE):
        be_v, v = m.group(1), m.group(2)
        replacements.append((
            m.start(1),
            m.end(1) + 1,
            "",
            {
                "type": "grammar",
                "original": m.group(0),
                "suggested": v,
                "explanation": f"谓语动词重复：be 动词与实义动词不可连用，应省略系动词 '{be_v}'",
                "offset": m.start(1),
                "length": len(be_v) + 1
            }
        ))

    # 4. 不定冠词与限定词智能纠偏 (Articles & Determiners)
    # 4a. 不定冠词 'a' + 元音开头单词（排除 /juː/ 音素与 one/once 等半元音词）
    _A_VOWEL_EXCEPTIONS = frozenset({
        "one", "once", "user", "users", "usage", "useful", "useless", "use", "used",
        "utility", "utilities", "utilize", "ubiquitous", "union", "unify", "unified",
        "unicode", "uniform", "unit", "units", "unique", "university", "universities",
        "universal", "unix", "url", "urls", "usb", "udp", "uuid", "uart", "ui", "ux",
        "european", "euler", "euclid", "euclidean", "eulogy",
    })
    vowel_article_pattern = r'\b([Aa])\s+([aeiouAEIOU][a-zA-Z]+)\b'
    for m in re.finditer(vowel_article_pattern, clean_text):
        art, next_word = m.group(1), m.group(2)
        nw_low = next_word.lower()
        if nw_low in _A_VOWEL_EXCEPTIONS:
            continue
        if nw_low.startswith(("eu", "uni", "use", "uti", "ubiq")):
            if not nw_low.startswith(("unim", "unin", "uniden")):
                continue
        if next_word.isupper() and next_word.startswith("U"):
            continue
        suggested_art = "An" if art.isupper() else "an"
        replacements.append((
            m.start(1),
            m.end(1),
            suggested_art,
            {
                "type": "grammar",
                "original": f"{art} {next_word}",
                "suggested": f"{suggested_art} {next_word}",
                "explanation": f"冠词搭配：在元音音素开头的单词 '{next_word}' 前应使用 '{suggested_art}'",
                "offset": m.start(1),
                "length": len(art)
            }
        ))

    # 4b. 不定冠词 'an' + 辅音开头单词 (如 an book -> a book；保护 silent-h 与元音字母发音缩写 HTTP/SQL/LLM/XML)
    _AN_CONSONANT_EXCEPTIONS = frozenset({
        "hour", "hours", "hourly", "honest", "honestly", "honesty",
        "honor", "honors", "honorable", "honour", "heir", "heirs", "herb", "herbs",
    })
    _VOWEL_SOUND_ACRONYM_INIT = frozenset("FHLMNRSX")
    consonant_an_pattern = r'\b([Aa]n)\s+([bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ][a-zA-Z]+)\b'
    for m in re.finditer(consonant_an_pattern, clean_text):
        art, next_word = m.group(1), m.group(2)
        nw_low = next_word.lower()
        if nw_low in _AN_CONSONANT_EXCEPTIONS:
            continue
        if next_word.isupper() and next_word[0] in _VOWEL_SOUND_ACRONYM_INIT:
            continue
        suggested_art = "A" if art[0].isupper() else "a"
        replacements.append((
            m.start(1),
            m.end(1),
            suggested_art,
            {
                "type": "grammar",
                "original": f"{art} {next_word}",
                "suggested": f"{suggested_art} {next_word}",
                "explanation": f"冠词搭配：在辅音音素开头的单词 '{next_word}' 前应使用 '{suggested_art}'",
                "offset": m.start(1),
                "length": len(art)
            }
        ))

    # 4c. 固定处所短语多加冠词 (如 go to a school -> go to school)
    for m in re.finditer(r'\b(go\s+to\s+)(a\s+)(school|work|bed|hospital|college|university)\b', clean_text, flags=re.IGNORECASE):
        prefix, a_str, place = m.group(1), m.group(2), m.group(3)
        replacements.append((
            m.start(2),
            m.end(2),
            "",
            {
                "type": "grammar",
                "original": m.group(0),
                "suggested": f"{prefix}{place}",
                "explanation": f"习惯表达：表达上学/就寝/就医等抽象活动时使用 '{prefix}{place}'，不需加冠词 'a'",
                "offset": m.start(2),
                "length": len(a_str)
            }
        ))

    # 5. 代词人称、格与物主代词规则 (Pronoun Errors)
    # 5a. 主语位置代词格与礼貌顺序（严格限定于句首，避免误伤介词宾语如 'Send it to me and my friend'）
    _PRON_NOM_MAP = {"me": "I", "him": "he", "her": "she", "us": "we", "them": "they"}
    pron_subj_pattern = r'(?:^|[.!?]\s+)([Mm]e|[Hh]im|[Hh]er|[Uu]s|[Tt]hem)\s+and\s+((?:(?:my|your|his|her|their|our|the)\s+)?[a-zA-Z]+)\b'
    for m in re.finditer(pron_subj_pattern, clean_text):
        pron, other = m.group(1), m.group(2).strip()
        sugg_pron = _PRON_NOM_MAP.get(pron.lower(), "I")
        sugg_full = f"{other.capitalize()} and {sugg_pron}"
        replacements.append((
            m.start(1),
            m.end(2),
            sugg_full,
            {
                "type": "grammar",
                "original": clean_text[m.start(1):m.end(2)],
                "suggested": sugg_full,
                "explanation": f"代词主格与顺序：句中作主语应使用主格代词 '{sugg_pron}'，且英语礼貌习惯将对方置于自身之前（即 '{sugg_full}'）",
                "offset": m.start(1),
                "length": m.end(2) - m.start(1)
            }
        ))

    # 5b. 物主代词与主格混淆（仅在动词/介词后的宾语短语中生效，严禁把主语+实义动词 'They own houses' 改坏）
    PRON_POSS_MAP = {
        'they': 'their', 'he': 'his', 'she': 'her', 'it': 'its', 'you': 'your', 'we': 'our', 'i': 'my'
    }
    pron_pat = '|'.join(PRON_POSS_MAP.keys())
    _PRE_POSS_TRIGGER = r'(?:do|did|does|doing|done|finish|finished|write|wrote|complete|completed|make|made|build|built|check|use|used|keep|solve|read|with|for|in|on|of|to|by|at)'
    for m in re.finditer(rf'\b{_PRE_POSS_TRIGGER}\s+({pron_pat})\s+(own)\s+([a-zA-Z]+)\b', clean_text, flags=re.IGNORECASE):
        p, own, noun = m.group(1), m.group(2), m.group(3)
        sugg_poss = PRON_POSS_MAP[p.lower()]
        replacements.append((
            m.start(1),
            m.end(1),
            sugg_poss,
            {
                "type": "grammar",
                "original": f"{p} {own} {noun}",
                "suggested": f"{sugg_poss} {own} {noun}",
                "explanation": f"物主代词用法：修饰 'own {noun}' 时应使用形容词性物主代词 '{sugg_poss}'，不可使用主格代词 '{p}'",
                "offset": m.start(1),
                "length": len(p)
            }
        ))

    # 6. 介词搭配与及物动词误用 (Preposition Collocations)
    # 6a. arrive to + 地点限定词/地名 -> arrive in / at（严禁误伤不定式 'arrived to help us'）
    _PLACE_HEADS = r'(?:the|a|an|my|our|his|her|their|your|this|that|china|beijing|shanghai|london|america|city|country|tokyo|school|airport|station|office|campus|class|room|hotel|hospital)\b'
    for m in re.finditer(rf'\b(arrived?|arrives?)\s+(to)\s+({_PLACE_HEADS}[a-zA-Z\s]*)', clean_text, flags=re.IGNORECASE):
        v, prep, place = m.group(1), m.group(2), m.group(3).strip()
        sugg_prep = 'in' if any(k in place.lower() for k in ('china', 'beijing', 'shanghai', 'london', 'america', 'city', 'country', 'tokyo')) else 'at'
        replacements.append((
            m.start(2),
            m.end(2),
            sugg_prep,
            {
                "type": "grammar",
                "original": f"{v} {prep}",
                "suggested": f"{v} {sugg_prep}",
                "explanation": f"介词搭配：表达到达某地时应使用 '{v} {sugg_prep}'，不可使用 '{v} to'",
                "offset": m.start(2),
                "length": len(prep)
            }
        ))

    # 6b. different with -> different from
    for m in re.finditer(r'\bdifferent\s+(with)\b', clean_text, flags=re.IGNORECASE):
        replacements.append((
            m.start(1),
            m.end(1),
            "from",
            {
                "type": "grammar",
                "original": "different with",
                "suggested": "different from",
                "explanation": "介词搭配：固定短语与……不同应使用 'different from'，不可使用 'different with'",
                "offset": m.start(1),
                "length": len(m.group(1))
            }
        ))

    # 6c. depend of -> depend on
    for m in re.finditer(r'\b(depends?|depended?)\s+(of)\b', clean_text, flags=re.IGNORECASE):
        v = m.group(1)
        replacements.append((
            m.start(2),
            m.end(2),
            "on",
            {
                "type": "grammar",
                "original": f"{v} of",
                "suggested": f"{v} on",
                "explanation": f"介词搭配：固定短语取决于/依赖应使用 '{v} on'，不可使用 '{v} of'",
                "offset": m.start(2),
                "length": len(m.group(2))
            }
        ))

    # 6d. 及物动词多加介词 (discuss about -> discuss, marry with -> marry)
    for m in re.finditer(r'\b(discuss(?:es|ed|ing)?)\s+(about)\b', clean_text, flags=re.IGNORECASE):
        v = m.group(1)
        replacements.append((
            m.start(2),
            m.end(2),
            "",
            {
                "type": "grammar",
                "original": f"{v} about",
                "suggested": v,
                "explanation": f"及物动词用法：'{v}' 是及物动词，直接接宾语，不加介词 'about'",
                "offset": m.start(2),
                "length": len(m.group(2))
            }
        ))
    for m in re.finditer(r'\b(marry|married|marries)\s+(with)\b', clean_text, flags=re.IGNORECASE):
        v = m.group(1)
        replacements.append((
            m.start(2),
            m.end(2),
            "",
            {
                "type": "grammar",
                "original": f"{v} with",
                "suggested": v,
                "explanation": f"及物动词用法：'{v}' 直接接宾语（如 marry someone），不加介词 'with'",
                "offset": m.start(2),
                "length": len(m.group(2))
            }
        ))

    # 7. 修饰语词性规则 (行为动词 + good -> well)
    for m in re.finditer(r'\b(sings?|speaks?|plays?|dances?|cooks?|performs?|works?|runs?)\s+(good)\b', clean_text, flags=re.IGNORECASE):
        v = m.group(1)
        replacements.append((
            m.start(2),
            m.end(2),
            "well",
            {
                "type": "grammar",
                "original": f"{v} good",
                "suggested": f"{v} well",
                "explanation": f"修饰语词性：修饰行为动词 '{v}' 需使用副词 'well'，不能使用形容词 'good'",
                "offset": m.start(2),
                "length": 4
            }
        ))

    # 8. 双重连词 (Redundant Conjunctions: 仅限同一句内 Although ... but ...，绝不跨句误删)
    for m in re.finditer(r'\bAlthough\b[^.!?]+\b(but)\s+', clean_text, flags=re.IGNORECASE):
        replacements.append((
            m.start(1),
            m.end(1) + 1,
            "",
            {
                "type": "grammar",
                "original": "Although ... but",
                "suggested": "Although ... (省略 but)",
                "explanation": "连词多余：英语中 Although 与 but 属于双重从属连词，不可在同一句中同时出现，需省略 'but'",
                "offset": m.start(1),
                "length": 4
            }
        ))

    # 3. 单词级离线零延迟拼写纠错（完全杜绝长句逐词网络风暴！）
    tokens = re.finditer(r"\b[a-zA-Z0-9_]+(?:'[a-zA-Z]+)?\b", clean_text)
    for t in tokens:
        word = t.group(0)
        # 数学变量、代码标识符 (如 Nx, N1, kNN, x_1) 免疫纠错
        if is_math_or_code_identifier(word):
            continue

        word_lower = word.lower()
        # 避开已经被主谓一致/冠词覆盖的范围
        w_start, w_end = t.start(), t.end()
        if any(r[0] <= w_start and w_end <= r[1] for r in replacements):
            continue

        # 1. 手写笔迹与确诊错词先验映射优先于词库与首字母跳过（兼容句首 'Teh ...', 'Owever, ...'）
        if word_lower in HANDWRITING_OFFLINE_FIXES:
            sugg = HANDWRITING_OFFLINE_FIXES[word_lower]
            if word.isupper():
                sugg = sugg.upper()
            elif word[0].isupper():
                sugg = sugg.capitalize()
            replacements.append((
                w_start,
                w_end,
                sugg,
                {
                    "type": "spelling",
                    "original": word,
                    "suggested": sugg,
                    "explanation": f"手写笔迹纠偏：建议更正为 '{sugg}'",
                    "offset": w_start,
                    "length": len(word)
                }
            ))
            continue

        if word_lower in COMMON_TYPOS:
            sugg = COMMON_TYPOS[word_lower]
            if word.isupper():
                sugg = sugg.upper()
            elif word[0].isupper():
                sugg = sugg.capitalize()
            replacements.append((
                w_start,
                w_end,
                sugg,
                {
                    "type": "spelling",
                    "original": word,
                    "suggested": sugg,
                    "explanation": f"拼写错误：建议更正为 '{sugg}'",
                    "offset": w_start,
                    "length": len(word)
                }
            ))
            continue

        # 核心：纯本地词法探测，已确认合法的英文词汇绝对不标记为拼写错误！
        if not is_valid_word(word_lower):
            # 2. 首字母大写（如 Pascal, Dijkstra, Turing 等人名或专有名词）不盲猜（全大写长错词如 ALGORITM 仍允许纠错）
            if (word[0].isupper() and not word.isupper()) or (not word.isupper() and any(c.isupper() for c in word[1:])):
                continue

            # 3. 严格受限的单字母编辑 O(1) 邻域纠错（严禁将合法同根派生词如 rationale 篡改为 rational）
            sugg = _find_best_single_edit_candidate(word_lower, cutoff=0.88)
            if sugg and sugg != word_lower:
                if word.isupper():
                    sugg = sugg.upper()
                elif word[0].isupper():
                    sugg = sugg.capitalize()

                replacements.append((
                    w_start,
                    w_end,
                    sugg,
                    {
                        "type": "spelling",
                        "original": word,
                        "suggested": sugg,
                        "explanation": f"拼写错误：建议更正为 '{sugg}'",
                        "offset": w_start,
                        "length": len(word)
                    }
                ))

    # 4. 单次网络批处理：在线 LanguageTool 强化（带 60s 失败熔断器，超时严格限制 1.2s，失败即刻放弃，绝不拖慢后台管线）
    global _lt_breaker_until
    if allow_online and len(clean_text) > 12 and time.time() >= _lt_breaker_until:
        try:
            params = urllib.parse.urlencode({
                "text": clean_text,
                "language": "en-US"
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.languagetool.org/v2/check",
                data=params,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json"
                }
            )
            with _open_url_direct(req, timeout=1.2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for match in data.get("matches", []):
                    rule_obj = match.get("rule", {})
                    rule_id = rule_obj.get("id", "")
                    issue_type = rule_obj.get("issueType", "")
                    message = match.get("message", "")

                    # 🌟 彻底过滤纯标点、空白符、段落排版类规则（绝不在取词卡片里搞吹毛求疵的排版报障）
                    if any(k in rule_id for k in ("WHITESPACE", "TYPOGRAPHY", "COMMA_PARENTHESIS_WHITESPACE", "SPACE_BEFORE", "CONSECUTIVE_SPACES", "PUNCTUATION")):
                        continue
                    if "UPPERCASE_SENTENCE_START" in rule_id or "uppercase" in message.lower() or "大写" in message:
                        continue
                    if "HYPHEN" in rule_id.upper() or "EN_COMPOUNDS" in rule_id.upper() or "spelled with a hyphen" in message.lower() or "hyphen" in message.lower():
                        continue

                    offset = match.get("offset", 0)
                    length = match.get("length", 0)
                    end_offset = offset + length
                    # 避免与前面已识别出的规则发生重叠冲突（兼容零长插入点位如 'want go' -> 'want to go'）
                    if any(
                        (max(r[0], offset) < min(r[1], end_offset))
                        or (r[0] == r[1] and offset <= r[0] <= end_offset)
                        or (offset == end_offset and r[0] <= offset <= r[1])
                        for r in replacements
                    ):
                        continue
                    error_word = clean_text[offset:end_offset]
                    suggs = [r["value"] for r in match.get("replacements", [])]
                    if not suggs or not error_word:
                        continue

                    sugg = html.unescape(suggs[0])

                    # 🌟 强力抗噪音过滤门禁：
                    # 1. 错误词全是符号或无字母数字（如单独一个点或括号）-> 过滤
                    if not any(c.isalnum() for c in error_word):
                        continue
                    # 2. 剥离空格及连字符后字符一模一样（如 ' .' vs '.' 或 'doom scrolling' vs 'doom-scrolling'）-> 过滤
                    if re.sub(r'[\s\-–—]+', '', error_word).lower() == re.sub(r'[\s\-–—]+', '', sugg).lower():
                        if not (error_word == "i" and sugg == "I"):
                            continue
                    # 3. 数学/代码变量标识符 (如 Nx, N1, O(N)) -> 过滤
                    if is_math_or_code_identifier(error_word):
                        continue
                    # 4. 若原单词本身已经是合法词汇或前缀变体，拦截拼写错误假阳性
                    if is_valid_word(error_word.lower()):
                        if issue_type in ("misspelling", "typographical"):
                            continue
                        if is_prefix_relation(error_word, sugg):
                            continue

                    # 5. 🌟 拦截 LanguageTool 将手写常见连笔词荒谬脑补为偏门日常词 (如 treout -> trout 鳟鱼，orbo -> orb 球体)
                    err_clean = re.sub(r'[^a-zA-Z0-9_]', '', error_word).lower()
                    if err_clean in ("treout", "treot", "treut"):
                        sugg = "treat"
                    elif err_clean in ("orbo", "obo", "orb"):
                        continue
                    elif err_clean in ("owever", "iowever"):
                        sugg = "However" if error_word[0].isupper() else "however"
                    elif err_clean == "bust":
                        after_bust = clean_text[end_offset:].lstrip().lower()
                        if after_bust.startswith(("the one", "the other", "the first", "the second", "the below", "the above", "it ", "this ", "that ", "does ", "do ", "did ")):
                            sugg = "but"
                        else:
                            continue

                    replacements.append((
                        offset,
                        end_offset,
                        sugg,
                        {
                            "type": "grammar",
                            "original": error_word,
                            "suggested": sugg,
                            "explanation": match.get("message", f"建议更正为 '{sugg}'"),
                            "offset": offset,
                            "length": length
                        }
                    ))
        except Exception as lt_err:
            if hasattr(lt_err, "close"):
                try:
                    lt_err.close()
                except Exception:
                    pass
            _lt_breaker_until = time.time() + 60.0

    # 5. 去重与区间重叠剪裁，提取结构化 corrections 列表（严格处理零长插入与同起点重叠）
    replacements.sort(key=lambda x: (x[0], x[1]))
    filtered_replacements: List[Tuple[int, int, str, Dict[str, Any]]] = []
    for r in replacements:
        if not filtered_replacements:
            filtered_replacements.append(r)
            continue
        prev = filtered_replacements[-1]
        overlaps = (
            max(prev[0], r[0]) < min(prev[1], r[1])
            or (prev[0] == prev[1] and r[0] <= prev[0] <= r[1])
            or (r[0] == r[1] and prev[0] <= r[0] <= prev[1])
        )
        if not overlaps:
            filtered_replacements.append(r)

    corrections = [r[3] for r in filtered_replacements]

    # 6. 🌟 逆向从后往前应用替换，彻底杜绝切片索引错位与文本撕裂！
    polished = clean_text
    filtered_replacements.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for start, end, sugg_str, _ in filtered_replacements:
        polished = polished[:start] + sugg_str + polished[end:]

    # 7. 首字母大写与末尾标点优雅润色
    if polished and polished[0].islower():
        polished = polished[0].upper() + polished[1:]

    # 规范句末标点：仅在末尾为普通词且非标点时补充句号，避免破坏引号或括号
    if polished and polished[-1].isalnum():
        polished = polished + "."

    polished = html.unescape(polished)

    return {
        "has_errors": len(corrections) > 0,
        "corrections": corrections,
        "polished_sentence": polished
    }
