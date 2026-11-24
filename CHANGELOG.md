# ScreenLex 更新日志 (Changelog)

## v2.4.1 (2026-11-24)

> **网络代理竞速、独立离线状态条与开机置顶稳定性修复**：彻底解决有网代理环境下误报离线、离线提示侵占译文区以及开机自启浮窗易被压底的问题，确保真·离线与联网查词体验干净稳定。

- **翻译直连/活代理并行竞速与独立离线状态条（[`desktop/dictionary_service.py`](desktop/dictionary_service.py)、[`desktop/dictionary_window.py`](desktop/dictionary_window.py)）**：
  - 修复仅直连导致有网代理环境下被误判超时的缺陷：新增 80ms TCP 系统代理探活（`<5ms` 跳过死代理/畸形/SOCKS 代理，存活时与直连轨道并行竞速）及 `_http_post_keepalive` 的 `select` + `pending()` 死链清退；物理隔离编排/短语/前台查词/后台字幕四层线程池，并统一 `5.2s` 增强截止预算（严防 UI `6.8s` 定时器抢跑误报）；
  - 彻底将离线提示与译文区物理隔离：不再把「网络超时，已切换至离线原文」写入释义列表，改为独立 Editorial `offline_banner` 提示条与一键防连点「重试」按钮，无译文时自动隐藏 `defs_container` 与多余分隔线；52k 离线词库已命中词汇与 2–3 词短语逐词离线参考（`offline_partial`）均获干净展示并在返回导航后完整保留状态。
- **开机自启窗口置顶与 Z-Order 掉层自愈加固（[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`desktop/floating_dock.py`](desktop/floating_dock.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/app.py`](desktop/app.py)）**：
  - 针对 Windows 开机自启时 `HKCU\...\Run` 早于 Explorer/任务栏初始化导致浮窗被压底或剥离 `WS_EX_TOPMOST` 的问题，新增 64 位安全 `enforce_window_topmost` / `restore_topmost_if_needed`、`FloatingZenPill` 的 `WA_ShowWithoutActivating` 与开机稳定期（`1.5s / 4.5s / 10s`）置顶补强；
  - 监听 `TaskbarCreated`、`WM_DISPLAYCHANGE`、`SPI_SETWORKAREA` 与 `availableGeometryChanged` 自动重贴边，并在日常运行中通过轻量 Z-order 守卫（自动过滤 `DWMWA_CLOAKED` 隐形系统窗、零面积代理窗、穿透叠层及高权限 UIPI 窗）在被覆盖或 `Win+D` 降层后无感恢复置顶；同步为字幕舱、词典卡、定格遮罩与设置窗补全 64 位 `HWND_TOPMOST` 与 `dwmapi`/`SetPropW` 句柄安全声明。

## v2.4 (2026-11-19)

> **全维度体验、性能与工程升级**：补全可配置 4 组全局快捷键与独立 Editorial 设置窗、微柄左右吸附记忆、生词本快速筛选与 4 秒误删撤销、整节双语字幕 Markdown/TXT 一键导出、52,513 词真·离线全词库兜底、长视频/大词本分批零卡顿挂载、常驻 OCR 线程与切源轻提示，并完成工程清理与日志/打包体系加固。

- **全局快捷键与统一设置窗口（[`desktop/input_hook.py`](desktop/input_hook.py)、[`desktop/settings_window.py`](desktop/settings_window.py)、[`desktop/floating_dock.py`](desktop/floating_dock.py)、[`desktop/app.py`](desktop/app.py)）**：
  - 支持 4 组可配置全局热键（默认 `F8` 原位取词、`Alt+Q` 定格取词、`Alt+B` 开关实时双语字幕、`Alt+R` 框选视频字幕区），支持总开关、冲突校验、SQLite 持久化与线程内零重启热重载；
  - 新增 Editorial 风格独立设置窗口，入口同步接入边缘停靠微柄右键菜单（位于前 3 项下方）及系统托盘右键菜单，联动开机自启、停靠边选择、离线词库状态与日志目录直达。
- **边缘停靠微柄左右贴边与位置记忆（[`desktop/floating_dock.py`](desktop/floating_dock.py)）**：
  - 支持鼠标/触屏跨屏自由拖拽并自动吸附贴靠屏幕左/右侧边缘，持久化停靠侧与纵向比例至 `app_settings["dock_position"]`，微抖动点击防误移，重启自动还原；左侧停靠时智能向右弹出菜单。
- **生词本筛选、4 秒误删撤销与分批挂载（[`desktop/vault_window.py`](desktop/vault_window.py)、[`desktop/database.py`](desktop/database.py)）**：
  - 顶部新增 `全部 / 单词 / 长句` 快速筛选栏（实时计数并与防抖搜索联动）；
  - 移出生词时底部弹出 4 秒自动消隐「已移出 · 撤销」条，采用幂等 `set_word_collected` 恢复收藏并实时同步词典卡片书签状态；
  - 引入每帧 40 张卡片的异步分批挂载机制，300+ 生词秒开无卡顿。
- **双语字幕笔记一键导出、长视频分批挂载与切源轻提示（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)）**：
  - 独立字幕台新增「导出笔记」按钮，一键生成带时间轴的 Markdown（`.md`）或纯文本（`.txt`）双语讲稿；
  - 超过 60 句的长视频字幕台采用首批 40 行 + 每帧分批 40 行异步挂载，隐藏/恢复自动续挂不截断；
  - 重构 `LiveSubtitleEngine` 为单常驻 OCR 工作线程复用持久化 `asyncio` 事件循环，消除每 110ms 频繁创建销毁线程与事件循环开销；切源时（YouTube / B站外挂字幕 / 画面 OCR / 框选区）在 HUD 呈现 2 秒自动收隐轻量状态提示。
- **52,513 词真·离线全词库兜底（[`desktop/offline_dict.py`](desktop/offline_dict.py)、[`desktop/offline_dict.db`](desktop/offline_dict.db)、[`desktop/dictionary_service.py`](desktop/dictionary_service.py)）**：
  - 融合 MIT 协议 ECDICT 核心词表与内置 IELTS 学术语料编译为 3.66 MB 只读 `WITHOUT ROWID` SQLite 词库，无网/弱网环境 0ms 返回真实中文释义与标准 Unicode IPA 音标（含屈折/派生音标离线推演），联网时仍可平滑升级真实英/美双轨音标与语境译文。
- **工程健壮性与打包整理（[`desktop/logger.py`](desktop/logger.py)、[`ScreenLex.spec`](ScreenLex.spec)）**：
  - 归档 10 个零引用历史脚本至 `backup_v2.3_legacy_scripts/`；新增 UTF-8 轮转日志（`%APPDATA%\ScreenLex\screenlex.log`，`1MB × 3`）；基于独立隔离 `.build_venv`（`opencv-python-headless`）构建发行包。

## v2.0 (2026-11-18)

> **大版本里程碑（对比 v1.5 正式发行版）**：ScreenLex 从“定格课件取词词典”正式进化为 **「定格课件取词 + 全场景实时双语视频字幕」双引擎桌面学习平台**，全面重塑 Editorial 杂志排版视觉、原位透视字幕体验与 Windows 11 / 触控平板底层稳定性。

### 一、重磅新功能：全新实时双语视频字幕识别与外挂同步系统（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/win_ocr.py`](desktop/win_ocr.py)）
- **零取景框屏幕字幕智能捕获、手动 OCR 框选模式与滚动碎词缝合**：
  - 新增 `LiveSubtitleEngine` 与 `RollingCaptionStitcher`，无需实体取景框即可自动追踪前台视频、PowerPoint / WPS 幻灯片内嵌视频、Windows 媒体播放器及浏览器窗口（支持多显示器与异 DPI 自适应），结合 `6×8` 局部网格帧差门控、描边亮字幕判别与窄带 ROI 追踪，高效识别画面字幕并把逐词滚动碎片自动拼接为完整双语长句；
  - **独立 OCR 字幕框选锁定（`_SubtitleRegionSelectorOverlay`）**：在 HUD 顶栏（靶心按钮，右键清除）、双语字幕台（`框选字幕`，去除冗余 `OCR` 字样）与系统托盘菜单（`框选视频字幕区 (OCR)`）提供一键切换入口；拖拽划定任意视频字幕区后自动锁定为强制 OCR 模式（`_ocr_forced`），屏蔽 YouTube / Bilibili 自动外挂抢占，完美兼容无 CC 外挂字幕的 PPT 嵌视频与本地课程录像；
  - **复杂花底/纹理视频字幕双引擎召回与粘连切词**：新增纯 NumPy `detect_outlined_subtitle_regions` 与 `recognize_outlined_subtitle_words`（纯 NumPy 滑窗极小暗环隔离 + WinRT 与单帧 1×/1.5× RapidOCR 融合，限定 ONNXRuntime 线程防笔记本电池自旋卡顿并提速 20× 以上），结合 [`desktop/ocr_helpers.py`](desktop/ocr_helpers.py) 中 `_find_exact_dict_split` 与逗号/连字符后缀拆词 `split_glued_ocr_token`、常见 `ln → In` 上下文纠偏及 WinRT 句中大跨度漏词缺口探测，全面提升视频字幕识别准确度与响应速度；
  - 内置 `Keep-Alive` 翻译连接池与活跃/历史双线程池并发预译，单句实时上屏低延迟对照。
- **YouTube / Bilibili 免开原生字幕自动识别与全片预译同步**：
  - 支持 YouTube（InnerTube 接口 + 标题自动解析）与 Bilibili（WBI 签名、分P `?p=N` 及官方应用内防截屏二维码扫码登录 `_BilibiliLoginDialog`），无需打开网站 CC 或手动粘贴链接即可后台抓取完整英文字幕轨并全片分批预译；
  - 引入常驻无窗 Windows UIA 浏览器探针 `_BrowserUIAProbe` + 亚秒级锁相校准（`_sync_clock_from_uia`）：自动读取当前播放地址、播放器位置与实时进度，支持整数秒中点补偿与 `0.5x ~ 2.0x` 倍速自适应同步、拖拽快进快退瞬时对齐及停滞自动锁停，并支持 `[-2s]` / `[+2s]` 手动微调；
  - **英文智能语义断句引擎**：为外挂时间轴（`YouTubeTranscriptLoader.merge_raw_segments`）与 OCR 滚动缝合（`RollingCaptionStitcher`）新增多句拆解（`_split_item_by_sentences`）与子句边界优选（`_find_natural_clause_split`），结合停顿切分、缩写/小数保护、`even though / so that` 复合连词保全及非完结词（`_NON_TERMINAL_TAIL_WORDS`：避免在 `a / the / to / of / and` 处腰斩），同步优化 HUD 紧凑截断子句使双语断句自然易读。
- **双模字幕呈现、0ms 点词即查与播放联动**：
  - **原位透视悬浮条（`MODE_INPLACE_HUD`）**：基于 Windows `WDA_EXCLUDEFROMCAPTURE` 对截屏工具隐形，直接原位覆盖原视频字幕不发生自我 OCR 死循环（不支持系统自动降级为独立台）；
  - **独立双语字幕台（`MODE_CONSOLE_DRAWER`）**：可一键展开带实时焦点引文、点击时间轴跳转与完整历史流回看的独立窗口；
  - **点词即查联动**：字幕内任意英文单词点击/触按 `0ms` 弹出词典卡片并自动携带上下句语境，`VideoPlaybackController` 支持查词自动暂停、关卡自动恢复播放。

### 二、Editorial 2.0 视觉重塑与通透沉浸定制（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`desktop/dictionary_window.py`](desktop/dictionary_window.py)、[`desktop/vault_window.py`](desktop/vault_window.py)）
- **编辑杂志风全域统一**：统一四色纸墨美学（暖米纸 `#F9F8F6`、墨黑 `#1C1C1C`、石墨灰 `#6B7280`、陈玫 `#816D70`），全端窗口与菜单（`LexiconCardFrame`、`EditorialPopupMenu` 等）统一 `1.5px #1C1C1C` 锐利直角外框、零圆角与衬线/无衬线混排。
- **日夜双模、透视反色与全套外观调节（`_HudStylePopover`）**：
  - 支持 HUD 太阳/月亮 `240ms` 平滑日夜切换（`墨黑 / 暖米纸`）、字号（`10pt~22pt`）、**字重三档调节（`细 / 标准 / 粗`，`Segoe UI Light` / `Microsoft YaHei UI Light` / `Georgia` 原生字重联动）**、双语/单语切换、宽窄预设与 `0%~100%` 单调透明度调节；
  - 彻底移除字幕投影保持纯字干净字迹：透明度 `< 60%` 时利用 `WDA_EXCLUDEFROMCAPTURE` + `GDI32 BitBlt` 透视采样字幕背后真实画面亮度，带 `[132, 152]` 滞回防闪烁自动黑白反相字色（白/亮底切墨黑 `#111111`，暗底切象牙白 `#FAF9F6`）；
  - 极低透明度（`< 28%`）下鼠标靠近字幕 `28px` 内自动渐显紧凑磨砂深灰按键条（`_HudTopBar`），离开 `36px` 保护区后平滑渐隐收起。

### 三、Win11 合成渲染、花底定格选词与稳定性精修（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/freeze_overlay.py`](desktop/freeze_overlay.py)、[`desktop/floating_dock.py`](desktop/floating_dock.py)、[`desktop/ui_icons.py`](desktop/ui_icons.py)）
- **花底/强干扰视频字幕定格选词与白屏无响应根治（[`desktop/freeze_overlay.py`](desktop/freeze_overlay.py)、[`desktop/win_ocr.py`](desktop/win_ocr.py)、[`desktop/app.py`](desktop/app.py)）**：
  - 消除花哨视频背景（如高频斑点/蓝底走线上的白色黑边字幕）中 WinRT 仅检出首尾边缘词就提前短路跳过的问题：在后台全屏预提取、点词按需识别与抬手兜底中联动 `detect_outlined_subtitle_regions` 与 `recognize_outlined_subtitle_words` 完整找回被漏掉的中间词汇；
  - 统一拖选几何管线 `_apply_stroke_selection_geometry`（从单词起笔斜向跨行拖选优先按连续阅读序连片选词，不再被 `sh >= 20` 的矩形框规则误劫持），并在异步词库补全合并后按 `phys_start` 重锚定 `initial_idx` 防止索引偏移；
  - **GUI 主线程零阻塞与跨线程内存安全**：将定格超时按需识别（`_resolve_pending_tap_timeout`）及全局点词/F8 热键 OCR 全部移出主线程，定格图像转成不可变字节快照后传递后台线程；预提取优先以 WinRT 秒出全屏词汇并跳过已有完整印刷词的 PPT 横幅/缩略图区，`recognize_image` 仅截取命中的局部手写区运行 RapidOCR，从根源消灭整屏 PPT 定格白屏未响应与间歇卡顿。
- **Win11 分层透明即时刷新、多行零重叠与鼠标停驻续播根治**：恢复 `BilingualSubtitleWindow` 的 `WA_TranslucentBackground` 透明合成并修复 `_dwm_commit_layered_surface`（先布局失效重算后 `resize` 并重申 `WDA_EXCLUDEFROMCAPTURE`），彻底根治固定位置（`auto_follow=False`）下 `update_subtitle_data` 未随多行字幕自适应增高以及 `_apply_smooth_step` 动画中间态压缩导致中英文字幕上下重叠的 Bug；同步修复 `_BrowserUIAProbe` 通过真实“Play/播放”按钮识别暂停态（`uia_paused`），移除 `_on_clock_tick` 对冻结进度秒数的 `1.02s` 硬上限钳制，防止鼠标停在字幕区使视频控制栏隐藏后字幕卡死不走字或回弹，并引入 `_BLOCKED_PROCESS_EXES` 屏蔽 IDE/OneNote 等非视频进程窗口误抢占；向量化屏幕背景亮度采样并降低后台轮询开销；
- **零漂移收放定位**：精确对齐 `_measure_hud_height_for_width` 边距并用底边锚定收放，彻底解决反复移入移出鼠标导致字幕持续向下漂移的问题；
- **字幕时序防回弹与 ASR 缩略改写加固**：完善 `_sync_clock_from_uia`（含首帧/前向跳转整数秒边界保护、`%` 进度条百分比文本误判过滤）、`0.5x~2.0x` 倍速与停滞锁相、OCR 进度条整数秒区间兜底校准（`_sync_clock_from_ocr_timestamp` + `_min_keep_sec_for_cur_idx` 跨句边界保护）、全范围 `still_shows_cur` 与 6 帧 OCR 回跳确认及 `BilingualSubtitleWindow` `snapshot_seq` 单调防乱序机制；扩展 `RollingCaptionStitcher` 基于 `_asr_match_advance` 支持句首、句尾及多处连续 ASR 缩略/拼写改写原地修订（防止末尾缩略误粘回旧词）与双行滞留过滤，全面消除切句回弹；
- **触控去重与悬浮球长按修复**：为 `VectorIconButton`、`ZenCollectButton` 与 `PhoneticButton` 加入触控+Windows 合成鼠标事件去重防抖（`0.45s` 时间窗），修复触屏点击收藏与发音异常及 `FloatingZenPill` 长按 `480ms` 二级菜单弹出；
- **屈折词形与派生词音标自动补全（[`desktop/dictionary_service.py`](desktop/dictionary_service.py)、[`desktop/grammar_checker.py`](desktop/grammar_checker.py)）**：扩展有道音标接口同时解析 `ec` + `simple` 词典及 `phone` 字段，新增通用递归词干回退与音韵拼接管线（`_resolve_word_phonetics`、`_inflect_ipa_s`/`_ed`/`_ing`/`_agent` 等），为缺乏独立词典音标的屈折词（`orchestrates`/`coexists`/`updates`/`merges`/`orchestrated`/`orchestrating`）与 `-er`/`-or` 派生名词及其复数（`orchestrator` `/ˈɔːkɪstreɪtə(r)/`、`orchestrators` `/ˈɔːkɪstreɪtəz/`、`serializer`、`serializers` 等）自动由原形精准推演英/美双轨音标；同步扩展 `grammar_checker.py`（`is_derivation_relation`、`is_valid_word`）识别 `-er`/`-or`/`-ers`/`-ors` 构词形态，消除 `tokenizer`/`serializer` 等派生词的误纠偏劫持与漏 Match 问题；
- **全栈审计稳定性、性能与打包精修（16 模块联合加固）**：
  - **字幕与网络加固**：补全 `subtitle_window.py` 的 `draw_hud_bar` 导入修复自定义 OCR 选区绘制崩溃；重构 `_BrowserUIAProbe`（1.5s 超时队列读取 + 独立短锁防主线程关闭死锁 + 防孤儿 PowerShell 复活）；将 B 站 7 处接口统一切至直连 `_open_url_direct` 并阻止 `_urlopen_prefer_system` 对 `HTTPError` 盲目重试；修复 `_BilibiliLoginDialog._polling_busy` 刷新死锁、`_TranscriptRowWidget` 时间戳/索引不更新、`_apply_smooth_step` 60Hz 动画自旋、自定义区域空白帧 RapidOCR 尖峰、HUD 模式隐藏态延迟历史构建与批量翻译重复句漏回填；
  - **词典、语法与数据库加固**：消除 `_HTTP_RACE_EXECUTOR` 同池嵌套自死锁；禁止有道 `suggest` 盲目覆盖 `word` 污染生词本并新增释义/音标 LRU 缓存；重构 `speak_word` 为单线程亲和 MCI 非阻塞轮询切音 + 原子 MP3 校验与 SAPI 降级；修复 `OFFLINE_DEFS` 覆盖真实屏幕语境译文与 `preprocess_for_translation` Big-O 正则破坏 `O(log n)`/`OCR`/`OCaml`；精修 `-ed`/`-ing`/`-ves`/`-er` 连读音标与 `CORE_VOCAB` 预过滤预算节省；修复 `grammar_checker.py` `COMMON_TYPOS` 前置、漏尾 `e` 识别、多项高误报语法正则及零长插入重叠撕裂；加固 `database.py` 主线程持久连接 + `BEGIN IMMEDIATE` 原子写入 + `NULL`/脏 JSON/旧库列迁移防护 + `DictionaryCard` 复用单 `QTimer` 与空释义防崩；
  - **OCR、定格层、钩子与打包瘦身**：修复 `win_ocr.py` 描边字幕 `origin_x/y` 偏移、多行 `initial_index`、WinRT 缓冲显式释放及纯整数墨迹扫描；分离印刷体免受手写纠偏污染；加固 `freeze_overlay.py` 退出自增 `_session_id`/锁保护/Win11 手写笔兼容；补齐 `input_hook.py` 64 位 `ctypes` 签名与跨线程热键释放；修复 `run_desktop.py` `freeze_support()` 启动顺序与互斥单实例；优化 `ScreenLex.spec`（纳入 `cv2` 恢复打包版 RapidOCR 可用、仅打包 `common_words.txt` 杜绝测试库泄漏，并剔除 `llvmlite`/`numba`/`cryptography`/`Pythonwin`/`onnx` 与未用 Qt 插件大幅瘦身）；
- **工程发布**：清理工作区冗余测试脚本，打包全新 `ScreenLex_v2.0_Windows_x64.zip`。

---

## v1.7.2 (2026-11-17)

### 编辑杂志风 (StyleKit Editorial) 全端视觉重构与 HUD 日夜双模切换
- **Editorial 设计语言全局落地（[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/dictionary_window.py`](desktop/dictionary_window.py)、[`desktop/vault_window.py`](desktop/vault_window.py)、[`desktop/app.py`](desktop/app.py)、[`desktop/floating_dock.py`](desktop/floating_dock.py)）**：
  - 统一精简至四色设计系统：暖米纸画布 `#F9F8F6`、柔和墨黑 `#1C1C1C`、石墨灰 `#6B7280` 与克制暖点缀陈玫色 `#816D70`（严格限制在 `< 2%` 画面占比的引文竖脊、引号、点词底线、滑块游标与激活收藏状态）；
  - 全域采用零圆角（`border-radius: 0`、Win11 `DWMWCP_DONOTROUND`）与零投影层级，以留白及 `1px` 发丝线（`rgba(28,28,28,0.10)` / `0.40`）构建秩序；
  - 严格实行衬线标题/词头/引文/时间码（`Georgia` + `Noto Serif SC` + `STSong`）与无衬线功能文本（`Segoe UI Variable Text` + `Microsoft YaHei UI`）成对排印。
- **悬浮字幕条日夜双模与太阳/月亮一键切换（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/ui_icons.py`](desktop/ui_icons.py)）**：
  - 在 `draw_vector_icon` 新增极简线框 `sun` / `moon` 图标，在 HUD 顶栏集成 `hud_theme_btn` 一键在 **Option A（Ink Band `#1C1C1C` 墨黑反色条）** 与 **Option B（Warm Paper `#F9F8F6` 暖米纸条）** 之间实时切换，状态随 `hud_appearance["hud_theme"]` 持久化；
  - 默认采用无阴影渲染仅在 `< 22%` 极高透明模式下按需启用对比保护；点按单词交互改用 `2px #816D70` 锐利底线替代蓝底圆角胶囊。
- **精简界面文案与共用 Editorial Pull Quote / 极细滚动条组件**：
  - 抽取共用 `EditorialPullQuoteFrame` 与 `editorial_scrollbar_qss` 至 `desktop/ui_icons.py`，用于字幕台当前焦点引文与词典卡片例句；
  - 精简字幕台标题（`字幕台`）、生词本标题（`生词本`）、Contrast Lockup 反色主按钮（`导出 CSV` / `加载`）、极简底线搜索框（`搜索...`）及样式浮层控件文案，彻底移除装饰性英文标签与重复说明；
  - 同步补充/更新 HUD 太阳月亮切换与 Editorial 像素验证测试（`desktop/test_subtitle_unit.py`）。
- **视觉细节打磨与预览稿 100% 结构对齐（[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`desktop/floating_dock.py`](desktop/floating_dock.py)、[`desktop/app.py`](desktop/app.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/dictionary_window.py`](desktop/dictionary_window.py)、[`desktop/vault_window.py`](desktop/vault_window.py)）**：
  - 新增 `EditorialPopupMenu(QMenu)` 替代悬浮球与托盘右键原生圆角菜单：强控 Win11 `DWMWCP_DONOTROUND` 直角边框、暖米纸 `#F9F8F6` + `1px solid #1C1C1C` 描边，移除偏斜原生复选列并对齐线性图标，字幕激活态以陈玫 `#816D70` 与 `· 已开启` 清晰标识；
  - **双语字幕台 (`editorial_preview_console.png` 对齐)**：刊头右上精简为 4 枚 `1px` 图标（`pin`、`pause_play`、`subtitle`、`close`）并移除标题旁影片徽章；标题下方新增 Editorial 下划线文字控制栏（`B站: Gekit`/`B站扫码`、`OCR`、`链接` 与 `-2s`/`+2s`）；将 `EditorialPullQuoteFrame` 实时焦点引文区上移至刊头横线正下方，且为当前激活时间轴条目补齐上下细线与陈玫左脊；
  - **课堂生词本 (`editorial_preview_vault.png` 对齐)**：顶层结构改为右上极简 `- ×`、次行 `25px` 衬线 `生词本` 与墨黑直角 `导出 CSV`；重构条目为**三栏杂志列表**（左栏全大写短日期 `NOV 17` + 中栏 `18.5px` Georgia 词头与斜体音标、精简释义 + 右栏发声图标与悬停联动陈玫实心 `bookmark` 图标），移除冗余例句堆叠；
  - **HUD、查词卡片与全窗口 1px 墨黑直角外框统一**：修复 Windows 11 原生去圆角窗口（`DictionaryCard`、`VocabularyVaultWindow`、`EditorialPopupMenu`）因依赖系统 DWM 非客户区描边与设置 `border: none` 导致外圈 `1px #1C1C1C` 细黑线缺失的问题，并为非纯透模式下的 `HUD Light/Dark` 强制保留完整不透明 `#1C1C1C` 边界，实现全端窗口一致的 `1px` 墨黑外框；同时将词典卡头词升至 `26px` Georgia，并以统一 `QTextDocument` 管线赋予原文区 `162%` 行高与更宽卡片宽度，彻底消除英文拥挤粘连。

## v1.7.1 (2026-11-16)

### Bilibili (哔哩哔哩) 双语字幕与官方扫码授权支持
- **B站视频标识解析、WBI 签名外挂字幕与时间轴锁相（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)、[`desktop/app.py`](desktop/app.py)）**：
  - 新增 `BilibiliTranscriptLoader`，全面支持 `bilibili.com/video/BV...`（含分P `?p=N`）、`av` 号、`b23.tv` 短链及 Edge/Chrome B站播放标签页自动检测；
  - 集成 `finger/spi`（`buvid3`/`buvid4`）、`web-interface/nav` WBI 密钥混合与 MD5 `w_rid` 签名，优先筛选英文（`en` / `ai-en`）外挂 CC 字幕轨，并兼容 UP主标在 `zh-CN` 轨中的中英双语混排字幕自动提取英文行（拒收纯中文轨误译）；
  - 扩展 `_BrowserUIAProbe`：地址栏 `$addrUrl` 优先防 `iframe` 抢占，按 `NameProperty` 定位 `哔哩哔哩播放器` 并将检索限定在文档/播放器子树（大幅缩短 UIA 耗时），同时从播放器时间文本节点（`MM:SS / MM:SS`）实时读取当前播放秒数以对齐进度与暂停；
  - 修复 `_is_valid_video_window` 优先放行包含 `Settings`/`Terminal` 等关键字的真实浏览器标签页，并在检测到无英文 CC 的 B站视频（如纯硬字幕/纯音频视频）时通过 `status_notice` 明确提示已切入 B站左栏画面 OCR；
  - 新增 `_is_non_video_browser_page` 隔离 PDF 阅读器与普通非视频网页（`file://` / `.pdf` / 无播放器网页），并收紧字幕 OCR 最小字符/宽度门槛，防止屏幕任意英文触发 HUD 误粘附；
  - 全面升级中英混排字体栈与 Lexicon 视觉质感（[`desktop/app.py`](desktop/app.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/dictionary_window.py`](desktop/dictionary_window.py)、[`desktop/vault_window.py`](desktop/vault_window.py)）：
    - 英文正文与字幕切换为 Windows 11 `Segoe UI Variable Display/Text`（恢复原生光学字距，中文无缝回退 `Microsoft YaHei UI`），标题搭配 `Georgia` 衬线与定高紧凑 `Cascadia Mono` 时间码微胶囊；
    - 悬浮条改用低饱和冷白/银灰（`#F8FAFC` / `#CBD5E1`）配合随底条透明度动态调节的轻柔单/双层投影（消除 3D 重影锯齿），去除顶栏按钮灰底方框；
    - 字幕台历史激活行改用 QPainter 圆角背景与内嵌圆角左脊线（根除 QSS `border-left` 上下弯钩畸变），统一顶栏无框图标与白底细框按钮，并修复 `_HudStylePopover.sync_state` 选中状态联动断裂。
  - 精简悬浮条与字幕台界面：移除「实时追踪」「点词即查」「正在播报 · 点击单词即查」等装饰性提示文字，隐藏默认模式徽章，精简空状态为「暂无历史字幕」与单行 `等待字幕`；
  - 修复双语字幕台在 `WA_TranslucentBackground` 顶层窗下未定制 `QScrollBar:vertical` QSS 导致的右侧滚动条全黑异常（对齐米白底色与 `#D4D4D0` 滑块样式）。
- **应用内 B站官方扫码登录弹窗（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)、[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`ScreenLex.spec`](ScreenLex.spec)）**：
  - 拒绝直接读取或破解浏览器本地加密 Cookie，在「样式浮层」与字幕台工具栏/链接栏新增 `B站扫码` 入口，弹出防截屏（`WDA_EXCLUDEFROMCAPTURE`）的 `_BilibiliLoginDialog`（内置 `qrcode` 实时渲染与轮询）；
  - 扫码凭据安全持久化至 SQLite `app_settings["bilibili_auth"]`，授权或退出后自动联动引擎重查当前视频英文字幕轨。

### UIA 浏览器直读锁相与多标签页杂音根因修复
- **修复 Edge 多标签/零宽字符标题解析与 Electron 杂窗过滤（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)）**：
  - `extract_youtube_title_from_window` 自动清除 `\u200b`/`\ufeff` 零宽字符并兼容 `and N more pages - Personal - Microsoft Edge` 后缀，解决未触发字幕轨加载而退回网页 OCR 的问题；
  - `_is_valid_video_window` 收紧 `Chrome_WidgetWin_1/0` 窗口识别，剔除 `ApplicationFrameWindow` 与无关桌面 Electron 应用（如 `PCB Defect Inspection`），阻止抓错窗口。
- **常驻无窗 Windows UIA 探针 `_BrowserUIAProbe`（实时 URL + 进度条秒数 + 播放器坐标）**：
  - 后台直读浏览器当前标签页精确 URL（热读取耗时 `< 5ms`）、`Seek slider` 秒数与 `YouTube Video Player` 精确区域坐标；
  - 视频暂停（`Seek slider` 停滞 `>= 1.25s`）立即锁定字幕不动，快进/拖拽毫秒级跳转对齐；
  - `player_box` 严格依据真实视频播放器居中且底部内缩 `42px`，彻底避开右侧播放列表；锁定 YouTube 页面后严禁抓取网页播放列表/评论区 OCR 杂音。
- **全片批量分批预译与彻底消除字幕定位“鬼畜”（[`desktop/dictionary_service.py`](desktop/dictionary_service.py)、[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)）**：
  - [`desktop/dictionary_service.py`](desktop/dictionary_service.py) 新增 `translate_batch`（Edge 批量数组 + 有道换行批处理竞速）；字幕轨载入后优先秒译当前进度 20 句并按 24 句/批全量后台译完 100% 片段，关闭双路并发覆盖竞争；
  - 字幕台 `_sync_history_rows` 在译文更新时同步触发 `_ScrollSurfaceOverlay.invalidate(clear_pixmap=True)`，根除滚动分片缓存长期残存“正在翻译...”假象；
  - 锁定 UIA `player_box` 后禁止 `_refresh_target_window` 周期性以整窗坐标覆盖引发左右横跳，配合 `12px` 坐标死区、`_follow_player_box` 贴底直调与 `140ms` 顶栏防抖定时器，消除字幕上下/左右鬼畜抖动。
- **悬浮条外观自定义控制与配置持久化（[`desktop/database.py`](desktop/database.py)、[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)）**：
  - 悬停顶栏新增 `样式` 微按钮（搭配重绘 `sliders` 滑块图标），点击弹出防截屏（`WDA_EXCLUDEFROMCAPTURE`）紧凑调节浮层 `_HudStylePopover`；
  - 支持 **字号大小（`10pt~22pt` 中英文联动）**、**底条浓度（`0%~90%`，`0%` 纯字描边 + 悬停微显轮廓）**、**语言模式（`双语` / `仅英文` / `仅中文`）**、**字幕宽度（`窄条` / `适中` / `宽屏`）** 及 **拖拽固定位置记忆 / 一键恢复默认**；
  - 在 [`desktop/database.py`](desktop/database.py) 新增独立 `app_settings` 表及 `get_setting` / `set_setting`，跨重启自动恢复配置且不触动生词本数据。
- **重绘字幕工具栏矢量图标并升级为“图标 + 中文”微按钮（[`desktop/ui_icons.py`](desktop/ui_icons.py)、[`desktop/subtitle_window.py`](desktop/subtitle_window.py)）**：
  - 重绘 `pin`（准星靶标替代易误解的圆圈十字）、`pause_play`（圆圈暂停徽标）、`link`（`-42°` 斜向链环）、`dock`（带圆点列表图标）及 `subtitle`（屏幕双线框），消除抽象符号歧义；
  - `VectorIconButton` 原生支持 `rgba(...)` 半透明底色与文字标签，悬浮字幕条与独立字幕台顶栏按钮同步配齐 `归位`、`点词暂停`、`链接`、`字幕台`、`悬浮条`、`清空` 清晰中文微标签；
  - 修复悬停 `QToolTip` 提示框被未加限定符 `hud_top_widget` 样式污染为纯黑矩形的问题（采用 `QWidget#SubtitleHudTop` 选择器隔离并显式配置 `#1E293B` 深色底 / `#F8FAFC` 亮字 `QToolTip` 全局样式）。

---

## v1.7 (2026-11-16)

### 免开原生字幕自动时间轴同步与半透明磨砂字幕条重构
- **免开 YouTube 原生字幕 · 标题自动识别 + 预翻译时间轴同步（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)、[`desktop/app.py`](desktop/app.py)）**：
  - 修复 `YouTubeTranscriptLoader._extract_player_response`：优先接入免令牌 YouTube InnerTube 移动端接口（`IOS 20.10.4` / `ANDROID 20.10.38`），彻底解决网页端 `&exp=xpe` 返回 0 字节空响应的问题；
  - 新增 `extract_youtube_title_from_window` 与 `resolve_video_id_by_title`：自动识别前台或底层浏览器正在播放的 YouTube 视频标题并解析 `videoId`，免去开启原生字幕及复制粘贴链接即可后台全片拉取字幕轨；
  - 校正 ASR 重叠时间戳并按自然从句切分，引入 `80ms` 高精度本地播放时钟 `_clock_timer`、提前 `15` 句后台预翻译（配合 Keep-Alive 竞速）以及播放器进度条时间码视觉校准（`extract_player_timestamp_sec`），切换字幕 **0ms 延迟**；
  - 无原生 CC 字幕时自动计算视频播放器底部居中安全区 `player_box` 定位；悬停顶栏新增 `[-2s]` / `[+2s]` 快捷时间轴对齐按钮。
- **真正半透明暗色磨砂底条 + 影视级文字阴影（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)）**：
  - 为 `BilingualSubtitleWindow` 恢复 `WA_TranslucentBackground` 透明通道（在 Win11 上禁用系统 DWM 外框光晕），`MODE_INPLACE_HUD` 改用抗锯齿 `10px` 圆角半透明暗色磨砂底条 `rgba(10, 12, 16, 158)`，自然透出底层视频画面，告别生硬黑框弹窗感；
  - `InteractiveSubtitleText` 与新增 `_ShadowLabel` 均支持黑色描边投影，确保亮色画面下依然清晰可读；`WDA_EXCLUDEFROMCAPTURE (0x11)` 截屏隐身在双模式下持续生效。

---

## v1.6.2 (2026-11-16)

### 双语字幕丝滑无感原位覆盖与毫秒级实时响应优化
- **沉浸式无感原位字幕舱（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)）**：
  - `MODE_INPLACE_HUD` 移除常驻顶部状态栏与亮灰边框，改为影院深黑无缝视窗（`#101216` / `#14161C`），顶部控制条默认隐藏并在鼠标悬停时平滑浮现（`enterEvent` / `leaveEvent`）；
  - 闲置无字幕态高度压缩至 `42px` 轻提示条，新句首次待译 ~200ms 内隐藏第二行防闪烁撑高；
  - 重构 `compute_hud_target_rect` 与 `16ms` 定时器 `_apply_smooth_step`：严格以原生字幕左右 `>=28px` 与底部基线实现 100% 完整包络绝不露边，配合 60FPS 阻尼滑移及外露边缘即时扩张，消除 28px 阶跃瞬移感。
- **静态课件防漏检、窄带追踪与零丢词翻译加速（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)、[`desktop/dictionary_service.py`](desktop/dictionary_service.py)）**：
  - 新增 6×8 局部网格最大帧差门控 `_has_significant_frame_change`（兼顾全图均值与最大局部块变化），解决静止 PPT 课件上底部新蹦出 1~2 词因全图占比低被误过滤的问题；
  - 默认轮询提速至 `110ms`，新增自适应窄带 ROI 截屏 OCR 与每 6 帧及未命中全量重定位，`RollingCaptionStitcher` 新增 14 词紧凑子句 `hud_en` 与无标点口语连词自然切分；
  - `_schedule_active_translation` 升级为单飞序列号接力机制，彻底消除高速吐词时的防抖丢词，辅以同句增量前缀中文托底实现 0ms 连续显示；
  - [`desktop/dictionary_service.py`](desktop/dictionary_service.py) 新增线程安全 HTTP/HTTPS Keep-Alive 长连接复用池（`_http_post_keepalive`）与启动后台预热 `prewarm_translation_connections`，在线单次翻译耗时由 `571~1531ms` 降至 `~250ms`。

---

## v1.6.1 (2026-11-16)

### 双语字幕防自我截屏、课件文字过滤与滚动稳定性修复
- **彻底解决双语字幕台自我 OCR 死循环**：
  - [`desktop/subtitle_window.py`](desktop/subtitle_window.py) 中无论处于 `MODE_INPLACE_HUD` 还是 `MODE_CONSOLE_DRAWER` 模式，均永久维持 Windows `WDA_EXCLUDEFROMCAPTURE (0x11)` 截屏隐身，[`desktop/dictionary_window.py`](desktop/dictionary_window.py) 同步启用截屏隐身，彻底阻断字幕台自身文本被重新抓屏循环识别；
  - Windows 11 下切为原生 DWM 硬件合成圆角边框（`apply_native_win11_chrome`），移除透明分层重影及右上角黑点残留，并将时间戳列宽扩展至 `64px` 完整显示 `HH:MM:SS`。
- **白底 PPT 课件与播放器杂音强力隔离（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)）**：
  - 新增像素对比度门控 `_is_likely_video_subtitle_line`，对高亮浅底文字（如 PPT 幻灯片标题、网页白底评论区）自动识别拦截，仅放行具备暗背景高对比衬托的真实视频字幕；
  - 扩展 `_INLINE_TIMESTAMP_RE` 拦截形如 `12:34 / 56-oo` 的播放器进度条误识噪点。
- **缝合去重、防抢滚屏与双线程池译文加速**：
  - `RollingCaptionStitcher` 新增 `_dedup_repeated_phrases` 折叠相邻重复 1~6 词片段，并将连续子串检查前置至重叠拼接之前，杜绝词组循环复读；
  - `LiveSubtitleEngine` 拆分为活跃句与历史句双独立线程池并支持活跃译文秒级继承；`BilingualSubtitleWindow` 仅在确实新增历史条目时自动滚底，用户向上滚轮回看立即锁定浏览位置。

---

## v1.6 (2026-11-16)

### 实时双语字幕舱、滚动碎词缝合与独立字幕台系统
- **零取景框自适应字幕捕获引擎（[`desktop/subtitle_engine.py`](desktop/subtitle_engine.py)）**：
  - 新增 `LiveSubtitleEngine`，自动追踪当前前台浏览器或媒体播放器窗口（过滤非媒体/开发聊天窗口并支持多显示器异 DPI 自适应），无需任何常驻实体取景框；
  - 采用 `220ms` 像素帧差门控，画面静止时 0 OCR 消耗、0 网络请求；字幕消失后静止 `2.0s` 自动触发归档清屏；
  - 扩展 [`win_ocr.py`](desktop/win_ocr.py) 的 `_recognize_winrt_async(..., raw_only=True)` 绕过手写替换污染，结合垂直行簇有效字母密度与水平居中度评分自动定位字幕区域并过滤播放器底部时间条噪点（如 `12:34 / oo`）。
- **YouTube 滚动碎词智能缝合状态机（`RollingCaptionStitcher`）**：
  - 基于词级前后缀重叠自动将 YouTube 逐词吐词、双行交替滚动碎片拼接为完整长句，遇到句末标点自动拆分短/长句并归档入历史记录，同步异步并发调用国内直连翻译生成中文对照。
- **DWM 硬件级透视原位遮盖与独立双语滚动台（[`desktop/subtitle_window.py`](desktop/subtitle_window.py)）**：
  - **方案 A（`MODE_INPLACE_HUD`）**：启用 Windows `SetWindowDisplayAffinity(..., 0x00000011)`（`WDA_EXCLUDEFROMCAPTURE`），人眼可见深炭磨砂双语字幕舱直接原位遮盖 YouTube 原生单语字幕（内置 `28px` 防抖阻尼随动），后台截屏自动透视窗口读取底层字幕，彻底消除双层重叠（系统不支持时自动降级为独立台）；
  - **方案 B（`MODE_CONSOLE_DRAWER`）**：可一键切换至独立停靠的 Pure White 风格双语滚动字幕台，实时展示“正在播报”焦点句与可向上回看的完整双语历史滚屏流。
- **YouTube 链接完整字幕提取同步（`YouTubeTranscriptLoader`）**：
  - 支持粘贴 YouTube 视频链接（`watch?v=`、`youtu.be/`、`/shorts/`、`/live/`）免插件提取字幕轨并合句翻译，配合 `match_timeline_index` 根据屏幕实时 OCR 词片自动锚定并跟随视频播放进度（含快进/快退）。
- **词块 0ms 点按即查与自动暂停/恢复联动（[`desktop/app.py`](desktop/app.py)、[`desktop/floating_dock.py`](desktop/floating_dock.py)、[`desktop/ui_icons.py`](desktop/ui_icons.py)）**：
  - 基于纯 `QPainter` 自绘交互词流控件 `InteractiveSubtitleText`，鼠标或触屏轻点字幕内任意英文单词 0ms 弹出 `DictionaryCard`，自动预填完整句及对应中文译文；
  - 新增 `VideoPlaybackController` 支持查词时自动发送空格暂停目标视频、正常关闭该词典卡后自动恢复播放；托盘菜单、右侧灵动胶囊中键及右键快捷菜单均已集成「实时双语字幕」一键开关，并在 [`ScreenLex.spec`](ScreenLex.spec) 同步更新打包模块清单。

---

## v1.5 (2026-11-16)

### 语法抗噪、重点短语钻取与卡片内手势二次识别
- **连字符（`-`）格式宽容与抗噪升级**：
  - 在 [`desktop/grammar_checker.py`](desktop/grammar_checker.py) 的 `check_grammar_and_spelling` 中屏蔽 `HYPHEN` / `EN_COMPOUNDS` 以及仅建议增删连字符的纯格式纠错（如 `doom scrolling` ➔ `doom-scrolling`），并扩展空白与连字符等价过滤，彻底消除无关大雅的连字符标错与强行改写。
- **卡片底部「重点短语」智能提取与一键钻取**：
  - 在 [`desktop/dictionary_service.py`](desktop/dictionary_service.py) 中新增 `extract_key_phrases` 短语提取引擎，针对长句与段落自动提取引号强调词（免疫 `Here's` / `It's` 等缩写撇号干扰）、内置词库多词短语、常见固定搭配/动词短语及连字符复合词（最多 4 条），扩充 `doom scrolling`、`break free` 等离线词条，并在 `enrich_lookup_sync` 中并发解析释义；
  - 在 [`desktop/dictionary_window.py`](desktop/dictionary_window.py) 的长句/段落卡片最下方新增「重点短语」展示区（`PhraseItemWidget`），点击任意短语即可在当前卡片内直接钻取完整双轨音标、详细释义与语境例句，并通过标题栏 `← 返回` 按钮（`_history_stack`）随时毫秒级返回原段落翻译。
- **图二（选取原文区）内嵌手势二次识别**：
  - 新增 `InteractiveSentenceLabel`，基于 `QTextDocument` 精确映射原文区域单词坐标，直接复用 [`desktop/freeze_overlay.py`](desktop/freeze_overlay.py) 抽离的共享几何选词核心 `select_word_indices_by_gesture`，支持在卡片底部原文区直接轻点单词、横向划词、跨行拖选词组及闭合画圈圈选进行二次查词；
  - 在 [`desktop/ui_icons.py`](desktop/ui_icons.py) 的 `SmoothScrollController` 中增加 `_targets_gesture_widget` 防拦截判断，保障原文区手势选词与卡片平滑滚动互不干扰。

---

## v1.4 (2026-11-13)

### 界面与视觉体验重构（Pure White Lexicon + D3 悬浮徽章）
- **去模板化纯白学术风改版**：新增统一视觉主题与矢量绘图模块 [`desktop/ui_icons.py`](desktop/ui_icons.py)，全面移除原有圆角大胶囊（Pill）样式与系统 Emoji 图标，采用高对比度纯白卡片（`#FFFFFF`）搭配普鲁士藏青（`#1A365D`）学术点缀色。
- **D3 极简边缘悬浮徽章**：重构 [`FloatingZenPill`](desktop/floating_dock.py)，精简为 `34×34px` 哑光深炭（`#1F232A`）圆角徽章（`5px` 圆角）搭配居中对焦工字（I-beam）矢量准星，支持点击定格、长按/右键唤出生词本与上下边缘拖动调整高度。
- **窗口圆角、顶边渲染与原生 Win11 DWM 合成修复**：
  - 在 Windows 11 上，[`DictionaryCard`](desktop/dictionary_window.py)（词典卡）与 [`VocabularyVaultWindow`](desktop/vault_window.py)（课堂生词本）切换为不透明硬件合成窗口（启用 `apply_native_win11_chrome` 原生抗锯齿圆角、`1px` `#CFCFCF` 细边框与 DWM 原生阴影），彻底消除 `WA_TranslucentBackground` + `UpdateLayeredWindow` 每帧 CPU 上传约 `7.8MB` 位图带来的 DWM 掉帧瓶颈（Windows 10 等旧系统继续回退 `LexiconCardFrame` 纯白抗锯齿圆角渲染），内部按钮及横幅收敛至 `4px–5px` 克制微圆角；
  - 移除卡片顶边深色装饰条（`spine_height=0.0`），彻底修复深色桌面背景下顶边外框与圆角之间出现视觉空隙的显示问题；
  - 统一通过 `clear_layout` 安全回收动态表单与例句区控件，消除高频切词时的视觉重叠残影。
- **高刷平滑滚动与边缘羽化渐隐**：
  - 移除窗口级 `QGraphicsDropShadowEffect` 离屏高斯模糊瓶颈；强制滚动视口（`#qt_scrollarea_viewport`）与滚动区保持不透明纯白底色并移除异常 `WA_OpaquePaintEvent` 属性，彻底消除搜索框下方及列表边缘偶发透明穿孔缺陷；
  - 新增 `SmoothScrollController`（通过 `DwmGetCompositionTimingInfo` 实现 DWM VBlank 锁相环与 `timeBeginPeriod(1)` 亚毫秒相位步进，自研鼠标/触控板/触屏惯性管线绕开 Qt 60Hz `QScroller` 上限）、`normalize_win32_process_priority` 调度保护（防止继承低优先级调度），与 `_ScrollSurfaceOverlay` `128px` 分片条带 LRU 缓存合成层（配合闲置渐进预烘焙、单次 DWM 合成提交与滚动条拖拽节流即时重绘，并在渲染离屏切片时保护 `updatesEnabled` 继承状态），彻底杜绝滚出初始缓冲区后变白及跨片卡顿掉帧，将 165Hz 真实桌面 DWM 呈现帧率提升至原生流畅水平；
  - 静止态 `ScrollEdgeFeatherOverlay` 保留上下端分离式渐变条，运动态由合成层直接叠加一致的四段式边缘渐变，配合 `#EAEAE6` 搜索底边细线实现丝滑渐隐。
- **定格遮罩与托盘规范化**：[`FreezeSelectOverlay`](desktop/freeze_overlay.py) 与 [`GestureOverlay`](desktop/gesture_overlay.py) 统一使用 `2px` 选词高亮框（普鲁士蓝选词、森林绿入库、绯红取消）与哑光炭黑顶部分流 HUD；[`desktop/app.py`](desktop/app.py) 同步更新托盘图标配色、菜单圆角与无符号纯文字通知提示。

### 识别管线、词典翻译与数据可靠性增强（v1.3 至 v1.4）
- **手写区域水平自适应拆分与 OCR 抗噪**：
  - `detect_handwriting_regions` 新增水平列间距分段阈值（`<= 80px`），避免页眉/角落彩色标记将整行印刷体大标题误合并进手写切片；
  - 手写专精识别链路彻底移除 WinRT 兜底回退，降低连笔草书产生乱码碎片的概率；`process_ocr_tokens` 支持 `O(N)` / 数学下标变量保护，并对语义断裂（如非系表结构下的 `be + low`）进行上下文聚合。
- **亚毫秒级离线拼写纠偏与学术保护**：
  - `check_spelling` 引入 1-edit 倒排索引与学术高频词优先表，离线未命中检测耗时低于 `2ms`；
  - 全面免疫英文简缩词（如 `doesn't`）、合法语法屈折词（`-s`/`-ed`/`-ing`）及复杂度/数学表达式（`O(1)`、`O(N^2 + M)`、`x_1`），并拦截误改合法短语（如 `bust the myth`）。
- **双层隔离并发与国内多轨直连翻译**：
  - [`desktop/dictionary_service.py`](desktop/dictionary_service.py) 将外层编排线程池（`_PIPELINE_EXECUTOR`）与内层 HTTP 竞速线程池（`_HTTP_RACE_EXECUTOR`）物理隔离，避免高并发富化请求下的线程池饥饿死锁；
  - 翻译接口并发竞速有道直连与微软 Edge 翻译接口，并使用 `urllib.request.ProxyHandler({})` 主动旁路失效本地代理；同步扩充 `OFFLINE_DEFS` 离线学术词条与屈折词形还原查询。
- **生词本持久化目录隔离与占位符防污染**：
  - [`desktop/database.py`](desktop/database.py) 将持久化数据库路径固定至 `%APPDATA%\ScreenLex\screenlex.db`（支持从旧目录自动迁移），与构建目录 `dist/` 物理隔离，升级或重新打包不再覆盖用户生词数据；
  - 入库及异步更新自动剥离“正在获取/正在翻译”骨架占位符，并通过 `update_enrichment_if_collected` 条件原子写回真实释义。

---

## v1.2 (2026-09-21)

- **全场景英文手写体与草书识别**：集成轻量云端专精引擎与本地笔迹增强管线。
- **直线横向划词手势与 0ms 释放**：多边形圈选（Lasso Loop）、矩形拉框（Drag Box）与横向划线三轨解耦。
- **智能语法纠错与学术表达建议**：内置离线基础词库与长句语法润色面板，无错误时自动隐藏保持界面简洁。
- **字体与零全局钩子**：统一 `Microsoft YaHei UI` 排版，非定格状态下彻底移除全局鼠标长按钩子拦截。

---

## v1.1 (2026-09-21)

- **全屏定格 0ms 呼出与后台 OCR 预提取**：消除点击悬浮控件时的主线程冻结卡顿。
- **画圈闭环多边形圈选（Winding Number）**：支持起笔引线自动切除与行感知上下文捕获。
- **进程生命周期修复**：升级锁机制并完善退出清理逻辑，避免托盘或窗口残留。
