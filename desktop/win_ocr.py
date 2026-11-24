import os
import sys
import asyncio
import ctypes
import logging
import threading
import time

_log = logging.getLogger("screenlex.ocr")

def _ensure_onnxruntime_dll_dir():
    cands = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(os.path.join(meipass, "onnxruntime", "capi"))
    cands.append(os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "onnxruntime", "capi"))
    try:
        import site
        for sp in site.getsitepackages():
            cands.append(os.path.join(sp, "onnxruntime", "capi"))
    except Exception:
        pass
    for dll_dir in cands:
        if os.path.exists(dll_dir) and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(dll_dir)
            break

# 必须在任何 winrt 模块导入前最优先加载 onnxruntime，杜绝 Windows C++ 运行时 DLL 初始化冲突
try:
    _ensure_onnxruntime_dll_dir()
    import onnxruntime
except Exception:
    pass

from PIL import ImageGrab, Image, ImageOps, ImageFilter
import winrt.windows.media.ocr as ocr
import winrt.windows.graphics.imaging as imaging
import winrt.windows.storage.streams as streams
import winrt.windows.globalization as glob
from typing import Dict, Any, List, Optional, Tuple
try:
    import numpy as np
except Exception:
    np = None
from .ocr_helpers import (
    clean_word_text,
    extract_sentence_from_words,
    sort_words_reading_order,
    process_ocr_tokens,
    split_glued_ocr_token,
)

class WindowsMediaOCR:
    def __init__(self):
        self._engine_en = None
        self._engine_zh = None
        self._rapid_engine = None
        self._winrt_lock = threading.Lock()
        self._rapid_init_lock = threading.Lock()
        self._rapid_lock = threading.Lock()
        self._cloud_breaker_until = 0.0

    def _get_engine(self, lang_tag="en-US"):
        if lang_tag == "en-US":
            if self._engine_en is None:
                with self._winrt_lock:
                    if self._engine_en is None:
                        try:
                            self._engine_en = ocr.OcrEngine.try_create_from_language(glob.Language("en-US"))
                        except Exception:
                            pass
                        if self._engine_en is None:
                            try:
                                self._engine_en = ocr.OcrEngine.try_create_from_user_profile_languages()
                            except Exception:
                                pass
            return self._engine_en
        else:
            if self._engine_zh is None:
                with self._winrt_lock:
                    if self._engine_zh is None:
                        try:
                            self._engine_zh = ocr.OcrEngine.try_create_from_language(glob.Language("zh-Hans-CN"))
                        except Exception:
                            pass
                        if self._engine_zh is None:
                            try:
                                self._engine_zh = ocr.OcrEngine.try_create_from_user_profile_languages()
                            except Exception:
                                pass
            return self._engine_zh

    def _get_rapid_fallback(self):
        if self._rapid_engine is None:
            with self._rapid_init_lock:
                if self._rapid_engine is None and not getattr(self, "_rapid_init_failed", False):
                    try:
                        _ensure_onnxruntime_dll_dir()
                        from rapidocr_onnxruntime import RapidOCR
                        import rapidocr_onnxruntime.utils as _ru
                        import onnxruntime as _ort
                        _orig_is = _ru.InferenceSession

                        def _bounded_session(path, sess_options=None, providers=None, **kw):
                            # 限制 ONNXRuntime 线程数：防止 default=0 在混合大小核笔记本电池供电时 16 线程自旋拉满 CPU 耗时 >2.5s
                            if sess_options is not None:
                                sess_options.intra_op_num_threads = 4
                                sess_options.inter_op_num_threads = 1
                                try:
                                    sess_options.execution_mode = _ort.ExecutionMode.ORT_SEQUENTIAL
                                except Exception:
                                    pass
                            return _orig_is(path, sess_options=sess_options, providers=providers, **kw)

                        _ru.InferenceSession = _bounded_session
                        try:
                            self._rapid_engine = RapidOCR(
                                det_model_path=None,
                                det_limit_type="max",
                                det_limit_side_len=1280,
                                use_angle_cls=False,
                            )
                        finally:
                            _ru.InferenceSession = _orig_is
                    except Exception as exc:
                        self._rapid_init_failed = True
                        _log.warning("RapidOCR init failed: %s", exc)
        return self._rapid_engine

    def recognize_at_point(self, gx: int, gy: int, radius_y: int = 600) -> Dict[str, Any]:
        """
        在全局屏幕物理坐标 (gx, gy) 处以大范围视线水平带切片快速 OCR 识别目标词汇及整行词块
        radius_y 扩大至 600 物理像素（双向 1200 像素，完美覆盖高刷平板多段落与长句）
        """
        try:
            user32 = ctypes.windll.user32
            screen_w = int(user32.GetSystemMetrics(78) or user32.GetSystemMetrics(0) or 2560)
            screen_h = int(user32.GetSystemMetrics(79) or user32.GetSystemMetrics(1) or 1600)
        except Exception:
            screen_w = 2560
            screen_h = 1600

        x1 = 0
        x2 = screen_w
        y1 = max(0, gy - radius_y)
        y2 = min(screen_h, gy + radius_y)

        try:
            img = ImageGrab.grab(bbox=(x1, y1, x2, y2))
        except Exception as e:
            _log.warning("Screen grab failed: %s", e)
            return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

        return self.recognize_image(img, x1, y1, gx, gy)

    def recognize_image(self, img: Image.Image, origin_x: int, origin_y: int, target_x: int, target_y: int, allow_online: bool = False) -> Dict[str, Any]:
        """
        直接对传入的高清纯净屏幕截图进行 OCR 识别（完全绕过二次截图与图层遮挡，0ms 截屏损耗，画质 100% 纯净）
        默认 allow_online=False，完全纯离线秒级决算，杜绝 UI 主线程网络阻塞
        """
        # 1. 前置特征检测：毫秒级探测是否包含彩色/高对比手写笔迹区域
        hw_regions = self.detect_handwriting_regions(img)

        # 2. 若明确检出手写笔迹，仅对命中目标坐标的局部区域跑 RapidOCR（杜绝整屏 PPT 上空耗数秒）
        if hw_regions:
            try:
                loc_tx = target_x - origin_x
                loc_ty = target_y - origin_y
                target_hw = None
                best_d = float("inf")
                for hr in hw_regions:
                    rx, ry, rw, rh = hr["box"]
                    if (rx - 40) <= loc_tx <= (rx + rw + 40) and (ry - 40) <= loc_ty <= (ry + rh + 40):
                        target_hw = hr
                        break
                    dc = abs((rx + rw * 0.5) - loc_tx) + abs((ry + rh * 0.5) - loc_ty)
                    if dc < best_d:
                        best_d = dc
                        target_hw = hr
                if target_hw is not None and (img.width * img.height > 400000):
                    hw_res = self._recognize_handwriting(
                        target_hw["crop"],
                        origin_x + target_hw["origin_x"],
                        origin_y + target_hw["origin_y"],
                        target_x,
                        target_y,
                        allow_online=allow_online,
                    )
                else:
                    hw_res = self._recognize_handwriting(img, origin_x, origin_y, target_x, target_y, allow_online=allow_online)
                if hw_res and (hw_res.get("word") or len(hw_res.get("all_words", [])) > 0):
                    return hw_res
            except Exception as e:
                _log.warning("Handwriting OCR error: %s", e)

        # 3. 运行 Windows 原生 WinRT OCR
        winrt_res = None
        try:
            winrt_res = asyncio.run(self._recognize_winrt_async(img, origin_x, origin_y, target_x, target_y))
        except Exception as e:
            _log.warning("WinRT OCR error: %s", e)

        # 4. 判定 WinRT 质量：若 WinRT 质量存疑（如词数极少、或大量 1 字符符号乱码），尝试 RapidOCR 补充
        winrt_words = winrt_res.get("all_words", []) if winrt_res else []
        target_w = winrt_res.get("word", "").strip() if winrt_res else ""
        alpha_count = sum(1 for c in target_w if c.isascii() and c.isalpha())
        is_high_conf = alpha_count >= 3 and (alpha_count / max(1, len(target_w))) >= 0.75

        # 检查 WinRT 词块是否呈现明显的火星文/碎屑特征（均长极短或多为符号）
        is_garbage_winrt = False
        if winrt_words:
            total_clean_len = sum(len(w.get("clean", "")) for w in winrt_words)
            avg_clean_len = total_clean_len / max(1, len(winrt_words))
            if avg_clean_len < 2.2:
                is_garbage_winrt = True

        if winrt_res and (len(winrt_words) >= 2 or is_high_conf) and not is_garbage_winrt:
            try:
                sub_regions = self.detect_outlined_subtitle_regions(img)
                if sub_regions:
                    in_sub = any(
                        (origin_x + r["origin_x"]) <= target_x <= (origin_x + r["origin_x"] + r["box"][2])
                        and (origin_y + r["origin_y"]) <= target_y <= (origin_y + r["origin_y"] + r["box"][3])
                        for r in sub_regions
                    )
                    direct_hit = any(
                        (w["box"]["x"] - 14 <= target_x <= w["box"]["x"] + w["box"]["w"] + 14)
                        and (w["box"]["y"] - 14 <= target_y <= w["box"]["y"] + w["box"]["h"] + 14)
                        for w in winrt_words
                    )
                    if in_sub or not direct_hit:
                        merged_words = list(winrt_words)
                        for sr in sub_regions[:2]:
                            sw = self.recognize_outlined_subtitle_words(
                                sr["crop"], origin_x + sr["origin_x"], origin_y + sr["origin_y"]
                            )
                            if sw:
                                merged_words.extend(sw)
                        if len(merged_words) > len(winrt_words):
                            return self._match_and_build_result(merged_words, target_x, target_y)
            except Exception:
                pass
            return winrt_res

        # 5. 若 WinRT 结果较差或质量存疑，先按区域尝试亮色描边视频字幕增强（严禁对整屏大图盲扫 RapidOCR 导致超时白屏）
        try:
            sub_regions = self.detect_outlined_subtitle_regions(img)
            sub_words: List[Dict[str, Any]] = []
            if sub_regions:
                for sr in sub_regions[:2]:
                    sub_words.extend(
                        self.recognize_outlined_subtitle_words(
                            sr["crop"], origin_x + sr["origin_x"], origin_y + sr["origin_y"]
                        )
                    )
            elif img.width * img.height <= 320000:
                sub_words = self.recognize_outlined_subtitle_words(img, origin_x, origin_y)
            if len(sub_words) > len(winrt_words):
                return self._match_and_build_result(sub_words, target_x, target_y)
        except Exception:
            pass
        try:
            hw_res = self._recognize_handwriting(img, origin_x, origin_y, target_x, target_y, allow_online=allow_online)
            if hw_res and hw_res.get("all_words"):
                if len(hw_res["all_words"]) > len(winrt_words) or hw_res.get("word"):
                    return hw_res
        except Exception as e:
            _log.warning("Handwriting OCR fallback error: %s", e)

        return winrt_res or {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

    def _recognize_handwriting(self, img: Image.Image, origin_x: int, origin_y: int, target_x: int, target_y: int, allow_online: bool = False) -> Dict[str, Any]:
        """
        手写笔记与草书专精识别管线 (本地离线 RapidOCR 双探针主力优先，严格遵守 allow_online 离线铁律)
        """
        words = self.recognize_handwriting_words(img, origin_x, origin_y, allow_online=allow_online)
        if words:
            return self._match_and_build_result(words, target_x, target_y)

        # 严格遵循 AGENTS.md 第 12.4 章：手写专精管线物理清退 WinRT OCR 兜底，严防草书脑补出火星文假词
        return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

    def _recognize_online_handwriting(self, img: Image.Image, origin_x: int, origin_y: int) -> List[Dict[str, Any]]:
        """轻量云端专精手写识别（支持各类连笔英文草书，带 60s 失败熔断保护）"""
        if time.time() < self._cloud_breaker_until:
            return []

        import os, io, base64, urllib.request, urllib.parse, json
        w, h = img.size
        max_dim = 1200
        scale = 1.0
        if w > max_dim or h > max_dim:
            scale = max_dim / max(w, h)
            target_size = (int(w * scale), int(h * scale))
            send_img = img.resize(target_size, Image.Resampling.LANCZOS)
        else:
            send_img = img

        buf = io.BytesIO()
        send_img.convert("RGB").save(buf, format="JPEG", quality=85)
        b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

        api_key = os.environ.get("SCREENLEX_OCR_KEY", "").strip() or "helloworld"

        data = urllib.parse.urlencode({
            "base64Image": "data:image/jpeg;base64," + b64_str,
            "apikey": api_key,
            "language": "eng",
            "isOverlayRequired": "true",
            "OCREngine": "2"
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.ocr.space/parse/image",
            data=data,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )

        words_out: List[Dict[str, Any]] = []
        inv_scale = 1.0 / scale

        try:
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                results = res.get("ParsedResults", [])
                if not results:
                    return []
                lines = results[0].get("TextOverlay", {}).get("Lines", [])
                for line_idx, line in enumerate(lines):
                    for w_item in line.get("Words", []):
                        raw_t = w_item.get("WordText", "").strip()
                        clean = clean_word_text(raw_t)
                        if not clean:
                            continue
                        lx = int(w_item.get("Left", 0) * inv_scale) + origin_x
                        ly = int(w_item.get("Top", 0) * inv_scale) + origin_y
                        lw = max(8, int(w_item.get("Width", 10) * inv_scale))
                        lh = max(8, int(w_item.get("Height", 10) * inv_scale))

                        words_out.append({
                            "text": raw_t,
                            "clean": clean,
                            "box": {"x": lx, "y": ly, "w": lw, "h": lh},
                            "center_x": lx + lw / 2,
                            "center_y": ly + lh / 2,
                            "line_id": line_idx
                        })
        except urllib.error.HTTPError as he:
            try:
                he.close()
            except Exception:
                pass
            self._cloud_breaker_until = time.time() + 60.0
            return []
        except Exception:
            self._cloud_breaker_until = time.time() + 60.0
            return []

        return words_out

    def recognize_handwriting_words(self, img: Image.Image, origin_x: int, origin_y: int, allow_online: bool = False) -> List[Dict[str, Any]]:
        """
        高可靠手写英文识别双轨管线：
        1. 优先主力：本地离线高反差色彩增强 + RapidOCR 双探针（0ms 网络开销，线程锁保护）；
        2. 云端通道兜底：严格仅在 allow_online=True 且本地无结果、未熔断时尝试，杜绝主线程/预提取线程同步联网。
        """
        # 1. 优先主力：本地离线 RapidOCR (线程锁安全保护)
        rapid = self._get_rapid_fallback()
        if rapid:
            try:
                import numpy as np
                from PIL import ImageOps
                # 全局自适应 Padding 保护：DBNet 文本行检测器要求文字周围具备上下边距，贴边裁切会导致概率核截断召回归零
                pad = 28
                padded_img = ImageOps.expand(img, border=pad, fill='white')

                # 双探针全量几何融合：墨迹图与原图各自独立推理，互补召回全部手写文本行
                # 仅在 C++ onnxruntime 推理期间持有互斥锁，NumPy 转换与 Python 几何融合在锁外并发执行
                preproc = self._preprocess_handwriting(padded_img)
                arr_preproc = np.array(preproc)
                arr_orig = np.array(padded_img.convert("RGB"))
                all_raw_results = []

                with self._rapid_lock:
                    ocr_res1, _ = rapid(arr_preproc, use_cls=False)
                    ocr_res2, _ = rapid(arr_orig, use_cls=False)

                if ocr_res1:
                    all_raw_results.extend(ocr_res1)

                if ocr_res2:
                    for item2 in ocr_res2:
                        poly2 = item2[0]
                        text2 = item2[1].strip()
                        conf2 = float(item2[2]) if len(item2) > 2 else 0.5

                        # 仅过滤纯中文或非英文噪点（例如界面按钮 "取消"、"松手查看释义"），保护中英混合笔记与草书符号
                        ascii_alpha2 = sum(1 for c in text2 if ('a' <= c <= 'z' or 'A' <= c <= 'Z'))
                        chinese2 = sum(1 for c in text2 if '\u4e00' <= c <= '\u9fff')
                        if ascii_alpha2 == 0 and len(text2) <= 4:
                            continue
                        if chinese2 >= 3 and ascii_alpha2 == 0:
                            continue

                        p2_y1 = min(pt[1] for pt in poly2)
                        p2_y2 = max(pt[1] for pt in poly2)
                        p2_x1 = min(pt[0] for pt in poly2)
                        p2_x2 = max(pt[0] for pt in poly2)
                        p2_h = max(1.0, p2_y2 - p2_y1)
                        p2_w = max(1.0, p2_x2 - p2_x1)

                        matched_idx = -1
                        for idx1, item1 in enumerate(all_raw_results):
                            poly1 = item1[0]
                            p1_y1 = min(pt[1] for pt in poly1)
                            p1_y2 = max(pt[1] for pt in poly1)
                            p1_x1 = min(pt[0] for pt in poly1)
                            p1_x2 = max(pt[0] for pt in poly1)
                            p1_h = max(1.0, p1_y2 - p1_y1)
                            p1_w = max(1.0, p1_x2 - p1_x1)

                            v_ov = max(0.0, min(p2_y2, p1_y2) - max(p2_y1, p1_y1))
                            h_ov = max(0.0, min(p2_x2, p1_x2) - max(p2_x1, p1_x1))
                            if (v_ov / min(p1_h, p2_h) >= 0.45) and (h_ov / min(p1_w, p2_w) >= 0.4):
                                matched_idx = idx1
                                break

                        # 清理被 item2 高度覆盖的其他碎片块（防止一对多切分导致的碎片残留）
                        cleaned_raw = []
                        for idx1, item1 in enumerate(all_raw_results):
                            if idx1 == matched_idx:
                                continue
                            poly1 = item1[0]
                            p1_y1 = min(pt[1] for pt in poly1)
                            p1_y2 = max(pt[1] for pt in poly1)
                            p1_x1 = min(pt[0] for pt in poly1)
                            p1_x2 = max(pt[0] for pt in poly1)
                            p1_h = max(1.0, p1_y2 - p1_y1)
                            p1_w = max(1.0, p1_x2 - p1_x1)
                            v_ov = max(0.0, min(p2_y2, p1_y2) - max(p2_y1, p1_y1))
                            h_ov = max(0.0, min(p2_x2, p1_x2) - max(p2_x1, p1_x1))
                            if (v_ov / p1_h >= 0.5) and (h_ov / p1_w >= 0.65) and (p2_w >= p1_w * 1.5):
                                continue
                            cleaned_raw.append(item1)

                        if matched_idx == -1:
                            cleaned_raw.append(item2)
                        else:
                            item1 = all_raw_results[matched_idx]
                            conf1 = float(item1[2]) if len(item1) > 2 else 0.5
                            alpha1 = sum(1 for c in item1[1] if ('a' <= c <= 'z' or 'A' <= c <= 'Z'))
                            if (conf2 > conf1 + 0.15) or (ascii_alpha2 >= alpha1 + 3 and conf2 >= 0.5):
                                cleaned_raw.append(item2)
                            else:
                                cleaned_raw.append(item1)
                        all_raw_results = cleaned_raw

                if all_raw_results:
                    all_words: List[Dict[str, Any]] = []
                    for line_idx, item in enumerate(all_raw_results):
                        poly = item[0]
                        text = item[1].strip()
                        # 鲁棒四边形包围盒解析：精确抵消 Padding 偏移并限制在局部切片有效几何范围内
                        min_px = min(pt[0] for pt in poly)
                        max_px = max(pt[0] for pt in poly)
                        min_py = min(pt[1] for pt in poly)
                        max_py = max(pt[1] for pt in poly)
                        loc_x1 = max(0.0, min_px - pad)
                        loc_y1 = max(0.0, min_py - pad)
                        loc_x2 = min(float(img.width), max(loc_x1 + 10.0, max_px - pad))
                        loc_y2 = min(float(img.height), max(loc_y1 + 10.0, max_py - pad))
                        lx1 = loc_x1 + origin_x
                        ly1 = loc_y1 + origin_y
                        lx2 = loc_x2 + origin_x
                        ly2 = loc_y2 + origin_y
                        line_w = max(10, lx2 - lx1)
                        line_h = max(10, ly2 - ly1)
                        tokens = text.split(" ")
                        total_chars = max(1, len(text))
                        char_w = line_w / total_chars
                        cur_char_offset = 0
                        for t in tokens:
                            clean = clean_word_text(t)
                            t_len = len(t)
                            if clean and any(c.isascii() and c.isalnum() for c in clean) and not any('\u4e00' <= c <= '\u9fff' for c in clean):
                                wx1 = int(lx1 + cur_char_offset * char_w)
                                wx2 = int(wx1 + t_len * char_w)
                                wy1 = int(ly1)
                                wy2 = int(ly2)
                                bw = max(8, wx2 - wx1)
                                bh = max(8, wy2 - wy1)
                                all_words.append({
                                    "text": t,
                                    "clean": clean,
                                    "box": {"x": wx1, "y": wy1, "w": bw, "h": bh},
                                    "center_x": wx1 + bw / 2,
                                    "center_y": wy1 + bh / 2,
                                    "line_id": line_idx
                                })
                            cur_char_offset += t_len + 1

                    # 水平重叠碎片去重：同行动态去重（若词块显著水平与垂直交叠，保留较长/较完整的词）
                    if all_words:
                        deduped = []
                        words_by_len = sorted(all_words, key=lambda w: len(w.get("clean", "")), reverse=True)
                        for w in words_by_len:
                            bx1 = w["box"]["x"]
                            bx2 = bx1 + w["box"]["w"]
                            by1 = w["box"]["y"]
                            by2 = by1 + w["box"]["h"]
                            is_dup = False
                            for kept in deduped:
                                kx1 = kept["box"]["x"]
                                kx2 = kx1 + kept["box"]["w"]
                                ky1 = kept["box"]["y"]
                                ky2 = ky1 + kept["box"]["h"]
                                v_ov = max(0, min(by2, ky2) - max(by1, ky1))
                                h_ov = max(0, min(bx2, kx2) - max(bx1, kx1))
                                min_w = max(1, min(w["box"]["w"], kept["box"]["w"]))
                                min_h = max(1, min(w["box"]["h"], kept["box"]["h"]))
                                if (v_ov / min_h > 0.4) and (h_ov / min_w > 0.5):
                                    is_dup = True
                                    break
                            if not is_dup:
                                deduped.append(w)
                        all_words = deduped

                    if all_words:
                        return all_words
            except Exception:
                pass

        # 2. 备用通道：云端识别（严格仅在显式允许 allow_online=True 且未熔断时尝试，遵守 AGENTS.md 第 1.1 章离线铁律）
        if allow_online and time.time() >= self._cloud_breaker_until:
            try:
                words = self._recognize_online_handwriting(img, origin_x, origin_y)
                if words:
                    return words
            except Exception:
                pass

        # 彻底清退手写专精管线内部的 WinRT 兜底，严禁 WinRT 接触手写墨迹产生火星文假词
        return []

    def _preprocess_handwriting(self, img: Image.Image) -> Image.Image:
        """笔迹色彩分离与高反差预处理：针对红/蓝/黑手写笔迹提取纯净黑白墨迹并轻度形态学连贯增强"""
        if img.mode in ('RGBA', 'LA'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            rgb_img = bg
        else:
            rgb_img = img.convert('RGB')

        try:
            import numpy as np
            arr = np.array(rgb_img, dtype=np.float32)
            r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
            # 多色墨迹色彩分离：覆盖红笔、蓝笔、绿笔、紫笔，同时抑制白底、灰底与黑色印刷体
            r_diff = r - (g + b) / 2.0
            b_diff = b - (r + g) / 2.0
            g_diff = g - (r + b) / 2.0
            diff = np.maximum(0, np.maximum(r_diff, np.maximum(b_diff, g_diff)))
            # 若检测到明显彩色墨迹，提取反向高纯度黑白图并进行轻度连笔笔画增强
            if np.percentile(diff, 99) > 25:
                p99 = max(1.0, float(np.percentile(diff, 99)))
                ink = np.clip((diff / p99) * 255.0, 0, 255).astype(np.uint8)
                try:
                    from PIL import ImageFilter
                    ink_p = Image.fromarray(ink, mode='L')
                    # 1px 轻度连笔笔画增强 (MaxFilter 相当于形态学膨胀，零外部依赖)
                    ink_p = ink_p.filter(ImageFilter.MaxFilter(size=3))
                    ink = np.array(ink_p)
                except Exception:
                    pass
                clean_img = Image.fromarray(255 - ink).convert('RGB')
                return clean_img
        except Exception:
            pass

        from PIL import ImageEnhance
        gray = rgb_img.convert('L')
        enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
        return enhanced.convert('RGB')

    def detect_handwriting_regions(self, img: Image.Image) -> List[Dict[str, Any]]:
        """
        毫秒级智能探测屏幕上的彩色手写笔迹区域（红笔、蓝笔、绿笔墨迹）。
        返回手写切片及其在原图中的绝对物理坐标 (crop_img, origin_x, origin_y)。
        纯 numpy 矩阵运算，耗时仅 3~6ms，绝不阻塞主线程。
        """
        try:
            import numpy as np
            w, h = img.size
            if w < 50 or h < 30:
                return []

            scale = 1.0
            if w > 1920:
                scale = 1920.0 / w
                detect_img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.NEAREST)
            else:
                detect_img = img

            arr = np.array(detect_img.convert("RGB"), dtype=np.int16)
            r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

            # 精准彩色墨迹滤波（纯 int16 定点比较，消除 float64 >110 MB 瞬时堆分配）
            is_red = (r > 125) & (r * 20 > g * 27 + 400) & (r * 4 > b * 5 + 80) & (g < 135)
            is_blue = (b > 125) & (b * 20 > r * 27 + 400) & (b * 4 > g * 5 + 80) & (r < 135)
            is_green = (g > 125) & (g * 20 > r * 27 + 400) & (g * 4 > b * 5 + 80) & (r < 135)
            is_pen = is_red | is_blue | is_green

            dh, dw = is_pen.shape
            if h >= 900:
                top_margin = int(120 * (h / 1600.0))
                bottom_margin = int(220 * (h / 1600.0))
                if dh > top_margin + bottom_margin + 100:
                    is_pen[:top_margin, :] = False
                    is_pen[dh - bottom_margin:, :] = False

            row_counts = np.sum(is_pen, axis=1)
            active_rows = np.where(row_counts >= 15)[0]
            if len(active_rows) == 0:
                return []

            clusters = []
            cur_s = active_rows[0]
            cur_e = active_rows[0]
            for r_idx in active_rows[1:]:
                # 行间距容差 50px，自适应汇聚手写笔记段落
                if r_idx - cur_e <= 50:
                    cur_e = r_idx
                else:
                    if cur_e - cur_s >= 8:
                        clusters.append((cur_s, cur_e))
                    cur_s = r_idx
                    cur_e = r_idx
            if cur_e - cur_s >= 8:
                clusters.append((cur_s, cur_e))

            # 二次聚合：若相邻手写段落垂直间距 <= 65px，合并为一个整体切片（大幅减少 OCR 延迟与内存开销）
            merged_clusters = []
            for c in clusters:
                if not merged_clusters:
                    merged_clusters.append(list(c))
                else:
                    prev = merged_clusters[-1]
                    if c[0] - prev[1] <= 65:
                        prev[1] = max(prev[1], c[1])
                    else:
                        merged_clusters.append(list(c))

            results = []
            inv_s = 1.0 / scale
            for r_s, r_e in merged_clusters:
                mask_band = is_pen[r_s:r_e+1, :]
                c_counts = np.sum(mask_band, axis=0)
                c_active = np.where(c_counts > 0)[0]
                if len(c_active) == 0:
                    continue

                # 水平段自适应拆分：若列间隔 > 80px，拆分为独立切片，防止两端孤立图案将整行正文大标题打包成巨大切片
                h_segs = []
                seg_s = c_active[0]
                seg_e = c_active[0]
                for col in c_active[1:]:
                    if col - seg_e <= 80:
                        seg_e = col
                    else:
                        h_segs.append((seg_s, seg_e))
                        seg_s = col
                        seg_e = col
                h_segs.append((seg_s, seg_e))

                for hs, he in h_segs:
                    sub_band = mask_band[:, hs:he+1]
                    if np.sum(sub_band) < 35:
                        continue
                    orig_x1 = max(0, int((hs - 40) * inv_s))
                    orig_x2 = min(w, int((he + 40) * inv_s))
                    orig_y1 = max(0, int((r_s - 25) * inv_s))
                    orig_y2 = min(h, int((r_e + 25) * inv_s))

                    if (orig_x2 - orig_x1 >= 40) and (orig_y2 - orig_y1 >= 15):
                        crop_piece = img.crop((orig_x1, orig_y1, orig_x2, orig_y2))
                        results.append({
                            "crop": crop_piece,
                            "origin_x": orig_x1,
                            "origin_y": orig_y1,
                            "box": (orig_x1, orig_y1, orig_x2 - orig_x1, orig_y2 - orig_y1)
                        })

            # 若存在大面积手写笔记主体（>40000 像素），过滤掉微小离散噪点（<3500 像素）
            if len(results) > 1:
                max_area = max(r["box"][2] * r["box"][3] for r in results)
                if max_area > 40000:
                    results = [r for r in results if (r["box"][2] * r["box"][3]) >= 3500]

            return results
        except Exception:
            return []

    @staticmethod
    def _np_sliding_min(arr, radius: int):
        out = arr.copy()
        for step in (1, 2, 4):
            if step > radius:
                break
            pad_x = np.pad(out, ((0, 0), (step, step)), mode="edge")
            out = np.minimum(out, np.minimum(pad_x[:, :-2 * step], pad_x[:, 2 * step:]))
        for step in (1, 2, 4):
            if step > radius:
                break
            pad_y = np.pad(out, ((step, step), (0, 0)), mode="edge")
            out = np.minimum(out, np.minimum(pad_y[:-2 * step, :], pad_y[2 * step:, :]))
        return out

    def detect_outlined_subtitle_regions(self, img: Image.Image) -> List[Dict[str, Any]]:
        """
        毫秒级探测亮色暗描边视频字幕条区（精准区分复杂背景白字与白底黑字幻灯片/文档）。
        """
        if np is None:
            return []
        try:
            w, h = img.size
            if w < 80 or h < 28:
                return []
            scale = min(1.0, 640.0 / max(1, w))
            dimg = (
                img.resize((max(16, int(w * scale)), max(16, int(h * scale))), Image.Resampling.NEAREST)
                if scale < 1.0
                else img
            )
            arr = np.asarray(dimg.convert("RGB"), dtype=np.float32)
            sat = arr.max(axis=2) - arr.min(axis=2)
            lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
            lmin4 = self._np_sliding_min(lum, 4)
            lmin7 = self._np_sliding_min(lum, 7)
            mask = (lum >= 165.0) & (sat <= 60.0) & (lmin4 <= 125.0) & (lmin7 <= 150.0) & ((lum - lmin7) >= 42.0)

            dh, dw = mask.shape
            row_cnt = mask.sum(axis=1)
            active_rows = np.where(row_cnt >= max(5, int(dw * 0.015)))[0]
            if len(active_rows) == 0:
                return []

            bands = []
            bs = be = int(active_rows[0])
            gap_y = max(5, int(14 * scale))
            for ry in active_rows[1:]:
                ry_i = int(ry)
                if ry_i - be <= gap_y:
                    be = ry_i
                else:
                    bands.append((bs, be))
                    bs = be = ry_i
            bands.append((bs, be))

            results: List[Dict[str, Any]] = []
            inv = 1.0 / scale
            gap_x = max(12, int(65 * scale))
            for rs, re_y in bands:
                cols = np.where(mask[rs : re_y + 1, :].sum(axis=0) >= 1)[0]
                if len(cols) < 12:
                    continue
                cs = ce = int(cols[0])
                segs = []
                for c in cols[1:]:
                    ci = int(c)
                    if ci - ce <= gap_x:
                        ce = ci
                    else:
                        if ce - cs >= max(10, int(24 * scale)):
                            segs.append((cs, ce))
                        cs = ce = ci
                if ce - cs >= max(10, int(24 * scale)):
                    segs.append((cs, ce))

                for sx1, sx2 in segs:
                    roi = lum[max(0, rs - 3) : min(dh, re_y + 4), max(0, sx1 - 3) : min(dw, sx2 + 4)]
                    if float(np.mean(roi > 210.0)) > 0.70:
                        continue
                    px1 = max(0, int(sx1 * inv) - 36)
                    py1 = max(0, int(rs * inv) - 24)
                    px2 = min(w, int((sx2 + 1) * inv) + 36)
                    py2 = min(h, int((re_y + 1) * inv) + 24)
                    if (px2 - px1) >= 80 and (py2 - py1) >= 22:
                        results.append({
                            "crop": img.crop((px1, py1, px2, py2)),
                            "origin_x": px1,
                            "origin_y": py1,
                            "box": (px1, py1, px2 - px1, py2 - py1),
                        })
            return results
        except Exception:
            return []

    @staticmethod
    def _isolate_outlined_bright_text(img: Image.Image) -> Image.Image:
        """2x Lanczos 上采样 + 纯 NumPy 极速暗边亮字中性通道提取，将斑驳视频背景上的白字隔离为白底黑字。"""
        up = img.resize((max(2, img.width * 2), max(2, img.height * 2)), Image.Resampling.LANCZOS).convert("RGB")
        if np is None:
            return ImageOps.expand(up, border=24, fill="black")
        arr = np.asarray(up, dtype=np.float32)
        sat = arr.max(axis=2) - arr.min(axis=2)
        lum = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
        neutral_score = np.clip((lum - 135.0) / 85.0, 0.0, 1.0) * np.clip(1.0 - np.maximum(0.0, sat - 28.0) / 55.0, 0.0, 1.0)
        dark_min = WindowsMediaOCR._np_sliding_min(lum, 7)
        halo_weight = np.clip((125.0 - dark_min) / 55.0, 0.0, 1.0)
        ink = np.clip(neutral_score * halo_weight * 255.0, 0.0, 255.0)
        soft_bw = Image.fromarray((255.0 - ink).astype(np.uint8), mode="L").convert("RGB")
        return ImageOps.expand(soft_bw, border=24, fill="white")

    def recognize_outlined_subtitle_words(
        self,
        img: Image.Image,
        origin_x: int = 0,
        origin_y: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        亮色描边视频字幕双引擎高召回提取：
        1. 2x 软描边隔离 + WinRT 获取精准逐词像素包围盒；
        2. 当 WinRT 存在碎裂符号/断点或召回不足时，补充 2x Lanczos RapidOCR + 词典粘连拆分按行融合。
        """
        if img is None or img.width < 20 or img.height < 12:
            return []
        scale = 2.0
        pad = 24
        soft_words: List[Dict[str, Any]] = []
        try:
            soft_pad = self._isolate_outlined_bright_text(img)
            sw_res = asyncio.run(
                self._recognize_winrt_async(
                    soft_pad, 0, 0, soft_pad.width // 2, soft_pad.height // 2, raw_only=True
                )
            )
            for w in (sw_res or {}).get("all_words", []):
                txt = (w.get("text") or "").strip()
                clean = clean_word_text(txt)
                if not txt or not clean:
                    continue
                if any("\u4e00" <= c <= "\u9fff" for c in txt):
                    continue
                b = w["box"]
                lx1 = max(0.0, (b["x"] - pad) / scale) + origin_x
                ly1 = max(0.0, (b["y"] - pad) / scale) + origin_y
                lx2 = min(float(img.width), max(1.0, (b["x"] + b["w"] - pad) / scale)) + origin_x
                ly2 = min(float(img.height), max(1.0, (b["y"] + b["h"] - pad) / scale)) + origin_y
                bw = max(6, int(round(lx2 - lx1)))
                bh = max(8, int(round(ly2 - ly1)))
                wx = int(round(lx1))
                wy = int(round(ly1))
                soft_words.append({
                    "text": txt,
                    "clean": clean,
                    "box": {"x": wx, "y": wy, "w": bw, "h": bh},
                    "center_x": wx + bw / 2.0,
                    "center_y": wy + bh / 2.0,
                    "line_id": w.get("line_id", 0),
                })
        except Exception:
            soft_words = []

        from .ocr_helpers import _ensure_glued_lexicon
        core_words, long_words = _ensure_glued_lexicon()

        def _is_known_token(w: Dict[str, Any]) -> bool:
            cl = (w.get("clean") or "").lower().strip("'")
            if not cl or not cl.isascii():
                return False
            parts = [p for p in cl.split("-") if p]
            if not parts:
                return False
            return all(
                (p in core_words) or (p in long_words)
                for p in parts
            )

        # 检查 WinRT 描边提取质量：若清晰完整且无生僻拼写坏词，优先直用；仅当有碎裂、生僻错词或稀疏时启动 2x RapidOCR 按行补全/替换
        def _has_fragment_noise(words_list: List[Dict[str, Any]]) -> bool:
            if len(words_list) < 2:
                return True
            dot_or_bad = sum(
                1 for w in words_list
                if ("•" in w["text"])
                or (len(w["clean"]) == 1 and w["clean"].lower() not in ("a", "i"))
            )
            if dot_or_bad >= 2 or any("•" in w["text"] for w in words_list):
                return True
            for w in words_list:
                cl = w.get("clean") or ""
                if len(cl) >= 5 and cl.isascii() and cl.isalpha() and cl.islower() and cl not in long_words:
                    return True
            return False

        rapid_words: List[Dict[str, Any]] = []
        if (_has_fragment_noise(soft_words) or not soft_words) and img.width * img.height <= 450000:
            rapid = self._get_rapid_fallback()
            if rapid and np is not None:
                try:
                    # 仅超矮单行做 1.5x 上采样；常规双行直接 1.0x + 224px 安全竖向衬边（耗时 ~130ms，兼顾高精度与不卡顿）
                    r_scale = 1.5 if img.height < 48 else 1.0
                    up = (
                        img.resize((max(2, int(round(img.width * r_scale))), max(2, int(round(img.height * r_scale)))), Image.Resampling.BILINEAR).convert("RGB")
                        if r_scale != 1.0
                        else img.convert("RGB")
                    )
                    pad_y = max(pad, (224 - up.height) // 2)
                    up_pad = ImageOps.expand(up, border=(pad, pad_y, pad, pad_y), fill=(20, 20, 35))
                    arr_up = np.array(up_pad)
                    with self._rapid_lock:
                        r_res, _ = rapid(arr_up, use_cls=False)
                    _LN_FOLLOWERS = frozenset({
                        "the", "a", "an", "this", "that", "these", "those",
                        "our", "my", "his", "her", "its", "their",
                        "order", "fact", "addition", "contrast", "practice", "theory",
                    })
                    for row_idx, item in enumerate(r_res or []):
                        poly = item[0]
                        raw_t = (item[1] or "").strip()
                        if not raw_t:
                            continue
                        if any("\u4e00" <= c <= "\u9fff" for c in raw_t):
                            continue
                        ascii_alpha = sum(1 for c in raw_t if c.isascii() and c.isalpha())
                        if ascii_alpha < 2:
                            continue
                        min_px = min(pt[0] for pt in poly)
                        max_px = max(pt[0] for pt in poly)
                        min_py = min(pt[1] for pt in poly)
                        max_py = max(pt[1] for pt in poly)
                        lx1 = max(0.0, (min_px - pad) / r_scale) + origin_x
                        ly1 = max(0.0, (min_py - pad_y) / r_scale) + origin_y
                        lx2 = min(float(img.width), max(2.0, (max_px - pad) / r_scale)) + origin_x
                        ly2 = min(float(img.height), max(2.0, (max_py - pad_y) / r_scale)) + origin_y
                        line_w = max(8.0, lx2 - lx1)
                        line_h = max(8.0, ly2 - ly1)
                        expanded_toks: List[str] = []
                        for tok in raw_t.split():
                            expanded_toks.extend(split_glued_ocr_token(tok))
                        if not expanded_toks:
                            continue
                        for ti in range(len(expanded_toks) - 1):
                            if expanded_toks[ti].lower() == "ln":
                                nxt_clean = clean_word_text(expanded_toks[ti + 1]).lower()
                                if nxt_clean in _LN_FOLLOWERS:
                                    expanded_toks[ti] = "In" if ti == 0 or expanded_toks[ti][:1].isupper() else "in"
                        joined = " ".join(expanded_toks)
                        char_w = line_w / max(1, len(joined))
                        cur_off = 0
                        for et in expanded_toks:
                            clean = clean_word_text(et)
                            et_len = len(et)
                            if clean and any(c.isascii() and c.isalnum() for c in clean):
                                wx1 = int(round(lx1 + cur_off * char_w))
                                wx2 = int(round(wx1 + et_len * char_w))
                                bw = max(6, wx2 - wx1)
                                bh = max(8, int(round(line_h)))
                                wy = int(round(ly1))
                                rapid_words.append({
                                    "text": et,
                                    "clean": clean,
                                    "box": {"x": wx1, "y": wy, "w": bw, "h": bh},
                                    "center_x": wx1 + bw / 2.0,
                                    "center_y": wy + bh / 2.0,
                                    "line_id": row_idx,
                                })
                            cur_off += et_len + 1
                except Exception:
                    rapid_words = []

        if not soft_words:
            return sort_words_reading_order(rapid_words)
        if not rapid_words:
            return sort_words_reading_order(soft_words)

        # 按行对齐质量：仅对软描边存在碎裂词（如 • / 断字）的区间用 2x RapidOCR+拆分结果替换
        ordered_soft = sort_words_reading_order(soft_words)
        ordered_rapid = sort_words_reading_order(rapid_words)

        def _line_quality_stats(ws: List[Dict[str, Any]]) -> Tuple[bool, int, int]:
            has_hard_bad = any(
                ("•" in x["text"]) or (len(x["clean"]) == 1 and x["clean"].lower() not in ("a", "i"))
                for x in ws
            )
            bad_oov = 0
            known = 0
            for x in ws:
                cl = (x.get("clean") or "").strip()
                if not cl or not any(c.isascii() and c.isalpha() for c in cl):
                    continue
                if _is_known_token(x):
                    known += 1
                elif len(cl) >= 4 and cl.isascii() and cl.isalpha():
                    bad_oov += 1
            return has_hard_bad, bad_oov, known

        soft_lines: Dict[int, List[Dict[str, Any]]] = {}
        for sw in ordered_soft:
            soft_lines.setdefault(sw.get("line_id", 0), []).append(sw)

        rapid_lines: Dict[int, List[Dict[str, Any]]] = {}
        for rw in ordered_rapid:
            rapid_lines.setdefault(rw.get("line_id", 0), []).append(rw)

        final_words: List[Dict[str, Any]] = []
        used_rapid_lids = set()
        for slid, s_ws in sorted(soft_lines.items()):
            sy1 = min(w["box"]["y"] for w in s_ws)
            sy2 = max(w["box"]["y"] + w["box"]["h"] for w in s_ws)
            best_rlid = None
            best_ov = 0.0
            for rlid, r_ws in rapid_lines.items():
                ry1 = min(w["box"]["y"] for w in r_ws)
                ry2 = max(w["box"]["y"] + w["box"]["h"] for w in r_ws)
                ov = max(0, min(sy2, ry2) - max(sy1, ry1))
                if ov > best_ov:
                    best_ov = float(ov)
                    best_rlid = rlid
            s_hard_bad, s_bad_oov, s_known = _line_quality_stats(s_ws)
            if best_rlid is not None and best_ov > 0:
                r_ws = rapid_lines[best_rlid]
                _, r_bad_oov, r_known = _line_quality_stats(r_ws)
                need_replace = s_hard_bad or (s_bad_oov > 0 and r_bad_oov < s_bad_oov and r_known >= s_known)
                if need_replace:
                    s_alpha = sum(sum(1 for c in w["clean"] if c.isascii() and c.isalpha()) for w in s_ws)
                    r_alpha = sum(sum(1 for c in w["clean"] if c.isascii() and c.isalpha()) for w in r_ws)
                    if r_alpha >= max(3, int(s_alpha * 0.75)):
                        final_words.extend(r_ws)
                        used_rapid_lids.add(best_rlid)
                        continue
            final_words.extend(s_ws)

        # 补充 WinRT 完全漏掉的行（如 IMG1/IMG2 上下整行缺失）
        for rlid, r_ws in sorted(rapid_lines.items()):
            if rlid in used_rapid_lids:
                continue
            ry1 = min(w["box"]["y"] for w in r_ws)
            ry2 = max(w["box"]["y"] + w["box"]["h"] for w in r_ws)
            has_soft_line = any(
                max(0, min(ry2, max(w["box"]["y"] + w["box"]["h"] for w in sws)) - max(ry1, min(w["box"]["y"] for w in sws)))
                >= 0.35 * max(8, ry2 - ry1)
                for sws in soft_lines.values()
            )
            if not has_soft_line:
                final_words.extend(r_ws)

        return sort_words_reading_order(final_words)

    async def _recognize_winrt_async(self, img: Image.Image, origin_x: int, origin_y: int, target_x: int, target_y: int, raw_only: bool = False) -> Dict[str, Any]:
        # 针对 ClearType 次像素渲染与细衬线字符（如 Computer Modern）笔画丢失进行画质增强
        # 注意：全屏原生截图无需耗时 100ms+ 的滤波变换，仅对局部裁切块应用增强以避免后台线程独占 GIL 造成鼠标卡顿
        w, h = img.size
        if w * h < 500000:
            from PIL import ImageEnhance
            try:
                enhanced = ImageEnhance.Contrast(img).enhance(1.4)
                enhanced = ImageEnhance.Sharpness(enhanced).enhance(1.25)
            except Exception:
                enhanced = img
        else:
            enhanced = img

        w, h = enhanced.size
        raw_bytes = enhanced.convert('RGBA').tobytes()

        with streams.DataWriter() as writer:
            writer.write_bytes(raw_bytes)
            buf = writer.detach_buffer()

        result = None
        with imaging.SoftwareBitmap.create_copy_from_buffer(
            buf,
            imaging.BitmapPixelFormat.RGBA8,
            w,
            h
        ) as sb:
            engine_en = self._get_engine("en-US")
            if engine_en:
                result = await engine_en.recognize_async(sb)

            if not result or len(result.lines) == 0:
                engine_zh = self._get_engine("zh-Hans-CN")
                if engine_zh:
                    result = await engine_zh.recognize_async(sb)

        if not result or len(result.lines) == 0:
            return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

        # 搜集所有文字块，保留相对屏幕的绝对物理坐标并严格绑定原生行号 line_id
        all_words: List[Dict[str, Any]] = []
        for line_idx, line in enumerate(result.lines):
            for w_obj in line.words:
                raw_text = w_obj.text.strip()
                if not raw_text:
                    continue
                r = w_obj.bounding_rect
                bx = origin_x + int(r.x)
                by = origin_y + int(r.y)
                bw = int(r.width)
                bh = int(r.height)
                clean = clean_word_text(raw_text)

                all_words.append({
                    "text": raw_text,
                    "clean": clean,
                    "box": {"x": bx, "y": by, "w": bw, "h": bh},
                    "center_x": bx + bw / 2,
                    "center_y": by + bh / 2,
                    "line_id": line_idx,
                    "ocr_source": "winrt",
                })

        if raw_only:
            return {
                "word": "",
                "sentence": "",
                "box": None,
                "line_words": [],
                "all_words": sort_words_reading_order(all_words),
                "initial_index": -1,
            }

        return self._match_and_build_result(all_words, target_x, target_y)

    def _recognize_rapid(self, img: Image.Image, origin_x: int, origin_y: int, target_x: int, target_y: int, raw_only: bool = False) -> Dict[str, Any]:
        rapid = self._get_rapid_fallback()
        if not rapid:
            return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

        import numpy as np
        img_np = np.array(img)
        with self._rapid_lock:
            ocr_res, _ = rapid(img_np)
        if not ocr_res:
            return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

        all_words: List[Dict[str, Any]] = []
        for line_idx, item in enumerate(ocr_res):
            poly = item[0]
            text = item[1].strip()
            lx1 = min(pt[0] for pt in poly) + origin_x
            ly1 = min(pt[1] for pt in poly) + origin_y
            lx2 = max(pt[0] for pt in poly) + origin_x
            ly2 = max(pt[1] for pt in poly) + origin_y
            line_w = max(10, lx2 - lx1)
            line_h = max(10, ly2 - ly1)

            tokens = text.split(" ")
            total_chars = max(1, len(text))
            char_w = line_w / total_chars

            cur_char_offset = 0
            for t in tokens:
                clean = clean_word_text(t)
                t_len = len(t)
                if clean:
                    wx1 = int(lx1 + cur_char_offset * char_w)
                    wx2 = int(wx1 + t_len * char_w)
                    wy1 = int(ly1)
                    wy2 = int(ly2)
                    bw = max(8, wx2 - wx1)
                    bh = max(8, wy2 - wy1)

                    all_words.append({
                        "text": t,
                        "clean": clean,
                        "box": {"x": wx1, "y": wy1, "w": bw, "h": bh},
                        "center_x": wx1 + bw / 2,
                        "center_y": wy1 + bh / 2,
                        "line_id": line_idx
                    })
                cur_char_offset += t_len + 1

        if raw_only:
            return {
                "word": "",
                "sentence": "",
                "box": None,
                "line_words": [],
                "all_words": sort_words_reading_order(all_words),
                "initial_index": -1,
            }

        return self._match_and_build_result(all_words, target_x, target_y)

    def _match_and_build_result(self, raw_words: List[Dict[str, Any]], target_x: int, target_y: int) -> Dict[str, Any]:
        if not raw_words:
            return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": [], "initial_index": -1}

        # 核心：将全屏所有词块按自然行阅读顺序聚类，并进行智能子词切分与断词拼合（解决形如 'desktop/' 截断与 'desk+top' 误断裂问题）
        ordered_words = sort_words_reading_order(raw_words)
        all_words = process_ocr_tokens(ordered_words)

        def dist_to_box(px: int, py: int, b: Dict[str, int]) -> float:
            dx = max(b["x"] - px, 0, px - (b["x"] + b["w"]))
            dy = max(b["y"] - py, 0, py - (b["y"] + b["h"]))
            return (dx * dx + dy * dy) ** 0.5

        # 1. 查找直接命中触控坐标的词（针对二合一触控屏扩大至 28px 物理指尖容差）
        direct_hits = []
        pad = 28
        for i, w in enumerate(all_words):
            box = w["box"]
            if (box["x"] - pad <= target_x <= box["x"] + box["w"] + pad) and \
               (box["y"] - pad <= target_y <= box["y"] + box["h"] + pad):
                center_dist = ((target_x - w["center_x"]) ** 2 + (target_y - w["center_y"]) ** 2) ** 0.5
                direct_hits.append((center_dist, i, w))

        target_idx = -1
        if direct_hits:
            alpha_hits = [h for h in direct_hits if any(c.isascii() and c.isalpha() for c in h[2]["clean"])]
            candidates = alpha_hits if alpha_hits else direct_hits
            candidates.sort(key=lambda x: x[0])
            target_idx = candidates[0][1]
        else:
            # 2. 无直接命中时，寻找距离外框最近的单词（容差提升至 120px）
            min_box_dist = float("inf")
            best_i = -1
            for i, w in enumerate(all_words):
                d = dist_to_box(target_x, target_y, w["box"])
                if d < min_box_dist:
                    min_box_dist = d
                    best_i = i

            if min_box_dist <= 120 and best_i != -1:
                target_idx = best_i

        if target_idx == -1:
            return {"word": "", "sentence": "", "box": None, "line_words": [], "all_words": all_words, "initial_index": -1}

        target_word = all_words[target_idx]
        sentence = extract_sentence_from_words(all_words, target_idx)

        line_words = [w for w in all_words if w.get("line_id") == target_word.get("line_id")]
        line_idx = next((i for i, w in enumerate(line_words) if w is target_word), 0)

        return {
            "word": target_word["clean"],
            "sentence": sentence,
            "box": target_word["box"],
            "all_words": all_words,
            "line_words": line_words,
            "initial_index": line_idx
        }

win_ocr_engine = WindowsMediaOCR()
