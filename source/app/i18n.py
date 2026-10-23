"""Bilingual (zh-CN / en) UI strings with persistence via QSettings.

All user-visible widget text lives here as key -> {zh, en}.
Log/diagnostic lines from the engine stay in English; widget chrome
(buttons, labels, group titles, tabs, dialogs) must be bilingual (§B.16).
"""

from __future__ import annotations

STRINGS = {
    # ---- shell ----
    "app_title": {"zh": "smartLarch Platform — Athena 预处理 + Larch 拟合", "en": "smartLarch Platform — Athena prep + Larch fit"},
    "app_subtitle": {
        "zh": "XAFS 一体化：归一化 / 扣背景 / FT / EXAFS 拟合",
        "en": "Unified XAFS: normalize / background / FT / EXAFS fit",
    },
    "open_data": {"zh": "打开…", "en": "Open…"},
    "open_data_tip": {"zh": "打开 xdi / csv / dat / prj（可多选）", "en": "Open xdi / csv / dat / prj (multi-select)"},
    "quick_prj": {"zh": "快速 prj", "en": "Quick .prj"},
    "quick_prj_empty": {"zh": "(快速目录无 .prj)", "en": "(no .prj in quick folders)"},
    "quick_prj_dir_tip": {"zh": "选择快速打开目录（记忆选择；选定后快速 prj 下拉框只列该目录，「打开数据」对话框也从该目录起步）", "en": "Choose the quick-open folder (remembered; the Quick .prj dropdown then lists only this folder, and the Open-data dialog also starts here)"},
    "save": {"zh": "保存", "en": "Save"},
    "save_tip": {
        "zh": "保存为 Athena 兼容 .prj（全部数据组 + 归一化/FT 参数）",
        "en": "Save Athena-compatible .prj (all groups + norm/FT parameters)",
    },
    "about": {"zh": "关于", "en": "About"},
    "settings": {"zh": "设置", "en": "Settings"},
    "settings_tip": {
        "zh": "设置（语言 / 编码助手 / 升级 / 托盘 / 关于）",
        "en": "Settings (language / coding agents / updates / tray / about)",
    },
    "settings_auto_saved": {
        "zh": "更改将自动保存。",
        "en": "Changes are saved automatically.",
    },
    "settings_general_block": {"zh": "常规", "en": "General"},
    "settings_language": {"zh": "界面语言", "en": "Language"},
    "settings_coding_block": {"zh": "编码助手接口", "en": "Coding agents"},
    "settings_coding_hint": {
        "zh": "每行：AI Check 勾选 | 名称 | 程序路径（自动发现或浏览选择）| 浏览… | 打开 | 状态。只有 Kimi 和 Claude 的 CLI 能关掉全部工具，所以只有这两行可以打勾给 AI Check 用（用你自己的登录，不需要 API Key）。「打开」：Kimi/Grok/Qwen Code/DeepSeek 在新终端启动各自的 CLI（Kimi Code/Grok Build CLI、Qwen Code 的 qwen、DeepSeek Harness 的 dsh）；Codex/Claude 优先打开桌面客户端。工作目录为当前谱文件夹或项目根。",
        "en": "Each row: AI Check tick | name | program path (auto-found or Browse) | Browse… | Open | status. Only the Kimi and Claude CLIs can switch every tool off, so only those two rows can be ticked for AI Check (your own sign-in, no API key). Open: Kimi/Grok/Qwen Code/DeepSeek start their CLI in a new terminal (Kimi Code/Grok Build CLI, Qwen Code's qwen, DeepSeek Harness's dsh); Codex/Claude open the desktop app first. The working directory is the current spectrum folder or project root.",
    },
    "coding_kimi": {"zh": "Kimi", "en": "Kimi"},
    "coding_grok": {"zh": "Grok", "en": "Grok"},
    "coding_codex": {"zh": "Codex", "en": "Codex"},
    "coding_claude": {"zh": "Claude", "en": "Claude"},
    "coding_qwen": {"zh": "Qwen Code", "en": "Qwen Code"},
    "coding_deepseek": {"zh": "DeepSeek", "en": "DeepSeek"},
    "coding_open": {"zh": "打开", "en": "Open"},
    "coding_browse": {"zh": "浏览…", "en": "Browse…"},
    "coding_client_path": {
        "zh": "CLI/客户端（自动或浏览选择）",
        "en": "CLI/client (auto or Browse)",
    },
    "coding_browse_title": {
        "zh": "选择 {name} 客户端程序（.exe / .cmd / .bat）",
        "en": "Choose {name} client (.exe / .cmd / .bat)",
    },
    "coding_need_path": {
        "zh": "未找到 {name} CLI/客户端。请先安装对应 CLI，或点「浏览…」选择本机 .exe/.cmd/.bat。",
        "en": "{name} CLI/client not found. Install the corresponding CLI or use Browse… to choose a local .exe/.cmd/.bat.",
    },
    "coding_open_failed": {
        "zh": "无法启动 {name}。\n{detail}",
        "en": "Could not start {name}.\n{detail}",
    },
    "coding_path_saved": {
        "zh": "已保存 {name} 路径",
        "en": "Saved {name} path",
    },
    "coding_terminal": {"zh": "审核终端", "en": "Review terminal"},
    "ai_menu_tip": {
        "zh": "打开编码助手（Kimi/Grok 使用终端 CLI）",
        "en": "Open coding agent (Kimi/Grok use terminal CLI)",
    },
    "open_output_tip": {
        "zh": "打开输出目录",
        "en": "Open output folder",
    },
    "settings_api_block": {"zh": "API 绑定", "en": "API binding"},
    "settings_api_hint": {
        "zh": "每行：AI Check 勾选 | 服务商 | API Key | 模型 | 状态。打勾的行可被 AI Check 使用（模型在「验证连接」后从该服务商的列表里选）。API Key 也只注入对应客户端：Claude↔Anthropic、Codex↔OpenAI、Kimi↔Moonshot、Grok↔xAI、DeepSeek↔DeepSeek、Qwen Code↔百炼 Token Plan。空白审核终端不带密钥。密钥仅存本机（QSettings），勿提交到 git。",
        "en": "Each row: AI Check tick | provider | API key | model | status. Ticked rows may be used by AI Check (pick the model from the provider's list after Verify connection). Each key is also injected only into its matching client: Claude↔Anthropic, Codex↔OpenAI, Kimi↔Moonshot, Grok↔xAI, DeepSeek↔DeepSeek, Qwen Code↔Bailian Token Plan. Blank review terminals receive no keys. Keys stay on this PC (QSettings); never commit them.",
    },
    "api_anthropic": {"zh": "Claude / Anthropic", "en": "Claude / Anthropic"},
    "api_openai": {"zh": "Codex / OpenAI", "en": "Codex / OpenAI"},
    "api_moonshot": {"zh": "Kimi / Moonshot", "en": "Kimi / Moonshot"},
    "api_xai": {"zh": "Grok / xAI", "en": "Grok / xAI"},
    "api_deepseek": {"zh": "DeepSeek", "en": "DeepSeek"},
    "api_qwen": {"zh": "Qwen / 百炼 Token Plan", "en": "Qwen / Bailian Token Plan"},
    "api_qwen_region": {"zh": "Qwen 区域", "en": "Qwen region"},
    "api_qwen_region_intl": {"zh": "国际（新加坡）", "en": "International (Singapore)"},
    "api_qwen_region_cn": {"zh": "中国（北京）", "en": "China (Beijing)"},
    "api_qwen_region_tip": {
        "zh": "Token Plan Key 只能用于开通它的区域：国际站 Key 配国际端点，国内 Key 配北京端点；选错会 401。",
        "en": "A Token Plan key only works on the region it was issued for: international keys on the Singapore endpoint, China keys on the Beijing endpoint; the wrong one returns 401.",
    },
    "api_key_ph": {"zh": "粘贴 API Key…", "en": "Paste API key…"},
    "api_inject": {
        "zh": "向对应 CLI 注入其 API Key",
        "en": "Inject only the matching API key",
    },
    "api_open_terminal": {
        "zh": "打开无密钥审核终端",
        "en": "Open key-free review terminal",
    },
    "api_saved": {"zh": "已保存 API 绑定", "en": "API binding saved"},
    "settings_about_block": {"zh": "关于", "en": "About"},
    "lang_zh": {"zh": "简体中文", "en": "简体中文"},
    "lang_en": {"zh": "English", "en": "English"},
    "settings_update_block": {"zh": "版本升级", "en": "Updates"},
    "settings_update_folder": {
        "zh": "主程序升级包目录（启动自动更新）",
        "en": "Main-program update folder (auto on start)",
    },
    "settings_update_folder_hint": {
        "zh": "启动时自动扫描更高版本的 smartLarch Platform（latest.json / 带 vX.Y.Z 的目录或 zip）并应用。",
        "en": "On startup, auto-scan for newer smartLarch Platform packages (latest.json / vX.Y.Z) and apply.",
    },
    "settings_plugins_folder": {
        "zh": "插件目录（启动自动更新）",
        "en": "Plugin directory (auto-update on start)",
    },
    "settings_plugins_folder_hint": {
        "zh": "网页插件所在目录。固定名文件为当前版；同目录或 updates/ 下更高版本的 *_vX.Y.Z.html 会在启动时自动覆盖。",
        "en": "Web plugins folder. Fixed names are current; newer *_vX.Y.Z.html here or in updates/ auto-replace on start.",
    },
    "box_tools_plugins": {"zh": "附加工具", "en": "Companion tools"},
    "plugins_hint": {
        "zh": "网页插件在浏览器中打开；合并作用于左侧已标记组。插件目录在设置中配置。",
        "en": "Web plugins open in the browser; Merge applies to marked groups on the left. Plugin folder is set in Settings.",
    },
    "settings_update_auto_note": {
        "zh": "主程序与插件均在启动时自动检测并更新（覆盖当前插件文件）。只需配置好目录。",
        "en": "Main app and plugins auto-detect and update on startup (plugins overwrite in place). Only set the folders.",
    },
    "settings_runtime_block": {
        "zh": "科学运行时（xraylarch）",
        "en": "Scientific runtime (xraylarch)",
    },
    "settings_runtime_hint": {
        "zh": "首次启动若本机没有环境，会自动下载 Miniconda 并安装 xraylarch+PySide6 到用户目录（需联网，约 1–2 GB）。也可手动运行 Install-Runtime 脚本。",
        "en": "If no environment is found, first launch downloads Miniconda and installs xraylarch+PySide6 into a user folder (needs internet, ~1–2 GB). Or run Install-Runtime manually.",
    },
    "settings_runtime_path": {"zh": "当前 Python", "en": "Current Python"},
    "settings_runtime_install": {"zh": "安装 / 修复运行时", "en": "Install / repair runtime"},
    "plugin_missing": {
        "zh": "未找到插件文件：{name}\n请检查设置中的插件目录，或运行 Sync-Plugins。",
        "en": "Plugin file missing: {name}\nCheck the plugin directory in Settings, or run Sync-Plugins.",
    },
    "plugin_opened": {"zh": "已打开 {name}", "en": "Opened {name}"},
    "plugins_auto_updated": {
        "zh": "插件已自动更新：{list}",
        "en": "Plugins auto-updated: {list}",
    },
    "platform_auto_update": {
        "zh": "发现主程序 v{new}，已打开新版本目录（当前 v{cur}）。请从新目录启动。",
        "en": "Found main app v{new}; opened its folder (running v{cur}). Start from the new folder.",
    },
    "browse": {"zh": "浏览…", "en": "Browse…"},
    "open_folder": {"zh": "打开目录", "en": "Open folder"},
    "check_update": {"zh": "立即检查更新", "en": "Check updates now"},
    "apply_update": {"zh": "打开新版本", "en": "Open new version"},
    "settings_tray_block": {"zh": "窗口 / 托盘", "en": "Window / tray"},
    "close_action": {"zh": "点击关闭时", "en": "On window close"},
    "close_action_quit": {"zh": "直接退出程序", "en": "Quit the app"},
    "close_action_tray": {"zh": "最小化到系统托盘", "en": "Minimize to system tray"},
    "tray_show": {"zh": "显示主窗口", "en": "Show window"},
    "tray_quit": {"zh": "退出", "en": "Quit"},
    "tray_hidden": {
        "zh": "smartLarch Platform 已最小化到托盘。双击托盘图标可恢复。",
        "en": "smartLarch Platform is in the tray. Double-click the tray icon to restore.",
    },
    "update_none": {
        "zh": "未找到比当前 v{ver} 更高的版本。",
        "en": "No version newer than v{ver} found.",
    },
    "update_found": {
        "zh": "发现新版本 v{new}（当前 v{cur}）\n{path}",
        "en": "Found v{new} (current v{cur})\n{path}",
    },
    "update_no_folder": {
        "zh": "请先指定升级包目录。",
        "en": "Set an update package folder first.",
    },
    "update_applied_hint": {
        "zh": "已打开新版本目录。请运行其中的 Launch-smartLarch-Platform.bat，并可用 Install-Shortcuts.ps1 更新快捷方式。",
        "en": "Opened the new version folder. Run Launch-smartLarch-Platform.bat there; use Install-Shortcuts.ps1 to refresh shortcuts.",
    },
    "lang": {"zh": "English", "en": "中文"},
    # ---- groups panel ----
    "groups": {"zh": "数据组", "en": "Groups"},
    "mark_all": {"zh": "全选", "en": "Select all"},
    "mark_none": {"zh": "全不选", "en": "Select none"},
    "mark_invert": {"zh": "反选", "en": "Invert selection"},
    "mark_all_short": {"zh": "全选", "en": "All"},
    "mark_none_short": {"zh": "全不选", "en": "None"},
    "mark_invert_short": {"zh": "反选", "en": "Invert"},
    "mark_all_detail": {"zh": "标记所有已载入曲线；批量操作和“已标记”绘图会作用于它们", "en": "Mark every loaded curve; batch actions and Marked plotting apply to them"},
    "mark_none_detail": {"zh": "取消标记所有曲线", "en": "Remove the mark from every curve"},
    "mark_invert_detail": {"zh": "反转每条曲线的标记状态", "en": "Reverse the mark state of every curve"},
    "plot_current": {"zh": "当前", "en": "Current"},
    "plot_current_tip": {"zh": "只画选中的组", "en": "Plot only the selected group"},
    "plot_marked": {"zh": "勾选", "en": "Marked"},
    "plot_marked_tip": {
        "zh": "勾选组叠加；当前组始终显示，边窗跟着当前组",
        "en": "Overlay marked groups; current stays on the plot and owns the edge window",
    },
    "remove_current": {"zh": "删除当前组", "en": "Remove current"},
    "remove_current_tip": {"zh": "删除当前选中的组", "en": "Remove the currently selected group"},
    "close_file": {"zh": "关闭当前文件", "en": "Close current file"},
    "close_file_tip": {"zh": "移除与当前组来自同一文件的所有组", "en": "Remove every group that came from the current group's file"},
    "remove_marked": {"zh": "删除勾选", "en": "Remove selected"},
    "remove_marked_tip": {"zh": "删除所有勾选的组", "en": "Remove all checked groups"},
    "remove_all_groups": {"zh": "删除全部组", "en": "Remove all"},
    "remove_all_groups_tip": {"zh": "删除列表中的所有组", "en": "Remove every group in the list"},
    "rename": {"zh": "重命名", "en": "Rename"},
    "rename_tip": {"zh": "重命名当前组", "en": "Rename current group"},
    "rename_prompt": {"zh": "组名称：", "en": "Group name:"},
    "current_none": {"zh": "当前: —", "en": "Current: —"},
    # ---- per-group meta ----
    "z": {"zh": "元素", "en": "Z"},
    "edge": {"zh": "边", "en": "Edge"},
    "dE": {"zh": "ΔE", "en": "ΔE"},
    "imp": {"zh": "权重", "en": "Imp"},
    "imp_tip": {
        "zh": "合并时的权重（Athena importance）。1=同等份额；0=不参与平均。改完后点合并才会用到。",
        "en": "Merge weight (Athena importance). 1 = equal share; 0 = left out of the average. Used when you Merge.",
    },
    "freeze": {"zh": "冻结", "en": "Freeze"},
    "auto_edge": {"zh": "自动寻边", "en": "Auto edge"},
    "auto_edge_tip": {"zh": "在当前谱上找 E0 并猜测元素 + 边", "en": "Find E0 on current spectrum and guess Element + Edge"},
    "auto_edge_on_select": {"zh": "切换或导入曲线时自动识别元素/吸收边", "en": "Auto-detect element/edge when selecting or importing curves"},
    # ---- plot toolbar ----
    "space_grid_tip": {"zh": "四格视图 E/k/R/q", "en": "4-panel view E/k/R/q"},
    "space_tip": {"zh": "{s} 空间单图放大", "en": "Focus {s}-space panel"},
    "space_kq_tip": {"zh": "k + Re χ(q) 组合单图", "en": "Combined k + Re χ(q) panel"},
    "space_wt_tip": {
        "zh": "小波 |W| 全幅热图：跟随左侧列表当前组，参数用 Tools → Wavelet 里保存的（只读，无曲线交互）",
        "en": "Wavelet |W| full-panel heatmap: follows the current group in the list, with the settings saved in Tools → Wavelet (read-only, no curve interaction)",
    },
    "view_reset": {"zh": "已恢复初始视图", "en": "View reset to full range"},
    "view_range": {"zh": "窗区", "en": "Range"},
    "toggle_left_panel": {"zh": "显示/隐藏左侧列表", "en": "Show/hide the group list"},
    "toggle_right_panel": {"zh": "显示/隐藏右侧参数页", "en": "Show/hide the parameter pages"},
    "open_replace": {"zh": "打开（替换当前列表）", "en": "Open (replace list)"},
    "open_append": {"zh": "追加导入（保留当前列表）", "en": "Import (append to list)"},
    "imp_title": {"zh": "导入原始数据选项", "en": "Raw data import options"},
    "imp_col_energy": {"zh": "能量列", "en": "Energy column"},
    "imp_mode": {"zh": "μ 怎么来", "en": "How to get μ"},
    # Short combo labels (≤ ~12 CJK / ~20 EN); long formula lives in tooltips
    "imp_mode_trans": {
        "zh": "透射（I0 + It）",
        "en": "Transmission (I0+It)",
    },
    "imp_mode_fluo": {
        "zh": "荧光（I0 + Ifl）",
        "en": "Fluorescence (I0+Ifl)",
    },
    "imp_mode_both": {"zh": "透射 + 荧光", "en": "Transmission + Fluorescence"},
    "imp_mode_both_tip": {
        "zh": "一个文件出两条曲线：<名>_trans = ln(I0/It)，<名>_fluo = Ifl/I0。",
        "en": "Each file gives two curves: <name>_trans = ln(I0/It) and <name>_fluo = Ifl/I0.",
    },
    "imp_mode_mu": {
        "zh": "直接用 μ 列（台式机）",
        "en": "Use μ column as-is (benchtop)",
    },
    "imp_mode_trans_tip": {
        "zh": "透射测量：从入射光 I0 与透射光 It 计算 μ = ln(I0/It)。需勾选「自然对数 ln」。",
        "en": "Transmission: compute μ = ln(I0/It) from incident I0 and transmitted It. Usually enable Natural log.",
    },
    "imp_mode_fluo_tip": {
        "zh": "荧光测量：从 I0 与荧光强度 Ifl 计算 μ = Ifl/I0。一般不取 ln。",
        "en": "Fluorescence: compute μ = Ifl/I0 from I0 and fluorescence intensity. Usually no ln.",
    },
    "imp_mode_mu_tip": {
        "zh": "文件里已经有算好的 μ（例如 easyxanes 的 MU_from_* 列）：不再用 I0/It 计算，只挑那一列当 μ。",
        "en": "The file already contains μ (e.g. easyxanes MU_from_*). Do not recompute from I0/It — just pick that column.",
    },
    "imp_col_i0": {"zh": "I0 列", "en": "I0 column"},
    "imp_col_sig": {"zh": "透射列 It", "en": "Transmission column It"},
    "imp_col_mu": {"zh": "μ 列（台式机）", "en": "μ column (benchtop)"},
    "imp_range": {"zh": "能量范围 eV（留空=全部）", "en": "Energy range eV (blank = all)"},
    "imp_auto_batch": {"zh": "导入后自动批量处理", "en": "Auto-process after import"},
    "imp_auto_batch_tip": {
        "zh": "导入完成后按同一套规则自动跑：质量检查 → 选通道 → 逐扫校准 → 合并 → rebin → 自动归一化 → 统一 FT 窗口 → 归一化自检。不会自动导出，可在结果表里审阅，整轮可一步撤销（坏扫描只取消勾选，不删除）",
        "en": "After import, run the same rule set end to end: quality check, channel choice, per-scan calibration, merge, rebin, Auto norm, common FT window, normalization self-check. Nothing is exported; review the result table; the whole run undoes in one step (bad scans are only unmarked, never deleted)",
    },
    "batch_result_title": {"zh": "批处理结果", "en": "Batch result"},
    "batch_result_hint": {
        "zh": "⚠ 行排在最前面。这里只是记录：数据未导出，坏扫描未删除（只取消勾选），整轮可用「撤销」回退。",
        "en": "Warning rows come first. This is a record only: nothing was exported, bad scans were not deleted (just unmarked), and the whole run can be undone.",
    },
    "batch_col_group": {"zh": "数据组", "en": "Dataset"},
    "batch_col_source": {"zh": "来源 / 处理", "en": "Source / action"},
    "batch_col_channel": {"zh": "通道", "en": "Channel"},
    "batch_col_check": {"zh": "自检", "en": "Self-check"},
    "close": {"zh": "关闭", "en": "Close"},
    "cancel": {"zh": "取消", "en": "Cancel"},
    "imp_noask": {
        "zh": "跳过原始数据导入选项（自动导入）",
        "en": "Skip raw import options (auto import)",
    },
    "imp_col_name": {"zh": "列名", "en": "Column"},
    "imp_col_index": {"zh": "列号", "en": "Column number"},
    "imp_col_sample": {"zh": "首行示例", "en": "1st-row sample"},
    "imp_preview": {
        "zh": "列预览（只随窗口横向变宽；列宽固定，多列可左右滑）",
        "en": "Column preview (widens with the window only; fixed column width, scroll if many)",
    },
    "imp_ln": {"zh": "自然对数（ln，仅透射）", "en": "Natural log (ln, transmission only)"},
    "imp_ln_tip": {
        "zh": "透射 μ = ln(I0/It)。只对透射 / 透射+荧光 模式有效；荧光 μ = Ifl/I0 不取对数。",
        "en": "Transmission μ = ln(I0/It). Only used in Transmission / Transmission+Fluorescence; fluorescence μ = Ifl/I0 is never logged.",
    },
    "imp_ref": {"zh": "同时导入参考通道", "en": "Also import reference channel"},
    "imp_ref_col": {"zh": "参考列 Iref", "en": "Reference column Iref"},
    "imp_fl_col": {"zh": "荧光列 Ifl", "en": "Fluorescence column Ifl"},
    "imp_files": {"zh": "标记要导入的文件", "en": "Mark files to import"},
    "imp_files_count": {
        "zh": "本批 {n} 个",
        "en": "{n} in batch",
    },
    "imp_ok_batch": {
        "zh": "导入标记的 {n} 个",
        "en": "Import {n} marked",
    },
    "imp_none_selected": {
        "zh": "请在右侧至少标记一个要导入的文件（点 ✓ 图标，或按 A）。",
        "en": "Mark at least one file on the right (click the ✓ icon, or press A).",
    },
    "imp_batch_applied": {
        "zh": "批量导入：将导入标记的 {n} 个文件（共用列设置）",
        "en": "Batch import: importing {n} marked file(s) with shared column settings",
    },
    "imp_batch_skip": {
        "zh": "跳过（未标记）：{name}",
        "en": "Skip (not marked): {name}",
    },
    "s_mu": {"zh": "μ(E)", "en": "μ(E)"},
    "s_norm": {"zh": "归一化", "en": "Norm"},
    "s_bkg": {"zh": "背景", "en": "bkg"},
    "s_pre": {"zh": "pre", "en": "pre"},
    "s_post": {"zh": "post", "en": "post"},
    "s_d1": {"zh": "dμ/dE", "en": "dμ/dE"},
    "s_d2": {"zh": "d²μ", "en": "d²μ"},
    "cm_none": {"zh": "标记", "en": "Mark"},
    "cm_e0": {"zh": "设 E0", "en": "Set E0"},
    "pin_auto_disarm": {"zh": "📌 图钉 15 秒未使用，已自动收起", "en": "📌 Pin auto-disarmed after 15 s idle"},
    "cm_pre1": {"zh": "设 pre1", "en": "Set pre1"},
    "cm_pre2": {"zh": "设 pre2", "en": "Set pre2"},
    "cm_norm1": {"zh": "设 norm1", "en": "Set norm1"},
    "cm_norm2": {"zh": "设 norm2", "en": "Set norm2"},
    "cm_deglitch": {"zh": "去毛刺", "en": "Deglitch"},
    "pin_e0_tip": {"zh": "图钉：点它，再到 Energy 图上【双击】设 E0", "en": "Pin: click it, then double-click on the Energy plot to set E0"},
    "pin_pre1_tip": {"zh": "图钉：点亮后显示 pre 窗口（区间+边界+基线，边界可拖），图上单击设 pre1；再点图钉隐藏", "en": "Pin: shows the pre window (band + edges + baseline; edges draggable); click the plot to set pre1; click the pin again to hide"},
    "pin_pre2_tip": {"zh": "图钉：点亮后显示 pre 窗口（区间+边界+基线，边界可拖），图上单击设 pre2；再点图钉隐藏", "en": "Pin: shows the pre window (band + edges + baseline; edges draggable); click the plot to set pre2; click the pin again to hide"},
    "pin_norm1_tip": {"zh": "图钉：点亮后显示 norm 窗口（区间+边界+基线，边界可拖），图上单击设 norm1；再点图钉隐藏", "en": "Pin: shows the norm window (band + edges + baseline; edges draggable); click the plot to set norm1; click the pin again to hide"},
    "pin_norm2_tip": {"zh": "图钉：点亮后显示 norm 窗口（区间+边界+基线，边界可拖），图上单击设 norm2；再点图钉隐藏", "en": "Pin: shows the norm window (band + edges + baseline; edges draggable); click the plot to set norm2; click the pin again to hide"},
    # ---- mode tabs ----
    "tab_norm": {"zh": "归一化", "en": "Norm"},
    "tab_norm_tip": {
        "zh": "归一化 / 背景 / FT / 能量矫正",
        "en": "Normalization / background / FT / energy calibration",
    },
    "tab_fit": {"zh": "拟合", "en": "Fit"},
    "tab_fit_tip": {"zh": "Artemis EXAFS 拟合 (Model A)", "en": "Artemis EXAFS fit (Model A)"},
    "tab_tools": {"zh": "工具", "en": "Tools"},
    "tab_tools_tip": {"zh": "平滑 / 分析", "en": "Smoothing / analysis"},
    "sec_analysis": {"zh": "分析工具", "en": "Analysis"},
    # ---- norm page ----
    "box_norm": {"zh": "归一化 / 边前", "en": "Normalization / pre-edge"},
    "box_bkg": {"zh": "背景 / 样条", "en": "Background / spline"},
    "box_ft": {"zh": "傅里叶变换", "en": "Fourier transform"},
    "box_plot": {"zh": "Energy 图窗口 (相对 E0)", "en": "Plot E window (rel E0)"},
    "box_energy_cal": {"zh": "能量矫正", "en": "Energy calibration"},
    "btn_apply_shift_marked": {
        "zh": "同步 ΔE",
        "en": "Sync ΔE",
    },
    "btn_apply_shift_marked_tip": {
        "zh": "把右侧所选范围的 ΔE 统一设为基准值（校准后为校准组的实际平移；否则手动输入目标值）。设值而非累加——重复点不会叠加，误加的组会被纠正",
        "en": "Set ΔE of the selected scope to the reference value (after Calibrate: the calibrated group's actual shift; otherwise enter a target). Sets, never adds — re-clicking can't double-shift, and mis-shifted groups are repaired",
    },
    "op_ok_calibrate": {
        "zh": "✓ 校准完成  ΔE = {d} eV",
        "en": "✓ Calibrate done  ΔE = {d} eV",
    },
    "op_ok_shift_marked": {
        "zh": "✓ 已同步 {n} 组到 ΔE = {d} eV（其余已是该值）",
        "en": "✓ Synced {n} group(s) to ΔE = {d} eV (others already matched)",
    },
    "op_ok_drag_edge": {
        "zh": "✓ 已用拖动更新 {name}",
        "en": "✓ Updated {name} by drag",
    },
    "op_ok_section_all": {
        "zh": "已应用到全部组：{name}",
        "en": "Applied to all groups: {name}",
    },
    "op_ok_section": {
        "zh": "✓ 已成功（{name}）",
        "en": "✓ Applied OK ({name})",
    },
    "e0": {"zh": "E0", "en": "E0"},
    "std_e": {"zh": "标准 E0", "en": "Std E0"},
    "std_e_tip": {
        "zh": "校准用的标准（参考）边能量。元素/边已知时自动填理论值，可手改；点「校准 E0」后日志给出实际平移 ΔE",
        "en": "Reference edge energy for calibration. Auto-filled from theory when element/edge are known; editable. Calibrate logs the applied ΔE",
    },
    "auto": {"zh": "自动", "en": "auto"},
    "auto_e0_tip": {
        "zh": "自动寻边：勾选后每次处理都重新从导数找 E0（找到的值显示在框内）；取消勾选则固定使用框内值。该选择按文件记住",
        "en": "Auto E0: re-find the edge from the derivative on every process (found value shown in the box); uncheck to pin the boxed value. Remembered per file",
    },
    "ord": {"zh": "阶", "en": "ord"},
    "ord_tip": {
        "zh": "后边多项式阶数（larch：0 常数、1 线性、2 二次、3 三次）。Athena 的 nnorm 是项数 = 此值 + 1；三次在 Athena 中没有对应，保存 .prj 时记为二次",
        "en": "Post-edge polynomial degree (larch: 0 constant, 1 linear, 2 quadratic, 3 cubic). Athena's nnorm counts terms = this + 1; a cubic has no Athena equivalent and is saved to .prj as quadratic",
    },
    "flatten": {"zh": "压平", "en": "Flatten"},
    "pre_range": {"zh": "pre", "en": "Pre"},
    "norm_range": {"zh": "norm", "en": "Norm"},
    "step": {"zh": "步长", "en": "Step"},
    "fix": {"zh": "固定", "en": "fix"},
    "rbkg": {"zh": "Rbkg", "en": "Rbkg"},
    "kw": {"zh": "kw", "en": "kw"},
    "clamp_lo": {"zh": "Clamp 低", "en": "Clamp low"},
    "clamp_hi": {"zh": "Clamp 高", "en": "Clamp high"},
    "win": {"zh": "窗函数", "en": "win fn"},
    "phase": {"zh": "相位校正", "en": "phase correction"},
    "phase_tip": {
        "zh": "用 FEFF 路径相位校正 χ(R)，峰位接近真实键长。需要与当前吸收元素匹配的 feffNNNN.dat（拟合页勾选的第一条路径，或 data/feff_paths）。不是导出 chir_pha。",
        "en": "Uses a FEFF path phase so |χ(R)| peaks near the true distance. Needs a feffNNNN.dat for the current absorber (first used Fit path, or data/feff_paths). This is not the export-only chir_pha array.",
    },
    "phase_applied": {
        "zh": "相位校正已用 {path}",
        "en": "Phase-corrected with {path}",
    },
    "phase_need_feff": {
        "zh": "相位校正需要与当前吸收元素匹配的 feffNNNN.dat（拟合页勾选路径或 data/feff_paths）。本次未校正。",
        "en": "Phase correction needs a feffNNNN.dat for the current absorber (used Fit path or data/feff_paths). Left uncorrected.",
    },
    "rwin": {"zh": "R窗", "en": "Rwin"},
    "pkw": {"zh": "绘图kw", "en": "plot kw"},
    "show_span": {"zh": "窗区", "en": "span"},
    "show_span_k_tip": {
        "zh": "显示/隐藏 k 图上的 kmin–kmax 窗区（橙；显示时可拖边界）",
        "en": "Show/hide the kmin–kmax window on the k plot (orange; edges draggable while shown)",
    },
    "show_span_r_tip": {
        "zh": "显示/隐藏 R 图上的 rmin–rmax 窗区（青绿；显示时可拖边界）",
        "en": "Show/hide the rmin–rmax window on the R plot (teal; edges draggable while shown)",
    },
    "show_span_bkg_tip": {
        "zh": "显示/隐藏 k 图上背景扣减的 k 窗区（紫；显示时可拖边界）",
        "en": "Show/hide the background k-range on the k plot (purple; edges draggable while shown)",
    },
    "show_span_pre_tip": {
        "zh": "显示/隐藏 pre-edge 窗口（蓝色区间+虚线边界+红色基线；显示时可拖边界）",
        "en": "Show/hide the pre-edge window (blue band + dashed edges + red baseline; edges draggable while shown)",
    },
    "show_span_norm_tip": {
        "zh": "显示/隐藏 norm 窗口（绿色区间+虚线边界+红色基线；显示时可拖边界）",
        "en": "Show/hide the norm window (green band + dashed edges + red baseline; edges draggable while shown)",
    },
    "autoscale_e": {"zh": "自动缩放 E", "en": "Auto-scale E"},
    "apply_action": {"zh": "应用", "en": "Apply"},
    "apply_tip": {
        "zh": "把面板参数应用到右侧所选范围（当前 / 勾选 / 全部）并重算",
        "en": "Apply panel settings to the selected scope (Current / Marked / All) and reprocess",
    },
    "box_auto_norm": {"zh": "自动归一化", "en": "Auto norm"},
    "auto_norm": {"zh": "自动归一化", "en": "Auto norm"},
    "norm_ai_check": {"zh": "AI Check", "en": "AI Check"},
    "norm_ai_check_tip": {"zh": "只审阅所选范围的 pre-edge 和 normalization，不包含拟合、GDS 或 FEFF", "en": "Review pre-edge and normalization only for the selected scope; excludes Fit, GDS, and FEFF"},
    "norm_ai_apply": {"zh": "Apply AI Data", "en": "Apply AI Data"},
    "norm_ai_apply_tip": {"zh": "读取独立的归一化 AI 缓存，并按数据组应用经过校验的归一化参数", "en": "Read the separate normalization AI cache and apply validated per-dataset normalization parameters"},
    "norm_ai_no_result": {"zh": "还没读到归一化 AI 结果文件。AI 可能仍在分析——请耐心等待终端写完参数文件后再点一次；若终端已结束却没有结果文件，请重新运行 AI Check。", "en": "No normalization AI result file yet. The AI may still be analyzing — please wait for the terminal to finish writing the parameter file and click again; if the terminal ended with no result, re-run AI Check."},
    "norm_ai_running": {"zh": "归一化 AI 检查已在终端打开 AI 对话：AI 会先自动分析并写出参数 JSON，你可以继续追问；完成后回到这里点 Apply AI Data。", "en": "AI chat opened in Terminal: the AI analyzes first and writes the parameter JSON; you can keep chatting. Click Apply AI Data here when done."},
    "norm_ai_applied": {"zh": "已应用 {n} 个数据组的归一化建议。", "en": "Applied normalization suggestions to {n} dataset(s)."},
    "norm_ai_applied_box": {"zh": "读取成功：已从 AI 参数文件应用 {n} 个数据组的归一化参数。", "en": "Read OK: applied normalization parameters for {n} dataset(s) from the AI file."},
    "fit_ai_applied_box": {"zh": "读取成功：已从 AI 参数文件应用 {sections}。", "en": "Read OK: applied {sections} from the AI file."},
    "auto_norm_tip": {
        "zh": "按每条谱的形状自动寻边、定 pre/post、并定背景 k 与 FT k（R/Rbkg 不改）。右侧选范围再点",
        "en": "Per-curve auto edge, pre/post windows from μ(E) shape, plus background/FT k from χ(k) (R/Rbkg unchanged). Pick the scope, then click",
    },
    "auto_scope_current": {"zh": "当前", "en": "Current"},
    "auto_scope_marked": {"zh": "勾选", "en": "Marked"},
    "auto_scope_all": {"zh": "全部", "en": "All"},
    "auto_scope_combo_tip": {
        "zh": "单选：当前组 / 勾选组 / 全部组。勾选与全部都受左侧元素筛选限制（筛 All 则不限制）",
        "en": "Current / Marked / All. Marked and All obey the left element filter (All = no filter).",
    },
    "auto_norm_done": {
        "zh": "自动归一化完成：成功 {ok}/{n} 组（含自动 k 窗）。面板已载入当前组，可核对后 Save。",
        "en": "Auto normalization done: {ok}/{n} groups OK (k windows included). Panel loaded with current group — review, then Save.",
    },
    "auto_norm_reinit_tip": {
        "zh": "撤销上次自动归一化，恢复涉及组在 auto 前的参数",
        "en": "Undo last auto-normalization; restore pre-auto parameters for touched groups",
    },
    "auto_norm_reinit_none": {
        "zh": "没有可撤销的自动归一化（请先跑 Auto norm）。",
        "en": "Nothing to undo for auto-norm (run Auto norm first).",
    },
    # ---- ONE AI Check (v3.19.5, above the page tabs) ----
    "ai_check": {"zh": "AI Check", "en": "AI Check"},
    "ai_check_tip": {
        "zh": "一次检查：按勾选的类别（数据处理 / 拟合）审阅所选范围。结果只显示，点「应用」才生效",
        "en": "One check: review the selected scope for the ticked categories (data processing / fitting). Nothing is applied until you press Apply",
    },
    "ai_check_cat_process": {"zh": "数据处理", "en": "Data processing"},
    "ai_check_cat_ask": {
        "zh": "这次 AI Check 要看哪些？只发送并只允许应用勾选的类别。",
        "en": "What should this AI Check look at? Only the ticked categories are sent, and only they can be applied.",
    },
    "auto_norm_uniform_ft_ask": {
        "zh": "这次 Auto norm 里同一元素/同一边有 {n} 条曲线，它们的 FT k 范围怎么定？\n\n"
              "• 统一 k 范围：都用同一段（按最短的那条），|χ(R)| 峰高可以直接互相比较。\n"
              "• 各用最佳 k 范围：每条用自己噪声允许的最长一段，单看每条最好，但峰高不能互相比。",
        "en": "This Auto norm run holds {n} curves of one element/edge. How should their FT k range be set?\n\n"
              "• Same k range: one shared range (set by the shortest curve), so |χ(R)| peak heights compare directly.\n"
              "• Best k range each: each curve uses the longest range its own noise allows; best for one curve on its own, but peak heights do not compare.",
    },
    "auto_norm_uniform_ft_yes": {"zh": "统一 k 范围（可互相比较）", "en": "Same k range (comparable)"},
    "auto_norm_uniform_ft_no": {"zh": "各用最佳 k 范围", "en": "Best k range each"},
    "ai_check_cat_process_tip": {
        "zh": "校准 / 归一化 / 背景 / FT / 批处理决定。只勾这一类时，应用不会改动拟合表",
        "en": "Calibration / normalization / background / FT / batch decisions. With only this ticked, Apply never touches the fit tables",
    },
    "ai_check_cat_fit": {"zh": "拟合", "en": "Fit"},
    "ai_check_cat_fit_tip": {
        "zh": "GDS 参数建议 + FEFF 只读建议。只勾这一类时，应用不会改动任何归一化参数",
        "en": "GDS parameter advice + read-only FEFF advice. With only this ticked, Apply never changes normalization parameters",
    },
    "ai_check_apply": {"zh": "应用", "en": "Apply"},
    "ai_check_apply_tip": {
        "zh": "先显示对照表（当前值 → 建议值 → 理由），确认后只写勾选类别的参数",
        "en": "Shows a comparison table first (current to suggested to reason); only the ticked categories are written after you confirm",
    },
    "ai_check_need_category": {
        "zh": "请先勾选至少一个类别（数据处理 / 拟合）。",
        "en": "Tick at least one category first (data processing / fit).",
    },
    "ai_check_no_result": {
        "zh": "还没有可应用的 AI Check 结果。先点 AI Check，等它完成（报告区出现结论）后再点「应用」。",
        "en": "There is no AI Check result to apply yet. Click AI Check, wait until it finishes (the findings appear in the report), then press Apply.",
    },
    "ai_check_applied": {
        "zh": "AI Check 已应用：{n} 组（{sections}）。",
        "en": "AI Check applied: {n} dataset(s) ({sections}).",
    },
    "ai_check_reverted": {
        "zh": "以下组未通过归一化自检，已回退到原参数：",
        "en": "Reverted to the previous settings (normalization self-check failed):",
    },
    "ai_check_compare_title": {"zh": "AI Check 对照表", "en": "AI Check comparison"},
    "ai_check_compare_hint": {
        "zh": "逐行核对：当前值 → 建议值 → 理由。点「写入」才写入；写入后每组都会跑一次归一化自检，不合格的自动回退。",
        "en": "Check each row: current, suggested, reason. Write saves the values; every group then runs the normalization self-check and reverts itself if it fails.",
    },
    "ai_check_col_group": {"zh": "数据组", "en": "Dataset"},
    "ai_check_col_param": {"zh": "参数", "en": "Parameter"},
    "ai_check_col_current": {"zh": "当前", "en": "Current"},
    "ai_check_col_suggested": {"zh": "建议", "en": "Suggested"},
    "ai_check_col_reason": {"zh": "理由", "en": "Reason"},
    "yes": {"zh": "是", "en": "Yes"},
    "no": {"zh": "否", "en": "No"},
    "auto_norm_uniform_ft": {"zh": "FT k 范围", "en": "FT k range"},
    "auto_norm_uniform_ft_tip": {
        "zh": "同一元素/边的多条谱统一成一个 FT k 窗口（取各条最小），这样 |χ(R)| 峰高可以直接比较",
        "en": "Give every spectrum of one element/edge the same FT k window (the smallest of the set) so |chi(R)| peak heights compare directly",
    },
    "undo": {"zh": "撤销", "en": "Undo"},
    "undo_tip": {
        "zh": "撤销本模块上一步操作，恢复组参数到操作前",
        "en": "Undo this module’s last action; restore group parameters to the previous step",
    },
    "undo_smooth_tip": {
        "zh": "撤销 Smooth/Rebin：删除上一步新建的曲线（不改动原始谱）",
        "en": "Undo Smooth/Rebin: delete curves created by the last run (source spectra unchanged)",
    },
    "undo_smooth_done": {
        "zh": "已撤销 Smooth/Rebin：删除 {n} 条新建曲线。",
        "en": "Smooth/Rebin undone: removed {n} new curve(s).",
    },
    "undo_done": {
        "zh": "已撤销（{name}）：恢复 {n} 组。",
        "en": "Undone ({name}): restored {n} group(s).",
    },
    "undo_none": {
        "zh": "本模块没有可撤销的上一步（{name}）。",
        "en": "Nothing to undo for this module ({name}).",
    },
    "box_auto_fit": {"zh": "自动拟合", "en": "Auto fit"},
    "auto_fit": {"zh": "自动拟合", "en": "Auto fit"},
    "auto_fit_tip": {
        "zh": "用当前 GDS / 路径 / 拟合窗自动拟合。右侧单选范围后再点本按钮（路径为空时自动 Model A）",
        "en": "Auto-fit with current GDS / paths / windows. Pick scope, then click (Model A if paths empty)",
    },
    "auto_fit_done": {
        "zh": "自动拟合完成：成功 {ok}/{n} 组。",
        "en": "Auto fit done: {ok}/{n} groups OK.",
    },
    "auto_fit_reinit_tip": {
        "zh": "撤销上次自动拟合：恢复 GDS / 路径 / 拟合窗到 auto 之前",
        "en": "Undo last auto-fit: restore GDS / paths / fit windows to pre-auto state",
    },
    "auto_fit_reinit_none": {
        "zh": "没有可撤销的自动拟合（请先跑 Auto fit）。",
        "en": "Nothing to undo for auto-fit (run Auto fit first).",
    },
    "progress_auto_fit_title": {"zh": "自动拟合", "en": "Auto fit"},
    "progress_auto_fit_working": {
        "zh": "正在自动拟合 {n} 个组…",
        "en": "Auto-fitting {n} group(s)…",
    },
    "progress_auto_fit_group": {
        "zh": "自动拟合 {i}/{n}：{name}",
        "en": "Auto-fit {i}/{n}: {name}",
    },
    "progress_auto_fit_cancelled": {
        "zh": "自动拟合已取消（已完成 {ok}/{n}）。",
        "en": "Auto-fit cancelled ({ok}/{n} done).",
    },
    "auto_reprocess": {"zh": "修改参数后自动重算当前组（关闭则需手动点应用）", "en": "Auto-reprocess the current group on parameter change (off = manual Apply)"},
    "auto_reprocess_tip": {"zh": "勾选后：改参数自动重算当前组；不勾则需手动点「应用到当前组」", "en": "When on, edits reprocess the current group automatically; when off, use Apply to current"},
    # ---- fit page ----
    "fit_mode": {
        "zh": "Artemis 拟合 — GDS 自由模型（可一键预设）",
        "en": "Artemis fit — free GDS model (one-click preset)",
    },
    "path_col_elem": {"zh": "元素", "en": "Elem"},
    "box_paths": {"zh": "FEFF 路径", "en": "FEFF paths"},
    "prepare_feff": {"zh": "准备 FEFF", "en": "Prepare FEFF"},
    "prepare_feff_tip": {
        "zh": "运行 FEFF 生成 Model A 的两条 Pt–Pt 路径（已有则跳过）",
        "en": "Run FEFF for the two Model A Pt–Pt paths (skipped if present)",
    },
    "box_gds": {"zh": "GDS 参数", "en": "GDS parameters"},
    "gds_col_name": {"zh": "名称", "en": "Name"},
    "gds_col_value": {"zh": "值", "en": "Value"},
    "gds_col_kind": {"zh": "类型", "en": "Kind"},
    "gds_col_expr": {"zh": "表达式", "en": "Expr"},
    "path_col_use": {"zh": "用", "en": "Use"},
    "path_col_label": {"zh": "标签", "en": "Label"},
    "path_col_file": {"zh": "feff 文件", "en": "feff file"},
    "path_col_degen": {"zh": "简并", "en": "degen"},
    "btn_model_a": {"zh": "预设", "en": "Preset"},
    "btn_model_a_paths": {"zh": "预设路径", "en": "Preset paths"},
    "btn_model_a_paths_short": {"zh": "预设", "en": "Preset"},
    "btn_model_a_paths_tip": {
        "zh": "填入默认 Model A FEFF 路径",
        "en": "Fill the default Model A FEFF paths",
    },
    "btn_gds_add": {"zh": "添加参数", "en": "Add param"},
    "btn_del_sel": {"zh": "删除选中", "en": "Remove selected"},
    "btn_gds_clear": {"zh": "清空", "en": "Clear"},
    "btn_path_add": {"zh": "添加 feff 文件…", "en": "Add feff file…"},
    "btn_path_add_short": {"zh": "添加", "en": "Add"},
    "btn_path_add_tip": {
        "zh": "添加一个或多个 feffNNNN.dat 文件",
        "en": "Add one or more feffNNNN.dat files",
    },
    "btn_path_remove_short": {"zh": "删除", "en": "Remove"},
    "btn_path_remove_tip": {
        "zh": "删除当前选中的 FEFF 路径",
        "en": "Remove the selected FEFF path",
    },
    "kweights": {"zh": "k 权重", "en": "k-weights"},
    "fit_space": {"zh": "拟合空间", "en": "Fit space"},
    "fit_kweights_fixed": {"zh": "拟合 k 权重：1, 2, 3（固定）", "en": "Fit k-weights: 1, 2, 3 (fixed)"},
    "fit_plot": {"zh": "拟合视图", "en": "Fit view"},
    "fit_plot_kw": {"zh": "显示权重", "en": "Plot weight"},
    "fit_history": {"zh": "历史", "en": "History"},
    "hist_empty": {"zh": "(无历史记录)", "en": "(no history yet)"},
    "btn_hist_restore": {"zh": "恢复", "en": "Restore"},
    "btn_hist_restore_tip": {
        "zh": "把选中历史的 GDS / 路径 / 窗口设置填回表格",
        "en": "Refill GDS / paths / fit windows from the selected snapshot",
    },
    "btn_hist_clear": {"zh": "清空历史", "en": "Clear history"},
    "btn_hist_clear_tip": {"zh": "删除所有保存的拟合历史快照；不会删除导出的报告或数据", "en": "Delete all saved fit-history snapshots; exported reports and data are retained"},
    "hist_clear_title": {"zh": "清空拟合历史", "en": "Clear fit history"},
    "hist_clear_confirm": {"zh": "确定删除所有保存的拟合历史快照吗？此操作不会删除导出的报告或数据。", "en": "Delete all saved fit-history snapshots? Exported reports and data will be retained."},
    "hist_cleared": {"zh": "✓ 已清空 {n} 条拟合历史", "en": "✓ Cleared {n} fit-history snapshots"},
    "no_usable_paths": {
        "zh": "没有可用路径——先点「Model A 路径」或添加 feff 文件。",
        "en": "No usable paths — click Model A paths or add a feff file first.",
    },
    "feff_missing": {
        "zh": "缺少 feff 文件：\n{f}\n点「准备 FEFF」或修正路径。",
        "en": "Missing feff file(s):\n{f}\nClick Prepare FEFF or fix the path.",
    },
    "box_fit_win": {"zh": "拟合窗口 (取自归一化页 FT)", "en": "Fit windows (from Norm FT settings)"},
    "run_fit": {"zh": "运行拟合", "en": "Run fit"},
    "run_validate": {"zh": "demo check", "en": "demo check"},
    "run_validate_tip": {"zh": "载入演示谱并跑一遍 Model A 拟合（演示/自检）", "en": "Load the demo spectrum and run a Model A fit (demo/self-check)"},
    "demo_folder": {"zh": "Demo 文件夹…", "en": "Demo folder…"},
    "demo_folder_tip": {"zh": "选择含 demo .xdi 文件的文件夹；会记住下次使用", "en": "Choose the folder containing the demo .xdi file; remembered for next time"},
    "demo_file_missing": {"zh": "Demo 文件夹内没有 .xdi 文件：{folder}", "en": "No .xdi demo file found in: {folder}"},
    "curve_element": {"zh": "元素过滤", "en": "Element filter"},
    "curve_element_all": {"zh": "All", "en": "All"},
    "curve_element_tip": {"zh": "按吸收元素过滤左侧列表和“已标记”绘图；“全部元素”取消过滤", "en": "Filter the group list and Marked plots by absorber element; All elements clears the filter"},
    "fit_history_tip": {"zh": "选择历史记录会立即预览报告/曲线；“恢复”才会把参数填回表格", "en": "Selecting a snapshot previews its report/curves; Restore reapplies its settings to the tables"},
    "history_curves_saved": {"zh": "已存曲线", "en": "Curves saved"},
    "history_report_only": {"zh": "仅报告", "en": "Report only"},
    "history_report_only_note": {"zh": "仅报告：该旧记录没有保存数据/拟合曲线，无法精确重建历史曲线。", "en": "Report only: this older record did not save data/model curves, so its historical plot cannot be reconstructed exactly."},
    "fit_result_ph": {"zh": "拟合报告在此显示…", "en": "Fit report appears here…"},
    "fit_quality_ph": {"zh": "拟合评价：运行拟合后显示", "en": "Fit quality: shown after a fit"},
    "ai_review_tip": {"zh": "（已并入 AI 辅助）", "en": "(merged into AI Assist)"},
    "ai_review_failed": {"zh": "AI 失败", "en": "AI failed"},
    "ai_review_model_ph": {"zh": "先点「验证连接」，再选模型", "en": "Verify connection, then pick a model"},
    "ai_review_model_tip": {
        "zh": "列表来自服务商的 /models（点「验证连接」后刷新，新模型在前）；也可直接输入模型 ID。推理强度（effort）用该模型自己的默认。",
        "en": "The list comes from the provider's /models (refreshed by Verify connection, newest first); you can also type a model id. Reasoning effort stays at that model's own default.",
    },
    "ai_suggestion_scope_current": {"zh": "当前", "en": "Current"},
    "ai_suggestion_scope_marked": {"zh": "已标记", "en": "Marked"},
    "ai_suggestion_scope_all": {"zh": "全部缓存", "en": "All cached"},
    "ai_suggestion_scope_tip": {"zh": "Current 为当前拟合；Marked 为已标记曲线对应的拟合缓存；All 为全部拟合缓存。只有 Current 的结构化建议可自动写入参数。", "en": "Current uses the completed fit; Marked uses cached fits for marked curves; All compares every cached fit. Only a Current structured suggestion can be auto-applied."},
    "ai_assist_run": {"zh": "AI 辅助", "en": "AI Assist"},
    "ai_assist_run_tip": {
        "zh": "一次完成：拟合诊断报告 + 可应用的 GDS/归一化/FEFF 建议。优先终端 CLI，无 CLI 则用 API。需已有成功拟合。",
        "en": "One shot: fit diagnosis report + applicable GDS/norm/FEFF suggestions. Prefers CLI; else API. Needs a successful fit first.",
    },
    "ai_suggestion_run": {"zh": "AI 辅助", "en": "AI Assist"},
    "ai_suggestion_run_tip": {
        "zh": "一次完成：拟合诊断报告 + 可应用参数建议（原 AI Check + AI Review）",
        "en": "One shot: diagnosis report + applicable params (former AI Check + AI Review)",
    },
    "ai_suggestion_apply": {"zh": "Apply AI Data", "en": "Apply AI Data"},
    "ai_suggestion_gds": {"zh": "GDS", "en": "GDS"},
    "ai_suggestion_norm": {"zh": "归一化", "en": "Normalization"},
    "ai_suggestion_feff": {"zh": "FEFF", "en": "FEFF"},
    "ai_suggestion_feff_tip": {
        "zh": "只把 FEFF 建议写到报告区，不会改路径表（禁止自动改 FEFF）。",
        "en": "Shows FEFF advice in the report panel only — it does not change the path table.",
    },
    "ai_assist_apply_tip": {
        "zh": "读取最新 AI 辅助结果：诊断已显示在报告区；勾选段后把通过校验的参数写入表格，然后请再 Run Fit",
        "en": "Read latest AI Assist result: diagnosis is in the report panel; apply validated sections to tables, then Run Fit again",
    },
    "ai_suggestion_apply_tip": {
        "zh": "读取最新 AI 辅助结果并应用勾选的参数段",
        "en": "Read latest AI Assist result and apply ticked parameter sections",
    },
    "ai_assist_running": {
        "zh": "AI 辅助进行中：将输出诊断报告 + 参数 JSON；完成后可 Apply AI Data，再 Run Fit 验证。",
        "en": "AI Assist running: will produce a diagnosis report + parameter JSON; then Apply AI Data and re-run Fit.",
    },
    "ai_suggestion_running": {
        "zh": "AI 辅助进行中：诊断 + 参数建议…",
        "en": "AI Assist running: diagnosis + parameter suggestions…",
    },
    # ---- AI Check channels (v4.4.0): tick-to-activate CLI and API rows ----
    "settings_ai_check_block": {"zh": "AI Check", "en": "AI Check"},
    "ai_check_settings_hint": {
        "zh": "在上面「编码助手接口」和「API 绑定」里，给要让 AI Check 使用的行打勾；没打勾的行不会被用到。"
              "自动 = 先依次试打勾的 CLI，再试打勾的 API；某一个失败（未登录、订阅到期、Key 无效、网络）会自动换下一个。"
              "改完点「验证连接」，每行末尾会标 ✓ / ✗。",
        "en": "Tick the rows AI Check may use under Coding agents and API binding above; unticked rows are never used. "
              "Auto = the ticked CLIs first, then the ticked APIs; when one fails (not signed in, subscription ended, "
              "bad key, network) the next one is tried. Click Verify connection afterwards: each row gets a ✓ / ✗.",
    },
    "ai_check_use": {"zh": "AI Check 使用", "en": "AI Check uses"},
    "ai_check_use_auto": {"zh": "自动（打勾的 CLI 优先，再 API）", "en": "Auto (ticked CLIs first, then APIs)"},
    "ai_check_use_tip": {
        "zh": "自动 = 按下方顺序逐个试，失败就换下一个；也可以只用某一个打勾的通道。",
        "en": "Auto tries the channels below in order and moves on when one fails; or pick one ticked channel only.",
    },
    "ai_order_none": {
        "zh": "还没有打勾任何通道：在上面要用的 CLI 或 API 行首打勾。",
        "en": "No channel is ticked yet: tick the CLI or API rows you want to use above.",
    },
    "ai_order_auto": {"zh": "自动顺序：", "en": "Auto order: "},
    "ai_order_only": {"zh": "只用：", "en": "Only: "},
    "ai_col_active": {"zh": "AI Check", "en": "AI Check"},
    "ai_col_mark": {"zh": "状态", "en": "Status"},
    "ai_col_key": {"zh": "API Key", "en": "API key"},
    "ai_col_model": {"zh": "模型（验证后可选）", "en": "Model (pick after Verify)"},
    "ai_mark_unknown": {"zh": "已打勾，还没验证：点「验证连接」", "en": "Ticked, not verified yet: click Verify connection"},
    "ai_mark_off": {"zh": "未打勾：AI Check 不会用它", "en": "Not ticked: AI Check does not use it"},
    "ai_cli_active_tip": {
        "zh": "打勾 = AI Check 可以用 {name} CLI（全部工具关闭，只能根据证据回答；用你自己的登录，不需要 API Key）。",
        "en": "Ticked = AI Check may use the {name} CLI (every tool off, it can only answer from the evidence; your own sign-in, no API key).",
    },
    "ai_cli_uses": {"zh": "AI Check 用的程序：{path}", "en": "AI Check runs: {path}"},
    "ai_cli_not_found": {
        "zh": "还没找到 {name} 的命令行程序（CLI）；装好后重新打开设置。",
        "en": "No {name} command-line program (CLI) found yet; reopen Settings after installing it.",
    },
    "ai_cli_no_check": {
        "zh": "{name} 的 CLI 不能关掉全部工具，AI Check 不用它（「打开」仍可在终端使用）。要用这家的模型，请在下方 API 绑定打勾它的 API。",
        "en": "The {name} CLI cannot switch every tool off, so AI Check does not use it (Open still works in a terminal). To use this vendor's model, tick its API under API binding.",
    },
    "ai_api_active_tip": {
        "zh": "打勾 = AI Check 可以用 {name} 的 API（需要 Key 和模型；只在你点 AI Check 时发送）。",
        "en": "Ticked = AI Check may use the {name} API (needs a key and a model; sent only when you click AI Check).",
    },
    "ai_channel_no_cli": {"zh": "{label}：已打勾，但没找到 CLI 程序", "en": "{label}: ticked, but the CLI program was not found"},
    "ai_channel_no_key": {"zh": "{label}：已打勾，但没填 API Key", "en": "{label}: ticked, but no API key is saved"},
    "ai_channel_no_model": {
        "zh": "{label}：已打勾，但没选模型（点「验证连接」后在该行选一个）",
        "en": "{label}: ticked, but no model is chosen (click Verify connection, then pick one on that row)",
    },
    "ai_channel_use_missing": {
        "zh": "「AI Check 使用」选的是 {label}，但它现在不可用",
        "en": "\"AI Check uses\" is set to {label}, but it cannot run now",
    },
    "ai_check_no_channel": {
        "zh": "AI Check 还没有可用的 AI 通道。请在 设置 →「编码助手接口」或「API 绑定」里给要用的行打勾，再点「验证连接」。",
        "en": "AI Check has no usable AI channel yet. In Settings, tick the rows to use under Coding agents or API binding, then click Verify connection.",
    },
    "ai_check_open_settings_ask": {"zh": "现在打开设置吗？", "en": "Open Settings now?"},
    "ai_check_busy": {"zh": "AI Check 正在运行，请等它结束或先取消。", "en": "An AI Check is already running; wait for it or cancel it first."},
    "ai_check_started": {
        "zh": "AI Check：正在用 {label}（第 {i}/{n} 个通道）…",
        "en": "AI Check: using {label} (channel {i}/{n})…",
    },
    "ai_check_channel_failed": {
        "zh": "AI Check：{label} 未成功（{detail}）",
        "en": "AI Check: {label} did not succeed ({detail})",
    },
    "ai_check_all_failed": {
        "zh": "AI Check 没有完成：所有打勾的通道都未成功。",
        "en": "AI Check did not finish: none of the ticked channels succeeded.",
    },
    "ai_check_done": {
        "zh": "AI Check 完成（{label}，{secs} 秒）：结论在图下方的报告区；核对后点 AI Check 旁的「应用」。",
        "en": "AI Check done ({label}, {secs} s): the findings are in the report under the plot; review them, then press Apply next to AI Check.",
    },
    "ai_check_working": {"zh": "AI Check 运行中… {secs} 秒", "en": "AI Check running… {secs} s"},
    "ai_check_still": {"zh": "AI Check 仍在运行（已 {secs} 秒）…", "en": "AI Check still running ({secs} s)…"},
    "ai_cli_signin_ask": {
        "zh": "{name} CLI 已安装，但还没有登录（或登录已过期），暂时不能使用。\n现在打开终端运行 {command} 登录吗？按终端和浏览器里的提示完成授权即可。登录完成后会自动继续；在设置里验证时请再点「验证连接」。",
        "en": "The {name} CLI is installed but not signed in (or the sign-in expired), so it cannot be used yet.\nOpen a terminal and run {command} now? Follow the prompts in the terminal and the browser. It continues automatically afterwards; when verifying in Settings, click Verify connection again.",
    },
    "ai_cli_signin_started": {
        "zh": "已打开终端运行 {command}：请完成授权，完成后本窗口会自动继续。",
        "en": "Opened a terminal running {command}: finish the authorization; this window continues automatically.",
    },
    "ai_cli_signin_checking": {
        "zh": "登录窗口已结束，正在确认 {name} 是否登录成功…",
        "en": "The sign-in window closed; confirming that {name} is signed in…",
    },
    "ai_cli_signin_done": {"zh": "{name} 登录完成。", "en": "{name} sign-in finished."},
    "ai_cli_signin_failed": {
        "zh": "登录窗口已结束，但 {name} 还没有登录成功（窗口退出码 {rc}；直接关掉窗口也会这样）。请重试，或在设置里改用其它打勾的通道。",
        "en": "The sign-in window ended but {name} is still not signed in (window exit code {rc}; closing the window does this too). Try again, or tick another channel in Settings.",
    },
    "ai_cli_signin_timeout": {
        "zh": "等待登录超过 15 分钟，已停止等待（终端窗口仍可继续使用）。登录后请重新点 AI Check。",
        "en": "Stopped waiting for the sign-in after 15 minutes (the terminal window stays open). Click AI Check again after signing in.",
    },
    "ai_reason_not_installed": {"zh": "未安装", "en": "not installed"},
    "ai_reason_no_key": {"zh": "没填 Key", "en": "no key"},
    "ai_reason_signin": {"zh": "未登录", "en": "not signed in"},
    "ai_reason_key_rejected": {"zh": "Key 被拒/订阅无效", "en": "key or plan rejected"},
    "ai_reason_network": {"zh": "网络/超时", "en": "network or timeout"},
    "ai_progress_title": {"zh": "AI Check 进行中", "en": "AI Check in progress"},
    "ai_progress_channel": {
        "zh": "正在通过 {label} 分析证据（第 {i}/{n} 个通道；模型不提供百分比，下方为忙碌指示）",
        "en": "Analysing the evidence through {label} (channel {i}/{n}; the model reports no percentage, the bar only shows it is busy)",
    },
    "ai_progress_switch": {
        "zh": "{prev} 未成功（{reason}），已改用 {label}。",
        "en": "{prev} did not succeed ({reason}); now trying {label}.",
    },
    "ai_progress_elapsed": {"zh": "已用 {secs} 秒", "en": "Elapsed: {secs} s"},
    "ai_progress_expect": {
        "zh": "通常 20–90 秒；每个通道最长 5 分钟，超时自动换下一个。请不要关闭 smartLarch。完成后结论显示在图下方的报告区；核对后点 AI Check 旁的「应用」，会先弹出对照表，你确认后才修改参数。",
        "en": "Usually 20-90 s; each channel stops after 5 minutes at most and the next one is tried. Please keep smartLarch open. The findings then appear in the report under the plot; review them and press Apply next to AI Check: a comparison table comes first, nothing changes until you confirm.",
    },
    "ai_progress_expect_many": {
        "zh": "数据组较多，可能需要 1–4 分钟；每个通道最长 5 分钟，超时自动换下一个。请不要关闭 smartLarch。完成后结论显示在图下方的报告区；核对后点 AI Check 旁的「应用」，会先弹出对照表，你确认后才修改参数。",
        "en": "Many datasets: it can take 1-4 minutes; each channel stops after 5 minutes at most and the next one is tried. Please keep smartLarch open. The findings then appear in the report under the plot; review them and press Apply next to AI Check: a comparison table comes first, nothing changes until you confirm.",
    },
    "ai_progress_hide": {"zh": "后台继续", "en": "Keep in background"},
    "ai_progress_cancel": {"zh": "取消", "en": "Cancel"},
    "ai_check_cancelled": {"zh": "AI Check 已取消。", "en": "AI Check cancelled."},
    "ai_check_compare_ok": {"zh": "写入", "en": "Write"},
    "ai_assist_api_prose_only": {
        "zh": "API 返回的不是可应用 JSON，已作为诊断文字显示。要「Apply AI Data」请使用 Coding CLI 通道。",
        "en": "API returned non-JSON prose (shown as diagnosis). For Apply AI Data, use the Coding CLI channel.",
    },
    "ai_suggestion_no_context": {"zh": "所选范围没有可供 AI 审阅的成功拟合或历史快照。请先 Run Fit。", "en": "No successful fit or history snapshot for AI. Run Fit first."},
    "ai_suggestion_need_cli": {"zh": "找不到默认 AI 的命令行客户端。请先安装该 CLI，或在 Settings 的 Coding agents 中设置 CLI 路径。", "en": "Default AI CLI not found. Install it or set its path in Settings → Coding agents."},
    "fit_export_all": {"zh": "导出报告与数据", "en": "Export report + data"},
    "fit_export_all_tip": {
        "zh": "一次导出拟合报告 (.txt) 与 k/R 曲线 CSV，写入 设置 → 数据导出目录（与曲线/Analysis 导出相同）",
        "en": "Export fit report (.txt) and k/R curve CSVs into Settings → data export folder (same as curve/Analysis export)",
    },
    "ai_suggestion_no_result": {"zh": "还没读到 AI 结果文件。AI 可能仍在分析——请耐心等待终端写完参数文件后再点一次；若终端已结束却没有结果文件，请重新运行 AI Check。", "en": "No AI result file yet. The AI may still be analyzing — please wait for the terminal to finish writing the parameter file and click again; if the terminal ended with no result, re-run AI Check."},
    "ai_suggestion_scope_not_applicable": {"zh": "这份建议来自 Selected/All 的比较范围，不会自动覆盖当前参数。请切换到 Current 后重新运行 AI Suggestion，再读取并应用。", "en": "This suggestion came from Selected/All comparison scope, so it will not overwrite the current parameters. Switch to Current, run AI Suggestion again, then Read & Apply."},
    "ai_suggestion_applied": {"zh": "已自动应用经过校验的 AI 建议；请检查表格与窗口后重新 Run Fit。", "en": "Validated AI suggestions were applied. Review the tables/windows, then run Fit again."},
    "ai_suggestion_failed": {"zh": "无法读取 AI Suggestion", "en": "Could not read AI Suggestion"},
    "ai_suggestion_choose_apply": {"zh": "请至少勾选一个要应用的建议段。", "en": "Select at least one suggestion section to apply."},
    "ai_verify": {"zh": "验证连接", "en": "Verify connection"},
    "ai_verify_running": {"zh": "验证中…", "en": "Verifying…"},
    "ai_verify_tip": {"zh": "同时检查所有打勾的行（没打勾的不联系）：CLI 发一次极小的真实请求确认已安装且已登录（消耗可忽略）；API 用已存的 Key 拉取模型列表，填进该行的模型下拉。结果标在每行末尾：✓ 可用 / ✗ 不可用（悬停看原因）。", "en": "Checks every ticked row at once (unticked rows are not contacted): each CLI gets one tiny real request to confirm it is installed and signed in (negligible usage); each API lists its models with the saved key and fills that row's model picker. Each row ends with ✓ usable / ✗ not usable (hover for the reason)."},
    "ai_cli_install": {"zh": "安装 AI CLI…", "en": "Install an AI CLI…"},
    "ai_cli_install_confirm": {"zh": "将打开所选 AI CLI 的官方安装说明网页，不会自动运行下载脚本。继续？", "en": "Open the selected AI CLI's official installation guide? No downloaded script will run automatically."},
    "settings_cache_block": {"zh": "分析缓存", "en": "Analysis cache"},
    "settings_cache_folder": {"zh": "缓存目录（历史 / AI 评审 / AI 参数文件）", "en": "Cache folder (history / AI reviews / AI parameter files)"},
    "settings_cache_folder_hint": {"zh": "AI Check 的提示词与结果参数 JSON、AI 评审记录、拟合历史都保存在这里；可更换到任意文件夹。", "en": "AI Check prompts and result parameter JSON, AI reviews, and fit history all live here; point it anywhere you like."},
    "settings_cache_clear": {"zh": "清理缓存", "en": "Clear cache"},
    "settings_cache_confirm": {"zh": "只会删除 Fit History、AI Review 和 AI Suggestion 生成的缓存文件。继续？", "en": "Only generated Fit History, AI Review, and AI Suggestion cache files will be deleted. Continue?"},
    "settings_cache_cleared": {"zh": "已清理 {n} 个缓存文件。", "en": "Cleared {n} cache files."},
    "settings_eng_export_folder": {"zh": "工程模式导出目录", "en": "Engineering export folder"},
    "settings_eng_export_hint": {
        "zh": "工程模式导出的 layout.export-*.json / layout.current.json 存放位置（项目内文件，非系统缓存）。默认：项目下的 ui\\ 文件夹。",
        "en": "Where engineering mode writes layout.export-*.json / layout.current.json (project files, not system cache). Default: project ui\\ folder.",
    },
    "settings_eng_export_reset": {"zh": "恢复默认", "en": "Reset default"},
    "eng_export_dir_line": {"zh": "存储目录：{path}", "en": "Export folder: {path}"},
    "eng_export_path_tip": {
        "zh": "工程模式 JSON 导出目录（完整路径）。可在 设置 → 工程模式导出目录 修改。",
        "en": "Engineering JSON export folder (full path). Change under Settings → Engineering export folder.",
    },
    "fit_export_report": {"zh": "导出报告", "en": "Export report"},
    "fit_export_report_tip": {"zh": "把拟合报告（统计 + 路径表）保存为文本文件", "en": "Save the fit report (statistics + path table) as a text file"},
    "fit_export_data": {"zh": "导出数据", "en": "Export data"},
    "fit_export_data_tip": {"zh": "导出 k / R 空间的 data·fit·residual CSV", "en": "Export data / fit / residual CSV in k and R space"},
    # ---- tools page ----
    "box_tools_smooth": {"zh": "平滑 / 重采样", "en": "Smooth / Rebin"},
    "op_smooth_mode": {"zh": "平滑 (boxcar)", "en": "Smooth (boxcar)"},
    "op_rebin_mode": {"zh": "重采样", "en": "Rebin"},
    "boxcar_width": {"zh": "Boxcar 宽度 (奇数)", "en": "Boxcar width (odd)"},
    "boxcar_width_short": {"zh": "宽度", "en": "Width"},
    "box_tools_deglitch": {"zh": "去毛刺", "en": "Deglitch"},
    "op_deglitch_auto_mode": {"zh": "自动去毛刺", "en": "Auto deglitch"},
    "op_deglitch_click_mode": {"zh": "点击去毛刺", "en": "Click deglitch"},
    "box_tools_export": {"zh": "导出数据", "en": "Export data"},
    "export_dir": {"zh": "存储目录", "en": "Export folder"},
    "export_folder_lbl": {"zh": "导出目录", "en": "Folder"},
    "export_name_prefix_lbl": {"zh": "前缀", "en": "Prefix"},
    "export_name_suffix_lbl": {"zh": "后缀", "en": "Suffix"},
    "export_name_affix_tip": {
        "zh": "导出文件名：<前缀>export_<日期时间><后缀>（如 test_export_20260820_153000_run1）；Excel 再按元素加 _Fe / _Ni。留空则不加前后缀。",
        "en": "Export file name: <prefix>export_<datetime><suffix> (e.g. test_export_20260820_153000_run1); Excel also appends _Fe / _Ni per element. Empty affixes are omitted.",
    },
    "settings_data_export_folder": {
        "zh": "数据导出目录（曲线 / 拟合 / LCF / PCA / MCR / 边前峰）",
        "en": "Data export folder (curves / fit / LCF / PCA / MCR / pre-edge)",
    },
    "export_format": {"zh": "格式（按 E / k / R / q 分开）", "en": "Format (split by E / k / R / q)"},
    "export_fmt_csv_name": {"zh": "CSV", "en": "CSV"},
    "export_fmt_xlsx_name": {"zh": "Excel", "en": "Excel"},
    "export_fmt_csv": {
        "zh": "每谱每个空间一个文件（如 Name__E.csv / Name__k.csv）",
        "en": "One file per spectrum × space (e.g. Name__E.csv / Name__k.csv)",
    },
    "export_fmt_xlsx": {
        "zh": "按元素各一个工作簿（_Fe.xlsx / _Ni.xlsx）：每个文件里 E / k / R / q 各一表，该元素的谱并排",
        "en": "One workbook per element (_Fe.xlsx / _Ni.xlsx): E / k / R / q sheets, that element's groups side-by-side",
    },
    "export_source": {"zh": "导出对象", "en": "Export"},
    "export_src_marked": {"zh": "勾选的谱", "en": "Marked"},
    "export_src_current": {"zh": "当前谱", "en": "Current"},
    "export_kinds": {
        "zh": "数据种类（μ×3 · Norm×3 · k/R/q×4）",
        "en": "Data kinds (μ×3 · Norm×3 · k/R/q×4)",
    },
    "export_kinds_all": {"zh": "全选种类", "en": "Select all kinds"},
    "export_kinds_none": {"zh": "全不选", "en": "Select none"},
    "export_include_wt": {"zh": "小波数据 (CSV/Excel)", "en": "Wavelet data (CSV/Excel)"},
    "export_include_wt_png": {"zh": "小波热图 (.png)", "en": "Wavelet heatmap (.png)"},
    "export_include_wt_note": {
        "zh": "两项独立勾选，数据格式跟随上方 CSV/Excel 选择：CSV → 每组一个 wt_<组名>.csv（表头 K,R,coef，6 位小数）；Excel → 单个 <批名>_wt.xlsx，按元素分 sheet、同元素各组并排 K/R/coef 三列。参数取自「分析工具 → 小波变换」，没运行过则按各组数据自动取默认值。",
        "en": "Two independent opt-ins; data files follow the CSV/Excel choice above: CSV → one wt_<group>.csv per group (header K,R,coef, 6 decimals); Excel → a single <batch>_wt.xlsx with one sheet per element, each group a side-by-side K/R/coef triple. Parameters come from Tools → Analysis → Wavelet, or auto defaults per group if it was never run.",
    },
    "export_include_wt_tip": {
        "zh": "对同一批组做 χ(k) 连续小波变换，按上方 CSV/Excel 选择写数据文件（每组一个 .csv，或单个按元素分 sheet 的 _wt.xlsx）。",
        "en": "Runs the χ(k) continuous wavelet transform on the same batch and writes the data in the format chosen above (one .csv per group, or a single element-sheeted _wt.xlsx).",
    },
    "export_include_wt_png_tip": {
        "zh": "同时导出与数据对应的热图 .png（方形、jet，与界面所见一致）。只要数据就不要勾这项。",
        "en": "Also writes the matching heatmap .png (square, jet, same as on screen). Leave unticked if you only want the data.",
    },
    "progress_export_wavelet_title": {"zh": "小波变换导出", "en": "Wavelet export"},
    "progress_export_wavelet": {
        "zh": "小波变换 {i}/{n}：{name}",
        "en": "Wavelet {i}/{n}: {name}",
    },
    "export_wt_done": {
        "zh": "小波：已写 {n} 组 {kinds} → {path}",
        "en": "Wavelet: wrote {kinds} for {n} group(s) → {path}",
    },
    "export_wt_skipped": {
        "zh": "小波：跳过 {n} 组（无 χ(k)）：{names}",
        "en": "Wavelet: skipped {n} group(s) without χ(k): {names}",
    },
    "export_wt_fail": {"zh": "小波导出失败：{msg}", "en": "Wavelet export failed: {msg}"},
    # E(μ) row — short
    "export_kind_mu": {"zh": "μ", "en": "μ"},
    "export_kind_dmude": {"zh": "dμ/dE", "en": "dμ/dE"},
    "export_kind_d2mude": {"zh": "d²μ/dE²", "en": "d²μ/dE²"},
    # norE row — ultra-short so 3 fit
    "export_kind_norm": {"zh": "nor", "en": "nor"},
    "export_kind_dnormde": {"zh": "dnor/dE", "en": "dnor/dE"},
    "export_kind_d2normde": {"zh": "d²nor/dE²", "en": "d²nor/dE²"},
    # k
    "export_kind_chi": {"zh": "χ(k)", "en": "χ(k)"},
    "export_kind_kchi": {"zh": "kχ", "en": "kχ"},
    "export_kind_k2chi": {"zh": "k²χ", "en": "k²χ"},
    "export_kind_k3chi": {"zh": "k³χ", "en": "k³χ"},
    # R — same style as q (no leading xR)
    "export_kind_chir_mag": {"zh": "|χ(R)|", "en": "|χ(R)|"},
    "export_kind_chir_re": {"zh": "Reχ(R)", "en": "Reχ(R)"},
    "export_kind_chir_im": {"zh": "Imχ(R)", "en": "Imχ(R)"},
    "export_kind_chir_pha": {"zh": "Phaχ(R)", "en": "Phaχ(R)"},
    # q
    "export_kind_chiq_mag": {"zh": "|χ(q)|", "en": "|χ(q)|"},
    "export_kind_chiq_re": {"zh": "Reχ(q)", "en": "Reχ(q)"},
    "export_kind_chiq_im": {"zh": "Imχ(q)", "en": "Imχ(q)"},
    "export_kind_chiq_pha": {"zh": "Phaχ(q)", "en": "Phaχ(q)"},
    "export_hint": {
        "zh": "先 Apply。默认：nor · k²χ · |χ(R)| · Reχ(q)。Excel：按元素各一个文件，里面 E/k/R/q 各一表；CSV：每谱每空间一文件。",
        "en": "Apply first. Defaults: nor · k²χ · |χ(R)| · Reχ(q). Excel: one file per element with E/k/R/q sheets; CSV: one file per spectrum × space.",
    },
    "export_go": {"zh": "导出所选曲线", "en": "Export selected curves"},
    "progress_export_title": {"zh": "导出", "en": "Export"},
    "progress_export_working": {
        "zh": "准备导出 {n} 条谱…",
        "en": "Preparing export of {n} spectrum(s)…",
    },
    "progress_export_process": {
        "zh": "处理 {i}/{n}：{name}",
        "en": "Processing {i}/{n}: {name}",
    },
    "progress_export_write": {
        "zh": "写入文件… {detail}",
        "en": "Writing… {detail}",
    },
    "progress_export_cancelled": {
        "zh": "导出已取消。",
        "en": "Export cancelled.",
    },
    "export_need_folder": {
        "zh": "请先设定有效的导出目录。",
        "en": "Set a valid export folder first.",
    },
    "export_need_kinds": {
        "zh": "请至少勾选一种数据。",
        "en": "Select at least one data kind.",
    },
    "export_need_groups": {
        "zh": "没有可导出的谱（请勾选组，或选当前谱）。",
        "en": "No spectra to export (mark groups, or choose Current).",
    },
    "export_ok": {
        "zh": "✓ 已导出 {n} 个文件/表 → {path}",
        "en": "✓ Exported {n} file(s)/sheet(s) → {path}",
    },
    "export_fail": {
        "zh": "导出失败：{msg}",
        "en": "Export failed: {msg}",
    },
    "deglitch_hint_auto": {
        "zh": "Apply：对目标谱自动检测并去除毛刺点。",
        "en": "Apply: auto-detect and remove glitches on target spectrum(s).",
    },
    "deglitch_hint_click": {
        "zh": "直接点击目标点附近即可吸附到最近数据点；选中点会标红，Apply 后插值。滚轮缩放保持不变，双击才恢复全图。",
        "en": "Click near a target to snap to the nearest sample; it turns red, then Apply interpolates it. Wheel zoom is preserved; double-click resets.",
    },
    "deglitch_hint_before": {
        "zh": "📌 取针填 E → Apply 截去该点及之前全部（数据直接变短）。",
        "en": "📌 Pin to set E → Apply truncates that energy and all before (data is shortened).",
    },
    "dg_thresh_tip": {
        "zh": "Auto 去毛刺灵敏度：局部鲁棒偏差倍数，越小越灵敏（能抓更小的尖峰），默认 3.0",
        "en": "Auto-deglitch sensitivity: local robust-deviation multiplier; lower catches smaller spikes. Default 3.0",
    },
    "deglitch_no_near_point": {
        "zh": "点击位置附近没有数据点；请稍微靠近目标点再选。",
        "en": "No data sample is near the click; click a little closer to the target point.",
    },
    "deglitch_hint_after": {
        "zh": "📌 取针填 E → Apply 截去该点及之后全部（数据直接变短）。",
        "en": "📌 Pin to set E → Apply truncates that energy and all after (data is shortened).",
    },
    "deglitch_hint_range": {
        "zh": "填/📌 E1 与 E2（可手输）→ Apply 去除区间。图上取针会自动显示读数。",
        "en": "Set/📌 E1 & E2 (or type) → Apply removes the range. Pins auto-fill readings.",
    },
    "deglitch_need_energy": {
        "zh": "请先用 📌 或输入框设定能量 E（或 E1/E2）。",
        "en": "Set energy E (or E1/E2) with pin or spin first.",
    },
    "deglitch_e_picked": {
        "zh": "✓ 已选 E = {e} eV（可再调数值后 Apply）",
        "en": "✓ Picked E = {e} eV (edit if needed, then Apply)",
    },
    "deglitch_e1_picked": {
        "zh": "✓ 已选 E1 = {e} eV",
        "en": "✓ Picked E1 = {e} eV",
    },
    "deglitch_e2_picked": {
        "zh": "✓ 已选 E2 = {e} eV",
        "en": "✓ Picked E2 = {e} eV",
    },
    "dg_style_point": {"zh": "单点毛刺", "en": "Single point"},
    "dg_style_before": {"zh": "该点及之前全部", "en": "This point and all before"},
    "dg_style_after": {"zh": "该点及之后全部", "en": "This point and all after"},
    "dg_style_range": {"zh": "能量区间", "en": "Energy range"},
    "pin_dg_e_tip": {
        "zh": "图钉：在 Energy 图上点一下填入 E",
        "en": "Pin: click on Energy plot to set E",
    },
    "pin_dg_e1_tip": {
        "zh": "图钉：在 Energy 图上点一下填入区间 E1",
        "en": "Pin: click on Energy plot to set range E1",
    },
    "pin_dg_e2_tip": {
        "zh": "图钉：在 Energy 图上点一下填入区间 E2",
        "en": "Pin: click on Energy plot to set range E2",
    },
    "btn_clear_reset": {
        "zh": "全部重置",
        "en": "Reset All",
    },
    "btn_clear_reset_tip": {
        "zh": "恢复导入完成时的数据和参数，删除派生曲线，并清除标记、拟合/LCF/PCA/MCR/边前峰结果叠图与报告，以及全部撤销记录",
        "en": "Restore post-import data and parameters, remove derived curves, and clear marks, fit/LCF/PCA/MCR/pre-edge overlays and reports, and all undo history",
    },
    "op_ok_clear_reset": {
        "zh": "✓ 已全部重置到导入完成状态",
        "en": "✓ Reset all to the post-import state",
    },
    "rebin_auto": {"zh": "自动", "en": "auto"},
    "rebin_auto_tip": {
        "zh": "自动重采样：全部参数用默认值——pre1/exafs2 取第一/最后一个数据点，xanes 步长自动，pre2 −30、pre_step 2、exafs1 15、kstep 0.05、boxcar；八个框变灰。取消勾选后按框里的数（恢复你上次手填的值）。",
        "en": "Automatic rebin: every parameter at its default - pre1 / exafs2 = first / last data point, automatic xanes step, pre2 -30, pre_step 2, exafs1 15, kstep 0.05, boxcar; the eight boxes grey out. Untick to use the numbers in the boxes (your last manual values come back).",
    },
    "rebin_method": {"zh": "方法", "en": "method"},
    "op_ok_section_marked": {
        "zh": "✓ 已成功应用到勾选组（{name}）",
        "en": "✓ Applied OK to marked ({name})",
    },
    "op_align": {"zh": "对齐勾选→基准", "en": "Align marked→ref"},
    "op_align_tip": {
        "zh": "把勾选组的能量轴对齐到左侧所选的基准曲线（互相关找峰位偏移，逐组显示 ΔE）",
        "en": "Align marked groups' energy axes to the reference curve picked on the left (peak shift via cross-correlation; ΔE shown per group)",
    },
    "align_ref_tip": {
        "zh": "对齐基准曲线（默认当前组）",
        "en": "Reference curve for alignment (defaults to current group)",
    },
    "align_hint_label": {
        "zh": "峰位对齐 = 把勾选组对齐到上面选的基准曲线（align marked → selected），逐组给出 ΔE",
        "en": "Align peak = align marked groups onto the reference curve selected above (align marked → selected); per-group ΔE shown",
    },
    "op_calibrate": {"zh": "校准 E0…", "en": "Calibrate E0…"},
    "op_merge": {"zh": "合并勾选组…", "en": "Merge marked groups…"},
    # Smart merge (v2.19.0)
    "merge_title": {"zh": "智能分组合并", "en": "Smart grouped merge"},
    "merge_hint": {
        "zh": "已按 样品名 + 透射/荧光 + 元素/边 自动分组（去掉末尾扫描号、repeat 等）。"
        "每行右侧可改组：下拉选已有组、直接输入新组名，或选「不合并」。只有 ≥2 条的组会合并。",
        "en": "Curves are auto-grouped by sample name + trans/fluo + element/edge (trailing scan numbers, "
        "repeat tags removed). Change a row's group on the right: pick a group, type a new name, "
        "or choose “don't merge”. Only groups with ≥ 2 curves are merged.",
    },
    "merge_col_curve": {"zh": "曲线", "en": "Curve"},
    "merge_col_edge": {"zh": "元素/边", "en": "Edge"},
    "merge_col_group": {"zh": "合并到组", "en": "Merge into"},
    "merge_col_group_tip": {
        "zh": "同名的行合并成一条 <组名>_merged；可直接输入新组名",
        "en": "Rows with the same name merge into <group>_merged; type to create a new group",
    },
    "merge_skip": {"zh": "— 不合并 —", "en": "— don't merge —"},
    "merge_keep": {"zh": "保留原曲线", "en": "Keep originals"},
    "merge_keep_tip": {
        "zh": "勾选：原曲线保留但取消勾选，图上只显示合并结果；不勾：原曲线从列表移除。两种都可用下方「撤销」恢复。",
        "en": "On: originals stay in the list, unmarked, so the plot shows the merged curves. Off: originals are "
        "removed from the list. Either way Undo below restores them.",
    },
    "merge_src_mu": {"zh": "合并 μ(E)", "en": "Merge μ(E)"},
    "merge_src_norm": {"zh": "合并归一化", "en": "Merge norm(E)"},
    "merge_src_tip": {
        "zh": "μ(E)：原始吸收直接平均（推荐，与 Athena 默认一致）；归一化：先各自归一化再平均",
        "en": "μ(E): average raw absorption (recommended, Athena default). norm(E): normalise each, then average",
    },
    "merge_regroup": {"zh": "重新自动分组", "en": "Auto-group again"},
    "merge_regroup_tip": {"zh": "丢弃手动修改，恢复自动分组", "en": "Discard manual edits and restore the automatic grouping"},
    "merge_summary": {
        "zh": "将合并 {groups} 组（共 {curves} 条曲线）；{single} 个单条组不合并。",
        "en": "{groups} group(s) will be merged ({curves} curves); {single} single-curve group(s) stay as they are.",
    },
    "merge_ok": {"zh": "合并 {n} 组", "en": "Merge {n} group(s)"},
    "merge_progress": {"zh": "正在合并 {i}/{n}：{name}", "en": "Merging {i}/{n}: {name}"},
    "merge_one_done": {
        "zh": "合并完成：{name}（{n} 条）{msg}",
        "en": "Merged: {name} ({n} curves) {msg}",
    },
    "merge_all_done": {
        "zh": "智能合并完成：新建 {made} 条合并曲线，原曲线{kept}",
        "en": "Smart merge done: {made} merged curve(s); originals {kept}",
    },
    "merge_kept": {"zh": "已保留（取消勾选）", "en": "kept (unmarked)"},
    "merge_removed": {"zh": "已移除", "en": "removed"},
    "merge_cancelled": {"zh": "已取消合并", "en": "Merge cancelled"},
    "merge_undone": {"zh": "已撤销合并（移除 {n} 条合并曲线，原曲线已恢复）", "en": "Merge undone ({n} merged curve(s) removed, originals restored)"},
    "undo_merge_tip": {
        "zh": "撤销上一次合并：删除合并曲线，恢复原曲线与勾选",
        "en": "Undo the last merge: delete the merged curves, restore originals and their marks",
    },
    "op_lcf": {"zh": "线性组合拟合 (LCF)…", "en": "Linear combination fit (LCF)…"},
    "op_pca": {"zh": "主成分分析 (PCA)…", "en": "Principal component analysis (PCA)…"},
    "op_mcr": {
        "zh": "多元曲线分辨 (MCR-ALS)…",
        "en": "Multivariate curve resolution (MCR-ALS)…",
    },
    "op_peaks": {"zh": "边前峰拟合…", "en": "Pre-edge peak fit…"},
    "op_wavelet": {"zh": "小波变换 (Wavelet)…", "en": "Wavelet transform (WT)…"},
    "op_wavelet_tip": {
        "zh": "χ(k) 连续小波变换（Morlet / Cauchy）：热图 + wt_*.csv 导出（Tools → Export 里跟随 CSV/Excel 选择）。",
        "en": "Continuous wavelet transform of χ(k) (Morlet / Cauchy): heatmap plus wt_*.csv export (follows the CSV/Excel choice under Tools → Export).",
    },
    "lcf_hint": {
        "zh": "打开对话框：选择数据谱与标准谱、设定能量窗与约束，点「运行」后可导出。",
        "en": "Opens a dialog: pick data + standards, set energy window and constraints, Run, then Export.",
    },
    "mcr_hint": {
        "zh": "MCR-ALS（pyMCR）：系列谱 → 纯相谱 + 浓度；可写入 Groups 作后续 LCF。",
        "en": "MCR-ALS (pyMCR): series → pure spectra + concentrations; write Groups for LCF.",
    },
    # ---- log / footer ----
    "log": {"zh": "日志", "en": "Log"},
    # ---- engineering mode (§B.23) ----
    "eng_mode": {"zh": "工程模式", "en": "Engineering mode"},
    "eng_mode_tip": {
        "zh": "工程草稿：左/右/日志高=主窗口三列+日志；小控件用选控件+右键菜单；导出给代理，不立刻改界面",
        "en": "ENG draft: Left/Right/Log H = main layout; small controls via Pick + right-click; export for agent",
    },
    "eng_bar_title": {"zh": "工程", "en": "ENG"},
    "eng_main_layout_tip": {
        "zh": "左侧数字 = 主窗口三列布局草稿：左=Groups 栏宽，右=参数栏宽，日志高=底部日志区高度（不是单个小按钮）",
        "en": "Left numbers = main window layout draft: Left=Groups width, Right=param panel width, Log H=bottom log height (not a small button)",
    },
    "eng_left_w": {"zh": "左栏", "en": "Left"},
    "eng_left_w_tip": {
        "zh": "主窗口左侧 Groups 栏目标宽度（像素，草稿）",
        "en": "Target width of main Groups column (px, draft)",
    },
    "eng_right_w": {"zh": "右栏", "en": "Right"},
    "eng_right_w_tip": {
        "zh": "主窗口右侧参数栏目标宽度（像素，草稿）",
        "en": "Target width of main parameter column (px, draft)",
    },
    "eng_log_h": {"zh": "日志高", "en": "Log H"},
    "eng_log_h_tip": {
        "zh": "主窗口底部日志区域目标高度（像素，草稿）",
        "en": "Target height of bottom log band (px, draft)",
    },
    "eng_ctx_width": {"zh": "设目标宽度…（现 {w}）", "en": "Set width… (now {w})"},
    "eng_ctx_height": {"zh": "设目标高度…（现 {h}）", "en": "Set height… (now {h})"},
    "eng_ctx_rename": {"zh": "改文字 / 重命名…", "en": "Rename / edit text…"},
    "eng_ctx_width_title": {"zh": "目标宽度", "en": "Target width"},
    "eng_ctx_height_title": {"zh": "目标高度", "en": "Target height"},
    "eng_ctx_width_label": {"zh": "宽度 (px)", "en": "Width (px)"},
    "eng_ctx_height_label": {"zh": "高度 (px)", "en": "Height (px)"},
    "eng_notes_short": {"zh": "备注", "en": "Notes"},
    "eng_export_short": {"zh": "导出", "en": "Export"},
    "eng_help_short": {"zh": "说明", "en": "Help"},
    "eng_exit_short": {"zh": "退出", "en": "Exit"},
    "eng_exit": {"zh": "退出工程模式", "en": "Exit engineering mode"},
    "eng_on_detail": {
        "zh": "【工程·草稿】左栏/右栏/日志高=主窗口大布局。小控件：选控件→左键点选→右键设宽/高/改字。「预览」可临时看效果，关掉即还原。备注写意图。导出 JSON 给代理。",
        "en": "[ENG draft] Left/Right/Log H = main layout. Controls: Pick → left-click → right-click width/height/rename. Preview applies the draft temporarily; off = restore. Notes + Export for agent.",
    },
    "eng_edit_text_short": {"zh": "改字", "en": "Text"},
    "eng_edit_text_tip": {
        "zh": "选中控件后右键「改文字/重命名」",
        "en": "Right-click selected control → Rename / edit text",
    },
    "eng_edit_text_need_pick": {
        "zh": "请先选中控件，再右键「改文字」",
        "en": "Select a control first, then right-click → Rename",
    },
    "eng_draft_loaded": {
        "zh": "已载入为草稿（未改正式界面）",
        "en": "Loaded into draft (live UI unchanged)",
    },
    "eng_text_drafted": {
        "zh": "文案已记入草稿，导出后由代理改 i18n（界面未立即改字）",
        "en": "Text saved to draft for export/agent (live labels unchanged)",
    },
    "eng_pick": {"zh": "选控件", "en": "Pick"},
    "eng_pick_tip": {
        "zh": "开启后左键点选控件（不触发功能）。选中后右键：设宽/设高/改文字。左/右/日志高=主窗口大布局，不是小按钮。",
        "en": "When on, left-click selects a control. Right-click: width/height/rename. Left/Right/Log H = main window layout only.",
    },
    "eng_pick_hint": {
        "zh": "选中后右键菜单改尺寸/文字",
        "en": "After pick, right-click for size/text",
    },
    "eng_pick_on": {
        "zh": "选控件已开：左键选中 → 右键设宽/高/改字（十字光标）",
        "en": "Pick ON: left-click select → right-click width/height/rename",
    },
    "eng_pick_off": {"zh": "选控件已关，界面恢复正常点击", "en": "Pick OFF — normal clicks restored"},
    "eng_preview": {"zh": "预览", "en": "Preview"},
    "eng_preview_tip": {
        "zh": "把草稿（栏宽/日志高/控件宽高/文字）临时应用到界面看效果；随时关闭即还原，退出工程模式也会还原——正式界面不受影响，草稿仍由导出给代理统一改源码。",
        "en": "Temporarily apply the draft (columns/log/control size/text) to the live window to see the effect; turn off or exit eng mode to restore everything — production UI is untouched, the draft still goes to the agent via export.",
    },
    "eng_preview_on": {
        "zh": "预览开：草稿已临时应用到界面，改数值即时生效；关闭即还原",
        "en": "Preview ON: draft applied live, edits show instantly; turn off to restore",
    },
    "eng_preview_off": {"zh": "预览关：已恢复原始界面", "en": "Preview OFF: original layout restored"},
    "eng_sel_none": {"zh": "（未选中）右键可改选中控件", "en": "(none) right-click selected control"},
    "eng_sel_none_short": {"zh": "", "en": ""},
    "eng_sel_w": {"zh": "宽", "en": "W"},
    "eng_sel_h": {"zh": "高", "en": "H"},
    "eng_sel_clear": {"zh": "取消选中", "en": "Deselect"},
    "eng_sel_clear_short": {"zh": "×", "en": "×"},
    "eng_sel_picked": {
        "zh": "已选 {id} 草稿 {w}×{h} — 右键：设宽/高/改字",
        "en": "Selected {id} draft {w}×{h} — right-click: size/rename",
    },
    "eng_export": {"zh": "导出布局 JSON…", "en": "Export layout JSON…"},
    "eng_notes": {"zh": "编辑备注…", "en": "Edit remarks…"},
    "eng_notes_hint": {
        "zh": "写清拖不动/不好拖的问题：对齐、裁切、该固定哪一列、交互意图等。会随 layout JSON 一起导出，代理优先读这段。",
        "en": "Describe issues that drag alone cannot fix: alignment, clipping, which column to pin, interaction intent. Exported with the layout JSON; agents read this first.",
    },
    "eng_notes_placeholder": {
        "zh": "例：\n• Fit 页 GDS 与 FEFF 表应对齐等高，下面不要大片空白\n• Groups 侧栏再窄 20px，但 Element 下拉不要被裁切\n•「全部重置」按钮文案不要缩写",
        "en": "e.g.\n• Fit page: GDS and FEFF tables same height; no large empty gap below\n• Groups sidebar ~20px narrower, but Element combo must not clip\n• Keep full Reset All label; do not abbreviate",
    },
    "eng_notes_skip": {"zh": "跳过备注并导出", "en": "Skip remarks & export"},
    "eng_notes_saved": {
        "zh": "工程备注已保存（{n} 字），导出时会写入 JSON",
        "en": "Engineering remarks saved ({n} chars); included on export",
    },
    "eng_notes_cleared": {"zh": "工程备注已清空", "en": "Engineering remarks cleared"},
    "eng_exported_with_notes": {
        "zh": "（含用户备注）",
        "en": "(includes user remarks)",
    },
    "eng_exported_no_notes": {
        "zh": "（本次无备注。右键 🔧 → 编辑备注 可补充意图说明）",
        "en": "(no remarks this time. Right-click 🔧 → Edit remarks to add intent)",
    },
    "eng_apply_file": {"zh": "从 JSON 载入草稿…", "en": "Load draft from JSON…"},
    "eng_open_ui_dir": {"zh": "打开 ui 文件夹", "en": "Open ui folder"},
    "eng_help": {"zh": "工程模式说明", "en": "Engineering mode help"},
    "eng_help_body": {
        "zh": "工程模式 = 草稿 + 导出，不立即改正式界面。\n\n工具条左侧「左栏 / 右栏 / 日志高」= 主窗口大布局（Groups | 图 | 参数栏 + 底部日志），不是小按钮。\n\n小控件：\n① 开「选控件」\n② 左键点选\n③ 右键 → 设宽 / 设高 / 改文字\n④ 备注 + 导出 JSON → 代理改源码\n\n「预览」把草稿临时应用到界面看效果，改数值即时更新；关掉预览或退出工程模式即完全还原。\n\n启动：--eng",
        "en": "Engineering mode = draft + export.\n\nToolbar Left/Right/Log H = main window columns + log band, not small buttons.\n\nSmall controls:\n① Pick ON\n② Left-click select\n③ Right-click → width / height / rename\n④ Notes + Export → agent patches source\n\nPreview temporarily applies the draft to the live window; edits update instantly. Turning Preview off or exiting eng mode fully restores it.\n\nCLI: --eng",
    },
    "eng_on": {
        "zh": "工程已开：左/右/日志=大布局；小控件选后右键改宽高/文字；导出给代理",
        "en": "ENG ON: Left/Right/Log=main layout; pick control then right-click; export for agent",
    },
    "eng_off": {"zh": "工程模式已关闭", "en": "Engineering mode OFF"},
    "eng_exported": {
        "zh": "已导出布局：{path}",
        "en": "Layout exported: {path}",
    },
    "eng_applied": {
        "zh": "已从 JSON 预览应用：{path}（尚未改源码）",
        "en": "Preview-applied from JSON: {path} (source not modified)",
    },
    "eng_edit_text": {"zh": "编辑界面文字", "en": "Edit UI text"},
    "eng_text_zh": {"zh": "中文", "en": "Chinese"},
    "eng_text_en": {"zh": "English", "en": "English"},
    "eng_text_no_key": {
        "zh": "（未匹配 i18n 键，仅改当前控件显示）",
        "en": "(no i18n key matched; live widget text only)",
    },
    "log_toggle_tip": {"zh": "展开 / 收起日志", "en": "Show / hide log"},
    # ---- dialogs ----
    "select_group_first": {"zh": "请先选择一个组。", "en": "Select a group first."},
    "no_current_group": {"zh": "没有当前组", "en": "No current group"},
    "mark_groups_first": {"zh": "请先勾选组。", "en": "Mark groups first."},
    "mark_two_groups": {"zh": "请勾选 ≥2 个组", "en": "Mark ≥2 groups"},
    "align_hint": {"zh": "勾选要对齐的其他组；左侧下拉选基准曲线（默认当前组）。", "en": "Mark the groups to align; pick the reference curve in the dropdown (defaults to current group)."},
    "lcf_hint": {"zh": "勾选 ≥1 个标准谱组；当前组 = 数据。", "en": "Mark ≥1 standard groups; current = data."},
    "calibrate_prompt": {"zh": "已知边能量 (eV):", "en": "Known edge energy (eV):"},
    "eshift_prompt": {"zh": "把所选范围的 ΔE 设为 (eV):", "en": "Set ΔE (eV) for the selected scope:"},
    "open": {"zh": "打开", "en": "Open"},
    "open_cancelled": {"zh": "已取消打开", "en": "Open cancelled"},
    "load_failed": {"zh": "加载失败", "en": "Load failed"},
    "load_failed_batch": {
        "zh": "本批次有 {n} 个文件导入失败。所有文件和原因如下：",
        "en": "{n} file(s) failed to import in this batch. All files and reasons are listed below:",
    },
    "progress_load_title": {"zh": "载入中…", "en": "Loading…"},
    "progress_cancel": {"zh": "取消", "en": "Cancel"},
    "progress_cancelled": {"zh": "载入已取消", "en": "Load cancelled"},
    "progress_done": {"zh": "载入完成", "en": "Load complete"},
    "progress_loading_files": {
        "zh": "正在载入 {n} 个文件…",
        "en": "Loading {n} file(s)…",
    },
    "progress_file": {
        "zh": "文件 {i}/{n}：{name}",
        "en": "File {i}/{n}: {name}",
    },
    "progress_opening": {
        "zh": "正在打开 {name}…",
        "en": "Opening {name}…",
    },
    "progress_parsing_prj": {
        "zh": "解析 .prj：{name}（{size} MB）— 大文件可能需数十秒…",
        "en": "Parsing .prj: {name} ({size} MB) — large files may take a while…",
    },
    "progress_parse_attempt": {
        "zh": "解析 {name}（尝试 {attempt}/3）…",
        "en": "Parsing {name} (attempt {attempt}/3)…",
    },
    "progress_group": {
        "zh": "导入谱组 {i}/{n}：{name}",
        "en": "Importing group {i}/{n}: {name}",
    },
    "progress_fallback": {
        "zh": "prj 无多组，回退单谱载入：{name}",
        "en": "No multi-group in prj; fallback single load: {name}",
    },
    "progress_reading_spectrum": {
        "zh": "读取谱文件：{name}",
        "en": "Reading spectrum: {name}",
    },
    "progress_reading_short": {"zh": "正在读取 {name}", "en": "Reading {name}"},
    "progress_reading_pos": {"zh": "正在读取 {name}（第 {i}/{n} 个）", "en": "Reading {name} ({i}/{n})"},
    "progress_processing": {
        "zh": "正在处理曲线 {i}/{n}：{name}",
        "en": "Processing curve {i}/{n}: {name}",
    },
    "progress_drawing": {"zh": "正在绘制 {n} 条曲线", "en": "Drawing {n} curve(s)"},
    "progress_drawing_prep": {"zh": "正在准备显示…", "en": "Preparing the plot…"},
    "progress_busy_elapsed": {
        "zh": "{text} … 已用 {sec} 秒\n（大文件、OneDrive 下载或曲线很多时会久一些，程序没有卡死）",
        "en": "{text} … {sec} s\n(large files, OneDrive downloads or many curves take longer — the app is not frozen)",
    },
    "progress_auto_edge": {
        "zh": "自动寻边：{name}",
        "en": "Auto edge: {name}",
    },
    "progress_auto_norm_title": {
        "zh": "自动归一化",
        "en": "Auto normalization",
    },
    "progress_auto_norm_working": {
        "zh": "正在自动归一化 {n} 个组…",
        "en": "Auto-normalizing {n} group(s)…",
    },
    "progress_auto_norm_group": {
        "zh": "自动归一化 {i}/{n}：{name}",
        "en": "Auto-norm {i}/{n}: {name}",
    },
    "progress_auto_norm_cancelled": {
        "zh": "自动归一化已取消（已处理 {ok}/{n}）。",
        "en": "Auto-normalization cancelled ({ok}/{n} done).",
    },
    "no_groups_loaded": {
        "zh": "没有加载到任何组。\n\n• 用工具栏「快速 prj」下拉\n• 或把 .prj 拖到窗口上\n• 或「打开…」选择 prj\n• 详见日志",
        "en": "No groups loaded.\n\n• Use the Quick .prj dropdown in the toolbar\n• Or drag-drop the .prj onto this window\n• Or Open… and pick a .prj\n• See Log for details",
    },
    "fit_need_process": {"zh": "请先在归一化页处理谱。", "en": "Process spectrum on Norm tab first."},
}


class I18n:
    def __init__(self, lang: str = "zh"):
        self.lang = "zh" if lang.startswith("zh") else "en"

    def t(self, key: str, **fmt) -> str:
        entry = STRINGS.get(key, {})
        s = entry.get(self.lang) or entry.get("en") or key
        return s.format(**fmt) if fmt else s

    def toggle(self) -> str:
        self.lang = "en" if self.lang == "zh" else "zh"
        return self.lang
