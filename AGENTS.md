# 🏛️ ScreenLex 项目架构规范与避坑动态记忆 (AGENTS.md)

> 本文档为开发与维护 ScreenLex 项目时的动态技术记忆库。记录关键设计边界、高频踩坑点与不可逾越的性能禁区。

---

## 1. ⚡ 核心性能与主线程 0 延迟铁律

1. **抬手释放 0ms 纯几何决算**：
   - 在 `FreezeSelectOverlay._handle_pointer_up` 中，抬手释放决算必须是基于已识别词库的纯内存几何计算（耗时必须控制在 `< 1ms`）；
   - **绝对禁止**在 UI 主线程中发起任何同步网络请求（如在线拼写纠错 `dict.youdao.com/suggest`）或耗时计算；
   - `grammar_checker.py` 中的 `check_spelling` 默认必须 `allow_online=False`，纯离线词库毫秒级返回；在线联机增强仅允许在 `enrich_lookup_sync` 等后台子线程中异步进行。
2. **禁止二次同步切图阻塞**：
   - 抬手时严禁调用 `force_crop_ocr` 在主线程重新截屏并等待 OCR。已有定格词库完全满足几何命中断言。

---

## 2. 🎯 手势多轨解耦架构规范

定格层选词手势存在三种截然不同的物理意图，在 `desktop/freeze_overlay.py` 中必须严格解耦：

1. **闭合多边形圈选 (Lasso Loop)**：
   - **判定准则**：`is_closed` 为 True 或形成闭合多边形；
   - **包含算法**：严格按 `point_in_polygon` 判定词块中点与采样点；
   - **边界红线**：圈中哪些词就选哪些词，**严禁**向圈外同行动词/介词/名词进行同行延展，以保护用户圈选单个特定单词的意图。
2. **矩形拉框框选 (Rectangular Drag Box)**：
   - **判定准则**：具有明显的垂直高度与长宽比（`sw < 2.2 * sh` 或 `sh >= 20`）；
   - **包含算法**：严格按词块几何中心 `(cx, cy)` 是否落在拉框包围盒 `[min_sx - pad_tol, max_sx + pad_tol]` 判定；
   - **边界红线**：严禁使用纯 AABB 相交（如 `bx2 >= min_sx`），否则会导致框边缘轻碰 1px 就误选相邻单词。
3. **直线横向划词 (Horizontal Strikethrough Swipe)**：
   - **判定准则**：水平跨度显著（`sw >= 28`），且具有明显的细长横划特征（`sw >= 2.2 * max(1, sh)` 或 `sh <= 16`）；
   - **包含算法**：划线穿过目标文本行，行内词块水平中心在 `[min_sx, max_sx]` 范围内，或词块水平区间重叠超过 25%；
   - **连贯覆盖**：划线水平覆盖区间内的所有同行词块全部连贯高亮，确保整行手写笔记一划而过 100% 完整捕获。

---

## 3. 🧪 自动化测试与环境隔离规约

1. **Qt 应用程序单例兼容**：
   - 单元测试文件中，初始化 Qt 上下文必须使用防御性单例探测：
     ```python
     app = QApplication.instance() or QApplication(sys.argv)
     # 或
     app = QCoreApplication.instance() or QCoreApplication(sys.argv)
     ```
   - 严禁盲目直接实例化，防止 `unittest discover` 跨测试模块批量执行时触发 `libshiboken: Please destroy the QApplication singleton` 致命错误。
2. **前台 GUI 唤起规约**：
   - 自动化启动前台桌面应用或调用浏览器时，在 Windows 环境必须使用 Scheduled Task (Interactive) 绕过 Session 0 隔离，弹出至用户桌面。

---

## 4. ✍️ 多模态混合排版与手写并发召回规约

1. **绝对禁止全屏印刷体掩盖手写笔记**：
   - 当屏幕上同时存在印刷体（代码/标题）与手写彩色笔记时，Windows 原生 WinRT OCR 对连笔手写笔记通常全盘拒识；
   - 严禁以“全屏已检出 >= 2 个印刷词”为由直接跳过手写识别；
   - 必须通过 `detect_handwriting_regions` 执行毫秒级（< 6ms）笔迹颜色/行带扫描，并在后台线程并发发起手写切片专精识别，识别结果动态合并入 `all_words`。
2. **手势划线按需识别与释放兜底**：
   - 若用户划线（$sw \ge 28$）或圈选区域内暂无已知词块，松手释放时**绝对严禁直接闪退关闭**；
   - 必须进入微光等待态并在后台对划线切片执行定向手写识别，识别成功后自动弹出全功能词典抽屉。

---

## 5. 🛡️ 离线优先、并发安全与会话世代规约

1. **并发推理锁安全**：
   - 本地 `RapidOCR` 底层维护 C++ 句柄与 NumPy 状态，多后台线程（如全屏预提取与局部切片提取）并发调用必须经由 `threading.Lock()` 互斥加锁保护，防止 `onnxruntime` 内存访问违例致进程崩溃。
2. **云端接口失败熔断机制 (Circuit Breaker)**：
   - 云端手写识别接口一旦遭遇 HTTP 429 限流、网络断连或超时，必须立即触发 60 秒失败熔断；
   - 熔断期内所有新发起的切片识别必须 0ms 瞬间跳过网络请求，直接进入本地离线 RapidOCR 降级管线，杜绝累计数秒的网络挂起。
3. **会话世代隔离 (Session / Generation ID)**：
   - `FreezeSelectOverlay` 每次定格铺开均自增 `_session_id`；
   - 所有后台异步任务必须绑定调用时刻的 `session_id`，槽函数回调必须校验 `if session_id != self._session_id: return`，严防旧屏幕脏数据污染新会话及离线幽灵弹窗。
4. **环路闭合与宽词包含算法**：
   - Lasso 闭合圈检测必须自适应剔除起笔引线（lead-in）与收尾甩笔飞白（flick tail），杜绝首尾跨数百像素相连产生穿心直弦割裂内部词汇；
   - 词块几何测试必须采用 AABB 包围盒重叠预检 + 8 点空间多重采样（`inside_cnt >= 2`），彻底杜绝中心点偏移一票否决 120px+ 宽长词，同时杜绝边缘词被轻碰 2px 误选。
5. **物理行聚类与 Token 重构时序**：
   - 必须严格遵循**先执行物理 Y 轴自适应聚类并分配单调递增 `line_id`（`sort_words_reading_order`），再执行词内连字符与拼写重构（`process_ocr_tokens`）**的时序铁律；
   - 同行聚类必须引入水平重叠抑制（$X$ 轴重叠比例 $> 40\%$ 则严禁归入同行），杜绝垂直叠字上下颠倒穿插。

---

## 6. 🧩 动态库初始化顺序与残渣词清洗规约

1. **Windows 下 ONNXRuntime 与 WinRT 导入时序铁律**：
   - 在 Windows Python 3.14 环境下，`winrt` 模块底层锁定了 Windows 运行时 C++ 上下文。若在 `winrt` 之后导入 `onnxruntime`，会导致 `onnxruntime_pybind11_state.pyd` 在 `DllMain` 初始化时崩溃并抛出 `A dynamic link library (DLL) initialization routine failed`；
   - 必须在任何业务模块与 `winrt` 导入前，在进程顶端最优先配置 `os.add_dll_directory` 并预载 `onnxruntime`。
2. **残渣碎片词包围盒清洗算法**：
   - WinRT 对手写笔迹经常误识出散碎的乱码词（如 `4.112`, `010U7tl{å`）；
   - 当手写识别（RapidOCR）高置信度多词提取完成时，必须通过 `inter_area / min(area1, area2) >= 0.35` 判定并剔除同一区域被包裹的原有 WinRT 碎片，严防乱码词混入污染手写词库。
3. **拒绝 Synthetic Mock 伪测试原则**：
   - 针对 OCR 视觉识别与多模态交互功能，单元测试严禁全盘使用人工 Mock 单词列表测试几何逻辑；必须至少包含真实截图的物理端到端实证用例（如 `test_real_user_handwriting.py`）。

---

## 7. 🧹 手写空间遮罩清洗与进程生命周期规约

1. **全屏预提取手写空间遮罩清洗 (Spatial Mask Sanitization)**：
   - WinRT OCR 面对手写笔迹会把连笔强行切碎，误猜出诸如 `4hhJ`, `hymnal`, `w)ll`, `b'H` 等数十个火星文假词；
   - 在 `FreezeSelectOverlay._pre_extract_all` 中，WinRT 完成全屏识别后，必须使用 `detect_handwriting_regions` 检测出的所有手写切片矩形进行几何求交：凡落入或与手写区域显著重叠（$\ge 30\%$）的 WinRT 词块必须**100% 物理丢弃过滤**；
   - 只有 RapidOCR 本地离线引擎专精识别的手写词块才允许注入该区域，从源头彻底杜绝火星文污染词典卡片和学术润色。
2. **手写墨迹形态学连贯增强 (0 外部依赖)**：
   - 针对彩色手写墨迹，使用纯 PIL 原生 `ImageFilter.MaxFilter(size=3)` 进行 1px 细微形态学膨胀，有效修补书写断笔并增强 RapidOCR 字符召回率，无需在发布包中引入沉重的 OpenCV 依赖。
3. **PyInstaller Windows 单实例与多进程兼容**：
   - `multiprocessing.freeze_support()` 必须在入口脚本的 `if __name__ == '__main__':` 顶端最优先执行；
   - 严禁在模块全局顶层直接执行 `psutil.process_iter` 查杀旧进程，必须局限在主入口逻辑中，防止 PyInstaller 派生的子进程误杀父进程或引发连锁静默退出。

---

## 8. 🛡️ 语法与拼写纠错抗噪音与词意保护规约

1. **构词法前缀与反义词保护铁律**：
   - 严禁将 `mis-`, `un-`, `dis-`, `non-` 等前缀派生词（如 `misunderstanding`）误判为去前缀后的词根（`understanding`）；
   - 前缀表达完全不同的语义或否定极性，加减前缀绝对不是手抖拼写错误，在拼写匹配中必须通过 `is_prefix_relation` 强制拒绝替换。
2. **OCR 标点排版噪音强力过滤**：
   - OCR 切词产生的 token 间空格（如 `first .`、`( N1 )`）必须在纠错前通过 `normalize_ocr_text` 预先规整收拢；
   - 彻底过滤 LanguageTool 返回的纯标点、空白符、排版格式类规则（如 `WHITESPACE`, `TYPOGRAPHY`, `COMMA_PARENTHESIS_WHITESPACE`），严禁向用户展示 ` . -> .`、`( -> (` 等荒谬排版纠错。
3. **数学与代码标识符免疫**：
   - 学术课堂与算法笔记中普遍存在的 `Nx`, `N1`, `kNN`, `O(N)`, `x_1` 等变量符号，必须通过 `is_math_or_code_identifier` 判定并 100% 免疫拼写纠错，严禁硬猜成普通词（如 `Nx -> No`）。

---

## 9. 🚫 WinRT 词数短路致命陷阱与手写安全隔离带

1. **严禁凭 `len(words) >= 2` 判定 WinRT 成功**：
   - WinRT OCR 面对复杂手写草书时会强行切碎并误识出数十个火星文假词（如 `4hhJ`, `Alms`, `w)ll`, `(AJ` 等）；
   - 绝对禁止仅凭检出词块数 $\ge 2$ 就提前短路退出；一旦图像检测出手写特征（`detect_handwriting_regions`），必须无条件将路由切换至本地专精 RapidOCR 双探针管线。
2. **手写外扩 25px 安全隔离带 AABB 强力排空**：
   - 全屏预提取印刷体文字时，凡与手写区域外扩 25px 安全隔离带有任何交叠的 WinRT 词块，必须 100% 物理丢弃，只允许注入 RapidOCR 专精识别的手写词块。
3. **定格截屏前置主动清理**：
   - 触发定格即选模式前，必须主动隐藏并关闭旧词典卡片并刷新事件循环（`QApplication.processEvents()`），彻底杜绝旧卡片残影被截入定格背景导致二次误选。

---

## 10. 📐 手写切片 DBNet 概率核自适应 Padding 与重叠去重规约

1. **DBNet 边缘概率截断物理铁律**：
   - RapidOCR/PaddleOCR 底层使用的 DBNet 文本行检测器依赖文字包围的高斯概率核，若输入图像切片紧贴文字上下边缘（边距 $< 15\text{px}$），概率热图在边缘处发生截断衰减，导致召回率瞬时归零；
   - 必须在进入 RapidOCR 推理前全局施加 $\ge 28\text{px}$ 的安全白色 Padding（`ImageOps.expand(..., border=28, fill='white')`），并在坐标还原阶段精准抵消偏移并限制在原切片几何范围内（`poly - pad`），确保 0 像素漂移；
   - 手写探测裁切 `detect_handwriting_regions` 与按需裁切 `_async_hw_recognize` 必须保证外扩裕量至少 $\ge 25\text{px} \times \text{dpr}$。
2. **多色墨迹分离与背景抑制**：
   - `_preprocess_handwriting` 必须使用 RGB 互斥最大差分 `np.maximum(0, max(r_diff, b_diff, g_diff))` 提取纯净黑白墨迹，同时对深底反色、灰白幻灯片背景和黑色印刷体实现 100% 抑制。
3. **负间距防误粘合与重叠碎片去重**：
   - 在 `process_ocr_tokens` 中，误断裂粘合必须强制使用 `0 <= gap <= 1`，严禁使用 `gap <= 1` 防止负间距（重叠文本框）将相邻独立单词误拼接为复合词；
   - 在 `recognize_handwriting_words` 内部，按词长降序基于垂直交叠比例 $> 40\%$ 与水平交叠比例 $> 50\%$ 剔除被完整长词包含的孤立字形碎片（如 `text` 包含的 `t`）。

---

## 11. 🔤 英文简缩词、富文本转义与算法手写语义强化规约

1. **英文简缩词切词保全 (Contractions Preservation)**：
   - 语法纠错切词正则严禁使用粗暴的 `\b[a-zA-Z0-9_]+\b`，否则会将 `doesn't`、`don't` 等简缩词残暴腰斩为 `doesn`、`don`，进而被误判为 `doesn -> does` 产生荒谬纠错；
   - 必须使用 `r"\b[a-zA-Z0-9_]+(?:'[a-zA-Z]+)?\b"` 完整提取带撇号简缩词，且 `is_valid_word` 必须注入 `ENGLISH_CONTRACTIONS` 包含白名单，100% 免疫拼写误纠。
2. **Qt 富文本与 HTML 转义隔离 (Anti-`&#x27;` Artifacts)**：
   - QLabel/QTextDocument 对 HTML 实体中单引号 `&#x27;` 无法正常解析，会直接作为纯文本暴露出 `does&#x27;t` 丑陋乱码；
   - 润色句子与纠错解释进入 UI 标签前必须使用 `html.escape(..., quote=False).replace("&#x27;", "'")`，彻底杜绝撇号变实体乱码。
3. **算法手写符号与上下文形近归一**：
   - 针对算法课件手写笔记，`process_ocr_tokens` 必须自适应纠正高频 OCR 字形扭曲：
     - `OCN)`, `O(n)`, `OC1)` 统一标准化为 `O(N)`, `O(1)` 等标准 Big-O 复杂度标记；
     - `the lap as` 在算法语境下自适应纠偏为 `the loop as`；
     - `be` + `low` 间距小且同行时自动聚合成复合词 `below`。

---

## 12. 🏹 纵向大括号括选、跨栏空白截断与手写 WinRT 彻底清退规约

1. **纵向大括号与常规拉框严密解耦**：
   - 大括号手势判定 `is_vertical_bracket` 必须施加严格纵向高宽比限制：`sh >= 40 and sw <= 150 and sh >= 1.5 * sw`；
   - 严禁无长宽比约束判定（如 `sh >= 60 and sw <= 240`），杜绝常规矩形拉框选多行短语被误判为大括号而全屏误选。
2. **笔迹端点几何拓扑判定朝向**：
   - 绝对严禁按两侧词数多寡盲猜大括号开口；
   - 必须基于端点几何拓扑 `(pt_start.x + pt_end.x) / 2 > min_sx + 0.25 * sw` 判定向右开口（`{`, `[`, `<` 括选右侧多行笔记）或向左开口（`}`, `]`, `>` 括选左侧多行笔记）。
3. **行内连贯聚类与跨栏空白自动截断 (MAX_GAP = 90px)**：
   - 在多栏网页、双栏文献或带侧边栏场景下，大括号扩选必须引入行内连贯距离算法；
   - 沿括选方向向前延伸，一旦相邻词块水平间距超过 90px（逻辑像素），立即触发截断，彻底杜绝跨屏误选 500px 外的侧边栏。
4. **手写专精管线物理清退 WinRT 与 Unicode `isalpha` 铁律**：
   - 手写专精管线（`recognize_handwriting_words` 与 `_async_hw_recognize`）必须彻底剔除所有 WinRT OCR 兜底调用，严防 WinRT 对草书墨迹灾难性脑补出火星文假词；
   - Python `str.isalpha()` 对汉字字符恒返回 `True`，过滤英文有效词必须严格使用 `c.isascii() and c.isalpha()`，彻底堵死汉字穿透。
5. **双探针全量几何融合与一对多碎片清洗**：
   - 墨迹预处理图与原图双探针推理必须全量几何融合，严禁凭 `len(ocr_res1) < 3` 提前短路遗漏首行手写；
   - 原图长行替换预处理短行时，必须清理被其水平覆盖超过 65% 的短碎片，杜绝重复行残留；四边形包围盒必须使用 `min/max` 解析，严禁写死对角点。

---

## 13. 🖥️ 真实桌面 Session 穿透、高分屏 DPR 与 PyInstaller PYZ 规约

1. **真实桌面计划任务电池供电与穿透规约**：
   - Windows 任务计划程序默认开启“电池供电时不启动”，笔记本断开电源时会导致 `Start-ScheduledTask` 静默跳过；必须显式配置 `New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`；
   - 后台任务唤起置顶前台窗口时，受系统前台锁保护；必须使用 `AttachThreadInput + Alt keybd_event + ShowWindow(SW_MAXIMIZE) + SwitchToThisWindow` 稳定置顶目标应用窗口（如 OneNote）。
2. **高分屏 DPR 物理/逻辑坐标系与行首边距 (MAX_START_DIST = 160px)**：
   - `user32.SetCursorPos` 使用显示器物理像素（如 2560x1600），而 Qt 接收并处理的事件坐标为逻辑像素（如 1280x800，DPR=2.0）；
   - 大括号手势判定中，行首词离大括号笔迹的起始水平距离在 2.5K 屏幕上可能达到 120px 逻辑像素（约 240 物理像素），`MAX_START_DIST` 必须设置为 160.0 逻辑像素，既能无缝包夹首行笔记，又可杜绝跨越 300px+ 间隙选中异侧独立栏目代码。
3. **PyInstaller 运行时 PYZ 归档与进程文件锁**：
   - PyInstaller `onedir` 模式下，纯 Python 模块打包于可执行文件内部的 `PYZ-00.pyz` 中；
   - 直接修改 `dist/_internal/desktop/` 下的 `.py` 文件不会覆盖已打包进 PYZ 的字节码，必须执行 `pyinstaller ScreenLex.spec --noconfirm` 重新生成二进制产物；打包前必须终止运行中的 `ScreenLex.exe`，严防 `numpy/_umath_linalg.pyd` 等 C 动态库被占用引发 `PermissionError: [WinError 5]`。

---

## 14. 🎓 学术语法抗噪音与 Polished-First 翻译管线规约

1. **离线拼写防御与草书笔迹先验映射 (Anti-Trout Defense)**：
   - 本地 `difflib.get_close_matches` 面对手写连笔（如 `treout`）时，因相似度计算易匹配出日常偏门词（如 `trout` 鳟鱼）；
   - 必须在离线 `difflib` 检索前部署 `HANDWRITING_OFFLINE_FIXES` 笔迹先验映射，并在候选结果处强制拦截 `trout` 与 `orb` 假阳性；离线状态下无论断网或超时，绝对禁止将学术连笔误纠为不相干日常词。
2. **算法复杂度符号严密隔离 ($O(1)$ 常数保全)**：
   - 在翻译预处理与标点规整中，严禁使用粗暴正则将数字 `1` 归并为 `N`；
   - 必须严格区分常数复杂度 $O(1)$ 与规模复杂度 $O(N)$，仅对括号畸变（如 `OCN)`）执行标准化，绝对杜绝产生“哈希查找时间为 O(N)”的教学事故。
3. **润色优先翻译架构 (Polished-First Pipeline)**：
   - 长句与段落翻译必须先经过 `check_grammar_and_spelling` 生成规范学术英语句子（`polished_sentence`），再送交翻译引擎，从源头彻底杜绝破碎 OCR 碎片导致的火星文机翻；
   - 划词取词时，例句翻译必须完整保留现场语境 `context_sentence`，严禁用局部短语的润色结果覆盖整句上下文。
4. **合法核心词汇保护与状语多句断句**：
   - 严禁对全局文本执行无条件 `bust -> but` 等合法词篡改，保护 `bust the myth`、`went bust` 等合法英文表达；对 `bust` 纠偏必须严格限制在连词转折结构（如后接 `the one` / `the below`）；
   - 状语断句修复必须使用 `(^|[.!?]\s+)` 正则捕获组，兼容多句文本内部的分句状语（如 `However.`）逗号规整。

---

## 15. 💾 生词本持久化数据与构建产物物理隔离规约

1. **绝对禁止数据库置于构建产物目录 (Anti-Dist-Storage Rule)**：
   - `PyInstaller` 重新构建时会自动执行 `Removing dir ...\dist\ScreenLex` 清空输出目录；
   - 严禁在 `frozen=True` 时将数据库路径设为 `os.path.join(os.path.dirname(sys.executable), "screenlex.db")`，否则重新打包会导致生词本数据被物理抹除；
   - 必须统一将持久化数据目录锁定为 Windows 标准应用持久化路径：`%APPDATA%\ScreenLex\screenlex.db`，确保源码调试态与打包发行态共用同一物理数据库，终身免疫重新打包误删风险。
2. **构建前用户资产不可逆保护门禁**：
   - 执行任何构建（`pyinstaller`）或清理命令前，必须保证用户数据处于构建目录外部；
   - 严禁任何工具链直接或间接清理、覆盖非指令显式指定的数据库资产。

---

## 16. 🏷️ 课件彩色横幅水平段自适应拆分与印刷体词汇免杀规约

1. **水平列间距自适应聚类 (MAX_COL_GAP = 80px)**：
   - 课件顶部或章节横幅（Banner）常带有深蓝色/纯色彩色分量，且左右两端可能分布校徽与页码装饰；
   - 在 `detect_handwriting_regions` 中，严禁盲目将同垂直高度的整行 `c_active[0]` 与 `c_active[-1]` 一锅端合并为 1000px 宽的整屏切片；
   - 必须基于 `col - seg_e <= 80` 进行水平段自适应切分，将两端装饰图形局限在独立的小切片中，中间正文标题 100% 留给 WinRT 高精度印刷体识别，从源头杜绝将正文标题误送入 RapidOCR 切碎。
2. **印刷体正规英文单词绝对保护 (Anti-Artifact Overwrite)**：
   - 在 `FreezeSelectOverlay._on_words_extracted` 中合并词块时，旧词若为合法英文字词（`alpha_chars >= 2 and ratio >= 0.75`），绝对禁止被 RapidOCR 提取的火星文碎片反向覆盖；
   - 只有当旧词本身为散碎字符/乱码时才允许被手写词替换，彻底保护深蓝横幅与各类特殊背景下的英文单词点击命中率。
3. **专业课核心标题 0ms 离线高频词库加速**：
   - 在 `OFFLINE_DEFS` 中内置 `data structure and algorithms` 等高频课程标题与专业术语；
   - 用户划选或点击专业课标题时，0 毫秒本地极速直出权威释义，彻底消灭网络翻译骨架屏等待感。

---

## 17. 🔠 词形智能还原、离线词库绝对优先与骨架屏彻底消融规约

1. **所有格与屈折形态还原 (Lemmatization First)**：
   - 用户点击带所有格（如 `Pascal's`）或复数/过去式时，严禁直接拿未还原的原型提交在线词典或展示山寨假音标（如 `/Pascal's/`）；
   - 必须优先剥离 `'s` / `'` 并尝试词形还原检索基词（`Pascal`），提取真实权威音标与释义，0ms 瞬间上屏。
2. **离线权威词库先行于拼写纠错**：
   - 严禁在检索 `OFFLINE_DEFS` 之前盲目调用 `check_spelling`；
   - 必须先查原词及基词是否在离线库，杜绝将合法的数学/算法词汇（如 `factorial`）反向误纠偏为 `factor`。
3. **彻底清退 Google 翻译与海外网络阻塞**：
   - 严禁在翻译管道中引入 `translate.googleapis.com` 等国内无法直连的海外接口，杜绝 1.8s 强制超时挂起；
   - 必须统一采用国内 100% 极速直连双轨（有道 AI + Suggest 备用轨），响应时间控制在 150ms 内。
4. **骨架屏消融与优雅静默降级**：
   - 查词卡片初始严禁向用户展示生硬的“正在获取权威精准释义...”和“正在同步现场语境译文...”；
   - 释义优先以离线基词或原词瞬间展示，例句译文未就绪前保持隐藏，异步返回后平滑淡入，彻底消除转圈卡顿感。

---

## 18. 🔄 单例卡片生命周期重置与异步网络增强防死锁规约

1. **单例卡片 `_is_closed` 唤醒强制复位铁律**：
   - 词典卡片在应用全局作为单例对象复用（`self.dict_card`）；
   - 在 `closeEvent` 中会设置 `self._is_closed = True`；
   - 在 `show_word` 每次被调用时，**必须在最顶层第一行强制执行 `self._is_closed = False`**；
   - 严禁遗漏该重置，否则用户关闭过一次卡片后，后续所有需要异步网络增强（短语/长句翻译、非内置单词释义）的异步回调都会因 `_is_closed == True` 被静默物理丢弃，导致界面永久死锁在“正在翻译整句...”或“正在查询释义...”骨架屏。
2. **多世代隔离与双重防脏写门禁**：
   - 异步回调必须同时校验 `not getattr(self, "_is_closed", False)` 以及 `current_gen == self._current_gen_id`；
   - 确保快速连续切换划选时，只有最新一次选词结果才能刷屏，彻底杜绝历史旧会话串词与死锁。

---

## 19. 🌐 国内高可用翻译引擎与系统代理旁路穿透规约

1. **微软 Edge 官方翻译引擎主轨（免 Key / 0 墙 / 150ms 极速响应）**：
   - 翻译长句与段落首选微软 Edge 官方直连接口（`https://edge.microsoft.com/translate/translatetext?from=&to=zh-Hans&isEnterpriseClient=false`）；
   - 该接口依托国内 CDN 直连，无需任何密钥，学术长句中英互译自然流畅。
2. **强制代理旁路穿透（Anti-Proxy Hang / ProxyHandler({})）**：
   - 针对国内翻译与字典接口，必须使用 `urllib.request.build_opener(urllib.request.ProxyHandler({}))` 强制旁路本地系统代理；
   - 坚决杜绝 Windows 系统代理残留（如 Clash 监听的 `127.0.0.1:7897` 挂起或节点失效）导致 HTTPS CONNECT 握手超时死锁；无论用户“开梯子”或“关梯子”，均能 100% 毫秒级极速直出译文。

---

## 20. 🎨 Qt 透明窗口父子穿透、音标容器解耦与内容感知自适应规约

1. **Parent-Aliasing 顶层透明窗双重重影陷阱 (Anti-Ghosting Rule)**：
   - 在 Qt `WA_TranslucentBackground` 无边框浮动窗口中，所有深层子控件（如 `def_eyebrow`, `sentence_text` 等）在初始化时严禁传入 `parent=self`（顶层卡片窗口），必须仅交由其所在的布局（Layout）或直接子容器托管；
   - 顶层窗口透明背景不会主动重绘擦除历史像素，若深层子控件同时被顶层父级引用，会导致绝对坐标与相对坐标各绘制一遍，并在内容推移（如音标展示）时在屏幕上烙下两套错位重叠的双重文字残影。
2. **音标药丸独立容器与左侧自适应对齐**：
   - 音标组件（UK/US）必须统一封装于独立的水平容器 `phonetic_container` 中，居左紧凑排布；
   - 允许单音标（仅 US 或仅 UK）或双音标独立自适应呈现，严禁未封装容器直接平铺导致单音标孤立悬空或压迫下方释义。
3. **内容感知动态高度 (Content-Aware Dynamic Resizing) 与工作区防侵入**：
   - 词典卡片高度必须随标题长度、音标、释义行数、拼写建议、语法润色卡片及例句译文物理动态计算（240px ~ 520px），严禁死锁在初始 285px；
   - 窗口下边界必须严格受 `screen.availableGeometry().bottom() - 15` 约束，彻底避开 Windows 底部任务栏遮挡与裁切。

---

## 21. 🎬 实时双语字幕 DWM 透视遮盖与滚动碎词缝合规约

1. **DWM 透视原位遮盖铁律 (`WDA_EXCLUDEFROMCAPTURE`)**：
   - 在原位字幕舱模式 (`MODE_INPLACE_HUD`) 下，必须为顶层窗口调用显式声明 `wintypes.HWND` 签名的 `SetWindowDisplayAffinity(hwnd, 0x00000011)`；这样双语字幕舱可直接盖在视频原生单语字幕上方实现零重叠，而底层截屏 API 自动透视该窗捕获原生字幕。
   - 若运行系统不支持该特性（返回 `False`），必须自动降级切换至独立字幕台 (`MODE_CONSOLE_DRAWER`)，严禁原位遮盖触发 OCR 自我循环；而在 `MODE_CONSOLE_DRAWER` 下应解除排除标志，允许定格截屏取词。
2. **Qt 布局与纯测量规约（防隐藏页撑高与高度/宽度撕裂）**：
   - 严禁使用 `QStackedWidget` 混装紧凑原位字幕舱与多行历史字幕台，否则 `QStackedLayout` 会取所有子页最小高度最大值把紧凑字幕舱撑高至 258px+ 遮挡视频；必须使用同级容器互斥 `hide()/show()` 切换。
   - 自定义折行文字流控件的 `heightForWidth(w)` 必须保持只读无副作用（严禁修改布局缓存或调用 `setFixedHeight`），防抖跳过位移时按当前实际宽度测算高度。
3. **滚动碎词拼接与字幕区域评分清洗**：
   - YouTube 字幕 OCR 必须绕过手写纠错替换表（使用 `win_ocr._recognize_winrt_async(..., raw_only=True)`），防止 `"cone"` 被改写为 `"some"` 或连字符断裂。
   - 扫描区词块聚类必须对垂直文本簇按有效英文字母密度与水平居中度打分，同时过滤底部播放控制栏时间字样（如 `12:34 / oo`），严禁盲信最底行把进度条时间戳当成字幕。
   - `LiveSubtitleEngine` 所有跨线程状态读写与快照派发必须经 `threading.RLock()` 保护并传输元素级浅拷贝，退出时调用 `shutdown()` 释放 `ThreadPoolExecutor`，杜绝跨线程状态撕裂与退出误触恢复视频按键。

---

## 22. 📌 Windows 11 开机自启 `WS_EX_TOPMOST` Z-Order 守卫与 64 位句柄铁律

1. **开机竞争与 `WS_EX_TOPMOST` 主动置顶铁律**：
   - `HKCU\...\Run` 在 `explorer.exe` 与桌面 Shell 尚未完全就绪时即拉起应用；仅靠 `Qt.WindowType.WindowStaysOnTopHint` + 构造期单次 `show()` 在开机竞争、`TaskbarCreated`、`Win+D` 或全屏窗口切换后极易被 DWM 剥离 `WS_EX_TOPMOST` 而沉底隐藏。
   - 边缘微柄及置顶浮窗在 `showEvent` 与开机稳定期（`1.5s / 4.5s / 10s`）必须显式调用 `enforce_window_topmost(..., activate=False)`，并接入轻量 Z-order 守卫定时器（仅在发生 `WS_EX_TOPMOST` 丢失或被外部有面积非隐藏/非穿透窗口压盖时才无感调用 `SetWindowPos(HWND_TOPMOST, ..., SWP_NOACTIVATE)`）及 `TaskbarCreated` / `WM_DISPLAYCHANGE` / `SPI_SETWORKAREA` 重贴边监听。
2. **禁止 `WindowDoesNotAcceptFocus` 误伤右键菜单**：
   - 为防止开机抢占焦点导致被 Foreground Lock 压底，浮窗应使用 `Qt.WidgetAttribute.WA_ShowWithoutActivating` 配合 `SWP_NOACTIVATE`，严禁设置 `WindowDoesNotAcceptFocus`（否则右键 `EditorialPopupMenu` 外部点击收起异常）；且右键菜单/样式面板打开期间与隐藏/拖拽期必须暂停 Z-order 守卫。
3. **过滤 `DWMWA_CLOAKED`、零面积代理窗与抗 UIPI 自旋**：
   - `GW_HWNDPREV` 扫描外部遮挡窗口时必须过滤 `DWMWA_CLOAKED` 隐形 UWP/系统窗、零面积（`<= 0`）占位窗及 `WS_EX_LAYERED | WS_EX_TRANSPARENT` 穿透叠层，并在遇到高权限 `WS_EX_TOPMOST` 阻挡者且单次置顶无效时缓存跳过，严禁无休止自旋。
4. **全链路 64 位隔离 `WinDLL` 声明**：
   - 所有跨 Win32 API（`SetWindowPos`、`SetPropW`、`DwmSetWindowAttribute`、`DwmExtendFrameIntoClientArea` 等）必须使用模块级独立 `ctypes.WinDLL(..., use_last_error=True)` 并显式声明 `argtypes`/`restype`（含 `wintypes.HWND`/`wintypes.HANDLE`），严禁裸用 `ctypes.windll.*` 传入 raw `int` 触发 64 位 `OverflowError`。

---

## 23. 🌐 翻译直连/活代理并行竞速、Keep-Alive `select` 探活与离线状态条解耦规约

1. **死代理防挂起 + 活代理并行竞速**：
   - `dictionary_service.py` 必须通过 `_get_live_system_proxy()` 以 80ms TCP 连接探测系统代理（10s 缓存）：未运行/死代理 `< 5ms` 跳过，绝不挂起；若系统代理存活，在 `translate_text()` / `translate_batch()` 中同时并发竞速直连轨（`_fetch_edge`, `_fetch_youdao_http`, `_fetch_youdao_https`）与活代理轨（`_fetch_edge_proxy`），并在 `_USER_HTTP_EXECUTOR` 与 `_HTTP_RACE_EXECUTOR` 间隔离前后台线程池，避免批量字幕翻译阻塞前台查词。
   - 严禁重新引入 `translate.googleapis.com`（遵守 §17.3）。
2. **Keep-Alive 复用前 `select` 死链清退**：
   - `_http_post_keepalive` 池内连接空闲 TTL 不得超过 15s；复用前必须用 `select.select([sock], [], [sock], 0)` 检验对端是否已断开（FIN/RST/异常），若死则立刻丢弃并重连，首轮复用超时收紧为 `min(timeout, 1.4)`。
3. **离线状态条与译文/释义区物理隔离 (`translation_status`)**：
   - 翻译超时或离线时，绝对禁止将 `"网络超时，已切换至离线原文"` 等报错占位符写入 `definitions[0]["cn"]`。
   - 长句/未收录词无网时置 `definitions = []`、`translation_status = "offline"`，UI 隐藏 `defs_container` 并在独立的 `self.offline_banner` 展示轻量提示与“重试”按钮；已命中 52k `offline_dict.db` / `OFFLINE_DEFS` 的词汇绝不因例句或在线音标补充失败而弹离线提示；2–3 词短语无整句译文时通过 `offline_partial` 提供逐词离线参考释义。

---

## 24. 🎨 透明字幕 RGB 感知对比度与自适应可读性描边规约

1. **文字实际覆盖区精准 RGB 采样**：
   - `BilingualSubtitleWindow._sample_underlying_bg_rgb` 必须通过 `_compute_text_sample_local_rect` 紧贴当前可见英文字幕词块与中文字幕行共同覆盖区域进行采样，严禁在宽幅预设下采入左右空白翼区或漏采下方中文行；同步保留 `sample_screen_region_luma` 兼容接口。
2. **Rec.709 + WCAG 相对亮度 + 色度联合判别与纯黑白零描边保护**：
   - `analyze_backdrop_contrast` 必须结合有效背景 RGB 的 Rec.709 亮度分位数（`p20` / `p50`）、暗区占比（`L < 100`）、色度 `C` 及滞回阈值，避免“亮墙+深蓝衣物”混合场景或高饱和彩底误切深墨色字体。
   - 纯黑/深底及纯白/浅灰高对比背景（`cr_worst >= 5.5`）必须保持 `halo_strength = 0.0`（零描边零重影）；仅在最劣局部对比度不足时通过 `_draw_halo_text` 按 `halo_strength` 注入锐利自适应描边。
3. **`_ShadowLabel` 像素字号与色值一致性铁律**：
   - 启用自定义 `paintEvent` 绘制描边时，`_sync_hud_text_palette` 必须同步为 `hud_cn_label` 设置 `setPixelSize(cn_px)` 与 `set_text_color(cn_col)`，并保留适当 `contentsMargins` 防止阴影边缘裁切。

---

## 25. 🚀 版本更新、GitHub 同步与开源零隐私泄露铁律

1. **版本更新与文档同步规范**：
   - 每次功能迭代或问题修复完成并通过验证后，须按语义化版本在 `CHANGELOG.md` 顶部倒序追加更新日志（写明版本号、日期、影响模块与核心改动），并将新增的架构边界或踩坑经验同步沉淀至 `AGENTS.md`。
   - 同步维护 `README.md` 中的功能说明与项目结构索引；**仅在完成正式构建并上传新版本 GitHub Release 压缩包后**才更新 `README.md` 的 Release 徽章与压缩包下载文件名，严禁指向未发布的下载链接。
2. **Git 提交身份强制锁定门禁**：
   - 每次执行 `git commit`、`git tag` 或 `git push` 前，必须校验当前仓库局部配置 `git config --local user.name` 为 `Gek1t`、`user.email` 为 `231847204+Gek1t@users.noreply.github.com`。
   - 严禁沿用全局真实姓名或个人/机构邮箱提交；一旦发现身份偏差，必须立即中止并修正局部 `git config`。
3. **开源零隐私泄露与敏感资产前置审计铁律**：
   - **严禁个人与环境信息入库**：所有 Git 追踪文件（源码、注释、默认标签、`LICENSE`、`README.md`、`CHANGELOG.md`、`AGENTS.md` 及 Commit Message）中**绝对禁止**包含任何真实姓名、个人或学校/公司邮箱、院校或单位名称、所在城市、本机操作系统用户名或用户绝对路径、具体个人电脑设备型号，以及任何 API Key / Token / Cookie / `SESSDATA`。
   - **严禁个人数据与运行产物入库**：依托 `.gitignore` 严格隔离个人生词库与配置（`*.db`，仅白名单放行只读词库 `desktop/offline_dict.db`）、运行日志（`*.log`、`logs/`）、临时测试/草稿目录（`scratch/`、`.gemini/`）、本地备份（`backup_v*/`）及打包产物（`dist/`、`build/`、`ScreenLex_v*.zip`）；提交前必须确认 `git status --short` 仅含预期开源文件。
   - **截图与 Release 附件脱敏**：所有纳入 `docs/screenshots/` 的截图必须在隔离临时库/无隐私样本下渲染并检查 PNG 元数据不含本机路径或账号信息；发布新 Release 压缩包前须终止旧进程、以 `pyinstaller ScreenLex.spec --noconfirm` 重新打包并对包内文件执行字节级隐私扫描，确认零残留后再打注释标签并发布。
   - **推送前强制扫描闭环**：每次 `git push` 前必须对暂存区与全仓库追踪文件执行敏感信息（个人标识、非 noreply 邮箱格式、本机绝对路径、凭据关键字）正则审计，确认 **0 泄露命中** 且提交作者为匿名身份后方可推送至 `origin/main`。





