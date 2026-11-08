# Auto-Snap

《漫威终极逆转》（国服 Marvel Snap：Steam 渠道 appid 2955370 / 官网网易账号版）竞技场模式全自动对局：
只读读取游戏内存感知局面 → 大模型逐回合决策（规则 AI 兜底）→ 模拟鼠标出牌 → 自动结算、开下一局。

> ⚠️ 自动化对局可能违反游戏用户协议，存在封号风险，请使用小号。
> 本项目对游戏进程**只读**（`ReadProcessMemory`，不写内存、不注入、不改包），操作只通过截图识别与模拟鼠标。
> 国服 PC 端带网易反作弊组件，外部读内存本身也有被判定的风险。

## 当前状态（2026-11-07）

- **全流程无人值守**：启动先点 PLAY → 匹配 → 每回合感知 / 决策 / 激活 / 移动 / 出牌 / Snap / 撤退 / 结束回合 → Collect Rewards → Next → 下一局
- **客户端**：国服官网版（`cn_official`，当前默认）与国服 Steam 渠道均已实测；国际服预留
- **战绩**（`python tools/analyze_games.py`）：共 30 局 22 胜 8 负，方块净得 +39；其中官网版 20 局 13 胜 7 负，+15
- **决策**：大模型（gpt-6.1-sol，low）逐回合；截止 T1–T4 25s、T5 起 35s，再按回合计时（剩余 − 7s）收紧，来不及则规则 AI；
  没牌可出时不调用模型
- **策略**：目标期望方块最大——中期 Snap、终局大优势不 Snap、按校准后的胜率计算撤退；关键信息栈 + 卡组 / 信号知识库按需注入
- 资料库：748 张卡牌、221 个场地；卡组知识库（我方 loki、对手原型 3 个）；放牌顺序信号库
- **外部面板**（M1–M3）：`python main.py ui` 在浏览器里启停机器人、实时看局面 / 大模型决策 / 战报；战绩总览与逐回合复盘；知识库复核与编辑；支持回放历史对局演示
- 测试：120 个（`python -m pytest`）

## 快速开始

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # 填写 OPENAI_API_KEY / OPENAI_BASE_URL（可选 DEEPSEEK_*）
python tools/update_card_db.py  # 抓取最新卡牌 / 场地资料
python -m pytest
```

运行（游戏需已登录并停在主界面；窗口保持在前台，**按住 F8 随时中止**）：

```bash
python main.py auto --live --max-games 1 -v   # 实操打 1 局
python main.py auto -v                        # 演练：只决策不动鼠标
python main.py observe -v                     # 只观察：打印每回合局面
python main.py auto --live --decider rule_based --max-games 1   # 只用规则 AI
```

### 外部面板

```bash
cd ui/web && npm install && npm run build && cd ../..   # 首次：构建前端（需要 Node 18+）
python main.py ui                                       # 打开 http://127.0.0.1:8765
```

- 「出征准备」选客户端、演练 / 实操、大模型 / 规则 AI、局数，开始后实时显示三路战场、手牌、回合倒计时、赌注、
  大模型思考进度（对比截止时间）、本回合行动的执行结果、校准胜率与撤退线、关键信息栈、战报 / 原始日志
- 控制：本局后暂停 / 继续开局 / 停止（等同 F8，8 秒没退出自动强制结束）；**按住 F8 仍然有效**
- 「战绩」：胜率、魔方走势（点圆点进复盘）、输赢分布、Snap / 撤退、决策质量（各回合大模型耗时 vs 截止、兜底率、被拒、浪费能量）、客户端 / 卡组、场地胜负；可按客户端、最近 N 局筛选
- 「复盘」：逐回合重现战场与手牌、大模型行动 / 胜率 / 信息栈 / 理由、场地评估、执行明细，问题回合（兜底、被拒、浪费能量）在时间轴上标出，← → 切换回合；最后是终局
- 「知识库」：待复核队列（未收录场地、没认出的对手卡组、自动识别的放牌信号），可新建 / 归入对手原型、补场地利弊与要点；编辑我方卡组打法、对手原型、放牌信号、卡牌 / 场地人工覆盖（搜索资料库，场地编辑时附大模型历次判断）。
  每次写入前备份到 `logs/kb_backup/`；机器人运行中修改的知识库，下次启动生效
- 「回放演示」不连游戏，把 `logs/games/*.jsonl` 按实时协议重放，用来调试面板
- 结构：`ui/server`（FastAPI，只监听 127.0.0.1）以子进程启动 `main.py auto --events`；机器人用 stdout 输出
  `@@AUTOSNAP {json}` 事件、从 stdin 读控制命令（协议见 `gamelog/live.py`）；前端 `ui/web`（Vite + React + TS），
  开发时 `npm run dev`（5173 端口，接口代理到 8765）；视觉稿 `ui/design/prototype.html`

每局记录写入 `logs/games/<时间>_<对局id>.jsonl`（局面、场上要点、决策、模型原始输出、执行结果），
控制台输出建议重定向到 `logs/run_*.txt` 便于复盘。

## 客户端切换

支持多个客户端，配置在 `config/settings.toml` 的 `[game]` / `[game.clients.*]`（`clients/__init__.py`）：

| key | 客户端 | 进程 / 区分方式 | 状态 |
|---|---|---|---|
| `cn_official` | 国服官网版（网易账号，网易发烧游戏启动器，如 `E:\FeverApps\MarvelSnap`） | `SnapCN.exe`，目录里**没有** `NtUniSdkSteam.dll` | **当前默认**；2026-11-07 实战 2 胜（内存读取、共享界面模板、出牌、结算全流程） |
| `cn_steam` | 国服 Steam 渠道（Steam 账号登录） | `SnapCN.exe`，目录里有 `NtUniSdkSteam.dll` | 10 局实战验证 |
| `global` | 国际服 Steam | `SNAP.exe`，数据目录 `Second Dinner/SNAP` | 预留，未实测 |

- 两个国服版进程同名、共用数据目录 `LocalLow/NetEase/SnapCN`、IL2CPP 类与字段一致（按名字解析，不受构建差异影响）
- 选择优先级：`--client` > 环境变量 `SNAP_CLIENT` > `[game].client`；`auto` = 第一个正在运行的
- 每个客户端可配 `templates_dir`（专用界面模板，缺的按钮回落到 `data/templates/ui`）、`install_dir`（精确指定安装目录）
- 对局记录 `game_start` 带 `client` 字段
- 我方玩家按「手牌 CardDefId 已知张数多的一方」识别（对手手牌读出来是 `"None"`）；`ClientGameInfo` 投票只作兜底——堆里残留的旧对局会把投票带偏（EntityId 每局复用）

```bash
python main.py clients                          # 各客户端是否运行、卡组目录是否存在
python tools/detect_client.py                   # 新装客户端后：查看进程路径、标志文件、匹配的配置档
python main.py observe --client cn_official -v
python tools/mem_explore.py --client cn_steam   # 工具同样支持 --client（mem_explore / calibrate）
```

## 一局的流程

```
主界面识别(PLAY) ─▶ 匹配 ─▶ 每回合：
  感知 perception/memory_source   内存快照 → GameState（手牌、能量、3 地点比分与双方已揭示牌、对手手牌/牌库数、剩余牌库）
  场上要点 brain/board.py          跟踪每张场上牌的打出回合，重建「消息栈」board_notes
  决策 brain/llm/decider.py        大模型（后台线程，到截止时间未返回 → 规则 AI brain/rule_based.py）
  校验 brain/validator.py          能量、空位、同名多张、出牌限制；不合法 → 带错误让模型重试一次 → 仍不合法用规则 AI
  执行 executor/actions.py         按当前手牌定位拖牌，用内存确认落到目标地点；被拒 → 记为禁止并重新决策
  结束回合 ─▶ 等待期间：处理全屏提示（新卡展示）、预热模型连接
─▶ 结算识别(Collect Rewards → Next) ─▶ 下一局
```

## 目录

| 路径 | 内容 |
|---|---|
| `main.py` | 入口：`observe` / `auto`（对局循环、界面流程、提示处理、对局记录） |
| `clients/` | 客户端配置档：进程匹配（同名进程按安装目录标志文件区分）、数据目录、界面模板目录、区域 |
| `perception/memory/` | IL2CPP 运行时按名字解析类/字段（不硬编码偏移）+ 只读内存读取（`snap.py`） |
| `perception/memory_source.py` | 状态来源：可出牌时返回 `GameState`；`TurnGate` 等能量补满、抽牌完成、状态稳定 |
| `perception/deck.py` | 读取当前卡组（推算剩余牌库） |
| `brain/models.py` | 数据契约：`GameState` / `Card` / `Location` / `TurnDecision` |
| `brain/llm/` | 多厂商大模型：`openai_client.py`（OpenAI 兼容协议 / 中转 / DeepSeek）、`anthropic_client.py`、`decider.py`（逐回合决策） |
| `brain/board.py` | 场上要点（消息栈）：每回合重建，过期信息自动删除 |
| `brain/knowledge.py` | 卡牌/场地资料库查询；能力文字 → 标签（跟牌、中路、翻倍…）与出牌限制解析 |
| `brain/location_rules.py` | 出牌限制：场地效果、场上牌（Goose、Ebony Maw）、卡牌自身（Giganto…）+ 国服实测覆盖 |
| `brain/rule_based.py` + `strategy.py` | 规则 AI：整手牌组合搜索 + 可叠加打分规则 |
| `brain/locations.py` | 场地评估（人工知识库 → 资料库 → 中性），未知场地记录 |
| `brain/signals.py` + `data/signals.json` | 放牌顺序信号：场地 / 卡牌让「下一张（打到这里的）牌」受益或受损；未收录的自动识别写入（需复核） |
| `brain/calibration.py` | 胜率校准：历史预估 vs 实际胜负，撤退用校准值 |
| `brain/decks.py` + `data/decks/` | 卡组知识库：`mine.json`（我方卡组打法 + 每张牌要点）、`opponents.json`（对手原型：核心牌、标志牌、思路、应对、样本）；卡牌→原型哈希索引，局中只注入当前手牌要点与已识别原型；赛后自动积累样本，未命中写 `unmatched.jsonl` 待人工整理 |
| `brain/kbfile.py` | 知识库 JSON 安全写入：读最新 → 修改 → 原子替换（机器人赛后积累与面板编辑同时写也不互相覆盖） |
| `brain/validator.py` | 决策合法性校验 |
| `executor/` | 窗口定位（句柄缓存）、比例坐标、平滑拖拽、界面模板识别（`screens.py`）、遮罩检测 |
| `gamelog/recorder.py` | 对局记录 |
| `gamelog/stats.py` | 战绩汇总与单局逐回合复盘数据（面板「战绩」「复盘」页） |
| `gamelog/live.py` | 面板实时事件协议（stdout 事件 / stdin 控制）与局面、决策的精简 payload |
| `ui/` | 外部面板：`server/`（FastAPI + 子进程管理）、`web/`（React 前端）、`replay.py`（回放演示）、`design/`（视觉稿） |
| `data/` | `card_db.json` / `location_db.json`（抓取）、`cards.json` / `locations.json`（人工，优先）、`templates/ui/`（界面模板） |
| `tools/` | 见下方「工具」 |
| `brain/claude_decider.py`、`fsm/` | 早期占位（未使用；决策已统一到 `brain/llm/`，流程在 `main.auto`） |

## 决策

### 大模型（主力）

一次调用完成「场地评估 → 局势与规划 → 本回合出牌」，结构化 JSON 输出（`location_assessment` / `target_locations` / `plan` / `actions` / `reason`）。
提示内容（`decider.build_prompt`）：

- `board_notes`（最先读）：场上最新要点，每回合重建，最多 10 条——
  【本回合有效】上回合打出的 Hawkeye 类「下回合在此出牌 +N」（过了本回合即删除）；
  【持续】双方场上 Ongoing 效果与限制（牌离场即消失）；【对手上回合】对手新打出的牌（只保留一回合）；
  【场地事件】效果里提到的回合还没到（如「第 6 回合开始交换手牌」，倒计时，过期删除）
- 手牌（当前费用/战力 + 能力）、`card_info`（场上双方的牌、我方牌库/弃牌的能力，每张只出现一次）
- 场地：人工知识库（effect / bias / notes）或资料库效果文字；比分、空位、双方已揭示牌
- 回合、能量曲线、对手手牌/牌库数、运行时被拒的出牌组合 `forbidden`

### 规则 AI（兜底）

- 对整手牌做组合搜索（每张牌：不出 / 合法地点），价值 = 费用 ×（0.5 + 规则得分 / 3），避免「多出小牌压过关键大牌」
- 打分规则（`brain/strategy.py`，可扩展）：争夺价值、场地利弊（bias）、卡牌-场地配合（Ongoing 翻倍场地、Medusa 中路、白板牌）、
  **Hawkeye 跟牌**、**Star-Lord/Gamora 触发条件**（对手满位则不放）、**Iron Man 翻倍已有战力**
- 卡牌标签由能力文字自动推导（`knowledge.ability_tags`），新卡无需手工维护

### 回合计时与速度

| 项目 | 设置 | 依据（实测） |
|---|---|---|
| 推理强度 | 有计时 `low`，无计时 `medium` | 中转上 gpt-6.1-sol 不支持 `none` / `minimal` |
| `verbosity` | `low`（仅逐回合决策） | 18 回合重放：中位 7.9→6.6s，p90 14.6→11.6s，质量不变 |
| 截止时间 | T1–T4 **25s**，T5 起 **35s**；读到回合计时则取 min(截止, 剩余 − 7s)，< 5s 直接规则 AI | 回合 33–85s 不等；16s / 25s 时前期、后期都出现过超时被规则 AI 接手 |
| 连接 | httpx 保活 120s + 等待期间预热（`decider.warmup`） | 新建连接 1.4–5.1s（偶尔超过默认 5s 连接超时），复用 0.3–0.7s；T1 15.0→7.0s |
| 迟到结果 | 写日志「大模型迟到返回」 | 供继续调整截止时间 |

模型对比（`python tools/bench_decision.py`，18 个实战回合，结果 `logs/reports/bench_decision_*.json`）：

| 配置 | 中位 | p90 | 合法 | 结论 |
|---|---|---|---|---|
| gpt-6.1-sol low + verbosity low | 6.6s | 11.6s | 18/18 | **当前使用** |
| gpt-6.1-terra low + verbosity low | 5.2s | 7.8s | 17/18 | 更快，样本小，备选 |
| gpt-6-luna low + verbosity low | 5.1s | 10.1s | 16/18 | — |
| DeepSeek flash / v4-pro 不思考 | 1.9 / 3.6s | 2.2 / 4.7s | 10 / 14 of 18 | 费用常算错，不推荐 |
| DeepSeek 思考模式（low） | 28 / 45s | 74 / 88s | — | 赶不上回合计时 |

切换厂商：`config/settings.toml` 的 `[llm] profile = "deepseek"`（或环境变量 `LLM_PROFILE`），配置在 `[llm.profiles.*]`。

## 卡牌 / 场地资料库

- 更新：`python tools/update_card_db.py`（打印新增/变更）→ `data/card_db.json`（748 张）、`data/location_db.json`（221 个）
  - 数据源：Marvel Snap Zone 公开接口（官网 marvelsnap.com / marvelsnap.163.com 没有可抓取的图鉴）；
    其 `carddefid` 与游戏内存的 CardDefId / LocationDefId 一致，直接关联
  - 国际服数据；国服平衡改动以人工维护的 `data/cards.json`、`data/locations.json` 为准（优先级更高，如 Hawkeye 国服 +3）
- 出牌限制自动解析（`knowledge.parse_restrictions` / `parse_self_restriction`）：
  场地（The Big House、Kyln、Vault、Crimson Cosmos…）、场上牌（Goose 双方受限、Ebony Maw 仅控制者）、
  卡牌自身（Giganto 只能左、Sentry 不能右、Crossbones 只能领先处…）；国服实测与文字不符的在 `location_rules._IGNORE_DB_TEXT`
- 解析不了的限制：拖拽被拒 → `PlayRejected` → 记入 `GameState.forbidden` → 重新决策；现场截图存 `logs/evidence/`

## 配置（`config/settings.toml`）

- `[brain] decider = "llm" | "rule_based"`
- `[llm]`：provider、api（responses / chat / auto）、密钥环境变量名；`[llm.tasks.decision]`：模型、effort / effort_timed、
  verbosity、deadline_s / deadline_timed_s / deadline_late_s / late_from_turn
- 密钥只放 `.env`（已 gitignore），参考 `.env.example`

## 工具

| 命令 | 用途 |
|---|---|
| `python tools/update_card_db.py` | 更新卡牌/场地资料库 |
| `python tools/bench_decision.py [--only ...] [--games ...]` | 用历史对局重放对比模型/参数的速度与质量 |
| `python tools/analyze_games.py [--last N]` | 赛后复盘：战绩、场地胜负、未用完能量的回合、被拒出牌 |
| `python tools/llm_check.py` | 大模型连通性与结构化输出测试 |
| `python tools/llm_trial.py` | 离线试验场地分析 / 逐回合对比 |
| `python tools/mem_explore.py --watch 600` | 内存读取探测（对局中实时打印状态变化） |
| `python tools/detect_client.py [--dir 安装目录]` | 检测本机客户端与配置档匹配 |
| `python tools/calibrate.py` | 截游戏窗口并画出所有坐标，核对布局标定 |
| `python tools/probe_state_files.py`、`watch_live.py` | P0 状态文件调研 |

## 已知限制 / 下一步

- **大模型耗时**：后期回合常见 14–25s，偶尔超过截止被规则 AI 接手；提示词已精简，主要耗时在推理，可评估更快的模型或减少输出字段
- **胜率预估**：终局预估偏高（73–80% 实际约 1/3），已用历史校准；样本仍少，需持续积累
- **待实战验证**：激活改为画面确认、画面识别可激活牌（如复制来的激活能力）、移动（New York 类场地）
- **知识库复核**：`data/signals.json` 里 source=auto 的条目、`data/decks/unmatched.jsonl` 候选对手卡组需人工整理
- **临时卡组**：当前用 `loki-tmp`（White Queen 换 Spider-Man，用于验证激活），验证完换回 `loki`
- 新手期指引：`IsRunningTutorial` 或画面遮罩时暂停，等玩家跟随指引点击；不自动点击指引

## 开发记录（要点）

- **P0 状态文件**：`Standalone\States\prod\GameState.json` 只在结算和登录时写入，不能实时感知；
  `CollectionState.json` + `PlayState.json` 可读当前卡组
- **只读内存**：附加时定位类 ~12s + 扫描 Game 实例 ~5s，之后每次读取 1–5ms；对手未揭示牌只取数量，
  未揭示地点不取名字，从不读 `LocationDefIdToReveal`；我方牌库 `CardDefId` 为空（剩余牌库 = 卡组 − 已见牌）；
  结束判断用 `ClientResultMessage` / `_resultCreated`（`Game.Winner` 对局中就有值）
- **执行层**：比例坐标（手牌中心 0.4963、间距 0.0731、y 0.876；地点落点 x 0.37/0.50/0.63, y 0.60；结束回合 (0.907, 0.924)），
  任意窗口尺寸按比例换算；最小化时自动还原；`find_window` 改为句柄缓存 + 只枚举 UnityWndClass（7254ms → 2ms）
- **启动提速**：进程枚举改用 EnumProcesses + QueryFullProcessImageNameW（psutil 本机 7.3s → 20ms）；类定位与 TutorialManager 地址按「进程 ID + 启动时间」缓存到 `logs/.cache/`（14.5s → 0.01s）；新对局 / 回合计时器先扫「热区域」（托管堆约 80MB，0.2s；全堆 6.5GB 约 8s）；启动时先点 PLAY 再预热
- **回合计时**：`CubeUnity.App.Game.TurnTimer._timeRemaining / _startingSeconds`（每回合时长不同，实测 57–80s）；预算 = 剩余 − 7s，< 5s 直接规则 AI
- **场地与可操作项**：要点栈最前面列出本回合可主动做的事——【可激活】场上牌（含场地给的，如 Kvch；最后一回合标「最后机会」）、【可移动·本回合】（场地允许移入，如 New York 第 6 回合，或卡牌 Moveable）；定时场地事件倒计时、过期即删；【对手节奏】近 3 回合能量利用（已揭示牌费用）与摧毁 combo 次数。执行顺序：激活 → 移动 → 出牌
- **放牌顺序信号**（`brain/signals.py` + `data/signals.json`）：场地 / 卡牌让「下一张（打到这里的）牌」受益或受损时，要点栈写明本回合、哪一路、先放什么；代码兜底调整顺序；未收录的按文字自动识别写入知识库（source=auto，需复核）
- **胜率校准**（`brain/calibration.py`）：历史「模型预估胜率 vs 实际胜负」按阶段（终局 / 其他）±10% 窗口校准，撤退用校准值
- **提示词原则**：系统提示只放通用原则（约 1.1k 字）；具体卡牌、场地、卡组、信号一律进知识库，触发场景时按需注入
- **关键信息栈**：上回合打出、效果落在「下一张牌 / 下回合」的我方牌自动进入【本回合有效】（通用正则，非逐卡）；「下一张牌打在这里」类（Shuri）提示出牌顺序，代码兜底把该路战力最高的牌调到第一张
- **对手撤退**：对局结束后 30s 内不重扫（复用终局快照，毫秒级）；决策期间每 0.3s 检查对局是否结束，执行层每步检查（`GameOver`），发现结束立即 Collect Rewards → Next（此前全堆重扫让撤退后的操作拖慢约 75s）
- **界面识别**：模板匹配 `end_turn` / `collect_rewards` / `next` / `play` / `never_seen` / `tap_to_snap`（阈值 0.75）；
  遮罩检测用棋盘区拉普拉斯方差（遮罩 ≈ 2，正常 ≥ 867）
- **大模型演进**：场地分析并入逐回合决策 → 回合计时导致超时（sol medium 13–18s）→ low + 硬截止 + 规则 AI 兜底 →
  资料库关联与出牌限制解析 → 场上要点消息栈 + 规则 AI 组合搜索 → verbosity low、按回合分段截止、连接保活与预热
- 实战发现并修复：Goose 限制导致出牌被拒（→ 资料库限制解析）；同名两张 Sentinel 被误判「重复打出」（→ 校验按张数）；
  T3 未跟牌 Hawkeye、T4 Medusa 未放中路实为规则 AI 兜底所致（→ 规则 AI 卡牌规则 + 组合搜索，模型截止时间放宽）

## 路线图

- [x] P0 调研：实时感知改用只读内存
- [x] P1 执行层：拖牌 + 内存确认、被拒重决策
- [x] P2 感知层：内存读取逐回合与画面核对一致
- [x] P3 无人值守：界面流程、提示处理、连打多局
- [x] P4 大模型决策：资料库、场上要点、规则 AI 兜底、速度优化
- [x] P5 多客户端：国服官网版 / Steam 渠道切换，进程按安装目录标志区分
- [x] P6 赌注策略：Snap（中期、Key 牌前、终局不吓跑）、撤退（期望方块 + 胜率校准）
- [x] P7 对局机制：回合计时预算、激活、移动、Infinaut / Agatha 等出牌限制、对手撤退快速领奖励
- [x] P8 知识体系：关键信息栈、卡组知识库（我方打法 / 对手原型）、放牌顺序信号、提示词只放通用原则
- [ ] P9 稳定与提速：后期决策提速、激活 / 移动实战验证、长时间连打、知识库持续积累与复核
- [ ] P10 外部面板：M1 实时页与启停 ✅ → M2 战绩 / 复盘 ✅ → M3 知识库复核 ✅ → M4 设置 / 工具 / 悬浮小条
