# 竞技场（Arena Mode）实现方案

> 国服特有常驻模式。规则以用户确认为准（2026-11-09），见文末「规则备忘」。
> 目标：一场竞技（run）的期望积分最大；积分按 5 分一档结算奖励，35 封顶自动结束，溢出计入下一个 Infinity Avatar 进度。

## 界面实测（2026-11-09 截图，`tests/fixtures/arena/`，2000×1125 与模板基准同宽）

| 界面 | 要点 |
|---|---|
| 竞技场主页 `landing` | 顶部门票数 / 金块；中下「0/1 Arena Tickets」就是开始按钮（没票时点击弹买票）；右上 Infinity Avatar 进度 |
| 组卡 `draft_round1` | 顶部 `n/12`；三张候选 + 各自 Select；下方 12 个空槽逐步填满；底部 Concede / PLAY / Treasure Chest。无时间限制 |
| 整场进行中 `run_swap*` | 「Card Swap Chances left: N」；Win Cubes x/4 + 当前积分「N Cubes」；Lose Health x/2 + 「N Health」；右上刷新（放弃）；Infinity Avatar 进度；下方 12 张卡组「Hold to select a card」 |
| 换牌操作 | **长按卡组里要换下的牌**（变暗）→ 点候选的 Select；换完出现新的三张候选 |
| 匹配 `matchmaking` | 「ARENA MODE Matchmaking…」，卡组名「竞技卡组」 |
| 对局 `ingame_t1` / `match_end` | 布局与排位相同；**双方头像下的绿色格条是血量**（对手也是竞技场玩家，血量可见） |
| 单局结算 `victory` | VICTORY + 宝箱 + 「▲ 4」魔方；右下 Next |
| 宝箱 `chest_tiers_*` | 档位 −8 / 0 / 5 / 10 / 15 / 20 / 25 / 30 / 35（积分可以是负数） |

两个对策略有用的发现：

1. **下一次换牌的三张候选提前可见**（换牌次数为 0 时也显示，只是 Select 置灰）。换牌决策可以提前规划，
   对局中也知道「赢 4 魔方后能换进什么」。
2. 对手血量画面上可见，但**不纳入决策**（用户决定 2026-11-09：每局对手随机，参考价值低）。

### 宝箱档位奖励（决定积分的价值函数）

| 积分 | 奖励 |
|---|---|
| −8 | 1 优质券、20 奖章、收藏家代币 |
| 0 | 1 优质券、100 奖章、收藏家代币 |
| 5 | 1 优质券、250 奖章、收藏家代币 |
| 10 | 1 优质券、400 奖章、金块、收藏家代币 |
| 15 | 1 优质券、600 奖章、**神秘变体**、金块、收藏家代币 |
| 20 | 1 优质券、800 奖章、神秘变体、金块、收藏家代币 |
| 25 | 1 优质券、1000 奖章、神秘变体、金块、收藏家代币 |
| 30 | 1 优质券、1200 奖章、**高级神秘变体**、金块、收藏家代币 |
| 35 | 2 优质券、1500 奖章、高级神秘变体、**新头像**、金块、收藏家代币 |

奖励是阶梯式的，15、30、35 是大台阶。所以积分的价值不是严格线性：快到台阶时（比如 13 分），多赢 2 分的价值远大于平时。
**用户决定：以宝箱档位为主**（2026-11-09）。价值函数 `U(积分)` = 当前所在档位的价值（阶梯）+ 很小的头像进度线性项（只用来打破平局）。
默认档位价值（放进 `config/settings.toml` 的 `[arena.tier_value]`，可调）：

| 档位 | −8 | 0 | 5 | 10 | 15 | 20 | 25 | 30 | 35 |
|---|---|---|---|---|---|---|---|---|---|
| 价值 | 0 | 1 | 2 | 3.5 | 6 | 7 | 8 | 11 | 14 |

头像线性项每分 0.05。低于 −8 按 0 计。

## 总体思路

现有排位流水线（内存感知 → 大模型逐回合决策 → 规则 AI 兜底 → 鼠标执行 → 结算）**整体复用**。竞技场只新增四块：

1. **场外流程**：进入竞技场、门票 / 买票、12 轮组卡、换牌、开局、结算后回到竞技场界面循环。
2. **组卡与换牌决策**：三选一打分（好卡先拿 > 低费曲线 > 配合）。
3. **对局内的竞技场调整**：卡组完全已知；Snap / 撤退改为按「血量 + 积分」的整场期望计算。
4. **整场状态、记录与面板**：血量、积分、换牌进度、卡组，跨局、跨重启。

## M0 调研：竞技场状态从哪读（先做，决定后面的感知方式）

- 用 `tools/mem_explore.py` 在竞技场各界面找相关类（按名字搜 `Arena` / `Draft` / `Health` 等）：
  当前轮次、三张候选 CardDefId、已选卡组、血量、积分、换牌进度、门票数。
- 再看 `Standalone/States/prod/*.json` 在组卡 / 结算后有没有写竞技场状态（排位卡组就是从 `PlayState.json` + `CollectionState.json` 读的）。
- **读得到**：感知全走内存 / 状态文件，与现有架构一致，识别最稳。
- **读不到**：画面方案兜底——候选卡按卡面费用 / 战力数字 + 卡图模板匹配到国服卡牌库（客户端是英文，可加卡名文字匹配）；
  血量 / 积分区域截图识别数字。
- 产出：`perception/arena.py`（`ArenaState`：round、candidates、deck、hp、score、swap_progress、tickets、phase）。

### M0 结论（2026-11-09 实测，官网版 48.18）

**竞技场状态全部可从内存只读获得，不需要画面识别。**

- 入口：`Netease.Code.App.GameMode.Arena.ArenaModeManager` 单例（类定位后扫堆约 4s）。
  - 当前状态在 `_cacheState`（`NetEaseArenaModeState`）。注意：继承来的 `_state` 字段按泛型定义解析偏移不对，不要用。
  - 管理器自带配置：`GoldCost=150`、`PickedCardMaxCount=12`、`MaxHealthPoints=10`、`RewardChangeHealthPoints=2`、
    `MaxWinStakesCount=35`、`RewardChangeStakesCount=4`（规则参数直接读，不写死）。
- `NetEaseArenaModeState`：`TicketsCount`（门票）、`AccumulatedStakesCount`（头像进度，实测 8）、`DataVersion`（每次服务器更新 +1）、`Data` →
- `ArenaModeData`：

| 字段 | 含义 | 实测（第 1 局赢 4 后换过 1 次牌） |
|---|---|---|
| `Finished` | 本场是否结束 | false |
| `TotalGameCount` | 已打局数 | 1 |
| `WinStakesCount` | 积分 | 4 |
| `HealthPoints` | 血量 | 10 |
| `ChangeCardCount` | 剩余换牌次数 | 0（换前 1） |
| `EverChangedCardCount` | 已换次数 | 1 |
| `LastRewardChangeCardFromHealthPoints / FromWinStakes` | 上次发放换牌时的血量 / 积分（用来推换牌进度） | 10 / 4 |
| `PickableCards` | 三张候选（组卡时是本轮三选一，之后是下次换牌候选） | Leader, Magneto, Shocker |
| `PickedCards` | 已选卡组（组卡中逐步增加到 12） | Kingpin, MariaHill, Scorpion, … Ultron |
| `AppearedCards` | 本场出现过的卡和次数（`ArenaAppearedCard`） | 35 张 |
| `ArenaDeckId` | 卡池定义 | ArenaDeckS28 |

  卡片是 `ArenaDealCard.Card.CardDefId`，与国服卡牌库 / 对局内存的 CardDefId 一致，直接关联。
- 对局中：`Game.LeagueDefId`（枚举值）== 10052 识别竞技场局；
  （对手血量不纳入决策，不再读取。）
- 状态文件：没有竞技场专用文件；`GameState.json` 只在结算后写入上一局（含 `LeagueDefId`、`FinalCubeValue`、双方 `ArenaModeGameInfo`），可作赛后核对。
- 已验证（实战）：组卡进行中（第 n/12 轮）的读数、血量扣减与超额没收后的数值变化。

## M1 场外流程（导航状态机）

新增界面模板（等用户提供截图）：Game Modes 按钮、Arena Mode 入口、竞技场主页（门票 0/1、宝箱）、买票弹窗、
组卡界面（`n/12` + 三个 Select）、换牌界面（三选一 + 刷新）、整场进行中界面（卡组 + Concede / PLAY / Treasure Chest）、
整场结算界面。

流程：

```
主界面 → Game Modes → Arena Mode
  ├─ 有进行中的整场 → 直接进入（断线续打：以游戏里的血量 / 积分 / 卡组为准）
  └─ 没有 → 有门票用门票；没有门票且勾选了「用金块买票」才买（150 金块）；否则停止并提示
组卡 12 轮 → [换牌] → PLAY → 对局（复用现有循环）→ 结算 → 回到竞技场界面
  → 有换牌机会先换牌 → 下一局 …… → 血量归零 / 积分 ≥ 35 → 整场结算 → 按设置开下一场或停止
```

- 入口：`python main.py auto --mode arena`（默认 `ranked` 不变），面板「出征准备」加模式选择。
- 设置 `[arena]`：`buy_ticket_with_gold = false`、`max_runs`、`concede_run = false`（主动结束整场，默认不用）。
- **对局中不能重启游戏 / 退出**（会按撤退扣血）：竞技场模式下，现有异常恢复只在对局外执行；
  对局中出问题只做「等待 + 截图告警」，不重启。

### M1 实现状态（2026-11-09）

- `perception/arena.py`：`ArenaReader` / `ArenaState`（phase none/draft/run、换牌进度、规则参数），管理器地址按进程缓存（首次约 8s，之后 2ms）。
- `executor/screens.py`：`arena_screen()` + 5 个模板（`arena_header` / `arena_play` / `arena_tickets` / `game_modes` / `modes_arena`）。
- `executor/arena_flow.py`：`ArenaFlow.step()`——主界面 → Game Modes → Arena → 门票开场 → 组卡（内存确认卡组 +1）→
  换牌（长按卡组牌 + Select，或刷新放弃；内存确认 DataVersion 变化）→ PLAY；单局结算 Collect Rewards / Next。
  未识别弹窗（买票、整场结算）截图后 `ArenaStop` 停下。
- `brain/arena_draft.py`：组卡 / 换牌**规则占位**（身材 + 低费曲线），M2 替换。
- `main.py auto --game-mode arena [--max-runs N]`；`[arena]` 设置：`buy_ticket_with_gold`、`max_runs`、`swap_threshold`。
- 竞技场对局：卡组取本场 `PickedCards`；不写入对手原型库。对局内 Snap / 撤退暂时沿用排位逻辑（M3 改）。
- 实机演练（不动鼠标）：在整场界面识别为 `arena_board`，读数正确，下一步会点竞技场 PLAY。
- **实战 1 局（2026-11-09，对局 26ab287c）**：竞技场 PLAY → 匹配 → 6 回合 → Collect Rewards / Next → 回到竞技场全流程跑通；
  输 4 魔方，血量 10 → 6，获得 2 次换牌，两次换牌（长按卡组牌 + Select）都换下了目标牌。
  - 发现并修复：`Game.LeagueDefId` 在内存里是**枚举值**，竞技场 = 10052（结算文件里才是字符串 "NetEaseArena"），
    导致这局沿用了排位卡组 loki 推算剩余牌库，且对手卡组被写进 `unmatched.jsonl`（已删除）。现按 `league_id == 10052` 识别，
    竞技场模式下另有兜底：整场进行中开的局一律用竞技卡组。
  - 卡组列表按费用排序，换进来的牌会插到对应费用位置（界面顺序与内存一致）；换牌核对改为按张数。
- 待实机验证：组卡点击（下一场）；买票弹窗、整场结算界面（遇到时截图）。

## M2 组卡与换牌决策

### 知识：组卡评分表（知识库，按需注入）

- 新文件 `data/arena/ratings.json`：每张卡 `rating`（1–10，竞技场单卡强度）、`tags`
  （`standalone` 独立成立 / `needs:<体系>` 需要组件 / `enabler:<体系>` 组件 / `tech` 针对卡）、一句话要点。
- 初值：离线用 `[llm.tasks.review]`（高推理强度）对国服全卡池批量打分，写入 `source = auto`；
  面板「知识库」里加「竞技场评分」页人工复核修改（与现有复核流程一致）。
- 原则（写进组卡提示词，只放通用原则）：好卡先拿；低费曲线
  （1 费 1–3、2 费 3–4、3 费 2–3、4 费 1–2、5 费 1–2、6 费 0–1，不是硬约束）；前几轮看单卡强度，
  后几轮曲线和配合权重上升；需要组件的牌在有组件时才加分；针对卡打折。

### 决策

- 每次三选一：大模型一次调用（输入：三张候选的能力 + 评分表条目、已选卡组与曲线、轮次），输出选择和一句理由；
  规则兜底：`rating + 曲线补缺分（随轮次加权）+ 配合分`。
- 换牌：算出三张候选里最好的一张，和卡组里「边际价值最低」的一张比较（单卡强度 + 曲线 + 配合）；
  提升超过阈值才换，否则刷新放弃。
- 组卡结束后生成「本场卡组要点」（曲线、核心牌、出牌思路），对局中代替排位的 `data/decks/mine.json` 注入。

### M2 实现状态（2026-11-09）

- `data/arena/ratings.json` + `brain/arena_ratings.py`：评分表 489 张（国服卡池 = 国服客户端有、国际服资料为已发布可收集），
  `tools/build_arena_ratings.py` 用 `[llm.tasks.review]`（gpt-6.1-sol high）每批 25 张、并发 4，约 5.5 分钟生成，全部 `source=auto`。
  分布：1–3 分 93 张、4–6 分 213 张、7–8 分 170 张、9–10 分 13 张。人工复核后改 `manual`（重跑不覆盖）。
- `brain/arena_draft.py`：规则打分 = 评分 + 曲线（权重随轮次 0.3 → 1.0）+ 配合（needs/enabler 同体系）；
  换牌枚举「换下 × 换进」，整副卡组得分（评分 + 曲线偏差 + 配合）提升超过 `swap_threshold` 才换。
- `brain/llm/arena_decider.py`：大模型组卡 / 换牌（`[llm.tasks.arena]`，gpt-6.1-sol medium），提示词只放通用原则，
  只注入候选与卡组涉及卡的评分 / 标签 / 要点，规则分作参考；失败或越界用规则兜底。`[arena].draft_decider = "llm" | "rule"`。
- `tools/arena_draft_trial.py`：模拟 12 轮组卡（大模型 vs 规则对照）、`--live-swap` 读当前整场给换牌建议。
  试跑（seed 7）：12 轮中 10 轮两者一致，大模型在后两轮更看重单卡强度（Elsa、Nebula），两副卡组曲线都贴近目标。
- 待做：面板里复核评分表（M5）；组卡完成后的「本场卡组要点」注入对局决策（并入 M3）。

## M3 对局内调整

### 卡组已知

- `source.deck` 改为竞技场卡组（来自 M0 的 `ArenaState`），`deck_remaining` / 抽牌概率照常计算。
- **不写入对手原型库**：竞技场对手是随机组卡，`decks.record_game` 跳过竞技场对局，避免污染 `opponents.json` / `unmatched.jsonl`；
  对局中也不注入对手原型。

### Snap / 撤退：按整场期望（替换 `retreat_check` 在竞技场的计算）

设当前血量 H、积分 S、单局输 c 个魔方的代价：

- `c < H`：扣 c 血，整场继续 → 代价 `λ·c`（λ = 每点血的未来价值，按积分计）。
- `c ≥ H`：血量归零、整场结束；超出部分 `c − H` **全额**从积分没收（实测，见「整场实战」）→ 代价 `λ·H + (c − H)`。

赢 c 个魔方得 c 分（超过 35 的部分计入头像进度）。下面公式里的「分」最终都换成 `U(积分)` 的变化量，
台阶附近（如 13 → 15）自动更敢争。于是：

- **撤退**（当前赌注 s）的代价 `L(s)`；**打下去**（终局赌注 F）期望 `p·F − (1 − p)·L(F)`；后者更差就撤退。
- **Snap 的盈亏平衡点**：赌注从 F 到 2F，多赢 F，多输 `L(2F) − L(F)`。血量充足时 L 增长约等于 λ·F，
  阈值接近排位；血量见底且积分少（没什么可被没收）时阈值降到下限——即「绝境敢 Snap，以小博大」；
  积分多时超额全额没收很疼，阈值反而升高。
- 程序把 `hp`、`score`、`snap_breakeven`（按上式算好的胜率阈值）放进竞技场的提示词上下文，由大模型判断 Snap；
  撤退仍由程序按校准胜率计算。
- λ 初值 1.0（与排位等价），积累整场记录后按「剩余血量 → 最终积分」的实际数据校准。
- 不设「单局可输魔方 ≤ 剩余血量 − 没收线」之类的硬约束（用户指出是自我限制）。

### M3 实现状态（2026-11-09）

- `brain/arena_strategy.py`：整场期望。终局价值 U = 档位价值（`[arena.tier_value]`）+ 头像进度 0.05/分；
  继续打的价值 V(积分, 血量) 用简化整场模型递推（之后每局胜率 `game_win_rate`、赌注分布 1/2/4/8 = 15/35/35/15%），
  输的代价按「扣血 / 血量归零时超出部分全额没收」算（`forfeit()`）。由此得到：
  - 撤退线 `retreat_cutoff`（下限 `retreat_floor` 10%）：血量充足时 ≈ 22–26%（接近排位 25%）；
    2026-11-10 用户调整（对局 e4971723：对手第 5 回合 Snap，末回合明显不利，校准胜率 39.5% > 23% 没撤，输 4）：
    实际撤退线取 max(整场期望线, 固定线)，固定线最后一回合 `retreat_final` 40%、对手已 Snap `retreat_opp_snap` 60%（第 4 回合起，`retreat_opp_snap_from`；
    实战对局 4b495789 对手 T1 Snap、胜率 48% 被 60% 线撤退扣 1 血，用户决定前 3 回合只按整场期望线）；
    对手已 Snap 时提示词要求胜率算上对手本回合的应对（剩余能量 / 手牌、只需赢哪一路、Snap 是有把握的信号）；
    同日再调（用户觉得太保守）：撤退线（正常，最后一回合）30%、对手 Snap 当回合 45%，直接按这两条线判断、不再与整场期望线取较高者；
    三个值在面板「设置 → 竞技场」可改（`retreat_final` / `retreat_opp_snap` / `retreat_opp_snap_from`）；
  - Snap 门槛 `breakeven`（下限 `snap_floor` 25%）：满血 ≈ 45–51%；血量见底且积分少时降到下限，积分多时升高；35 封顶前（30+ 分）更高。
- 对局：`GameState.arena` 带血量 / 积分 / 档位 / `snap_breakeven` 注入提示词（系统提示只加一条通用原则），手牌附评分表要点；
  竞技场局不注入排位卡组 / 对手原型知识。撤退改用整场期望；大模型要 Snap 但胜率低于门槛时程序否决。
- 实战（对局 7a6ebf30）：血量 6 积分 4，T3 胜率 67% > 门槛 48% Snap，赢 4 魔方 → 积分 8；结算后大模型换牌 Kingpin → Elektra，回到竞技场。
  T6 大模型 59.7s 才返回，被规则 AI 接手（后期回合慢是已知问题，与竞技场无关）。
- 对手血量：用户决定不纳入决策（每局对手随机）。调研记录：对局中没有新的 `NetEaseArenaModeGameInfo`
  （内存里的是赛后结果记录 `GameResultAccountItem`），`ArenaModeHealthBar` / `ArenaModeCubeHealthBar` 是换牌面板的进度条。

## M4 整场状态、记录与统计

- `logs/arena/runs.jsonl`：每场一条——每轮三张候选与选择、每次换牌、每局（赌注、输赢、血量 / 积分前后）、结算档位。
- 每局对局记录 `game_start` 增加 `mode = "arena"`、`run_id`，排位战绩与竞技场分开统计。
- **没收观测**：每次「血量不足抵扣」的对局记录输掉的魔方、当时血量、实际扣分（已据此确认是全额没收）。
- 面板「战绩」加竞技场页：场数、平均积分、档位分布、头像进度、按剩余血量的 Snap / 撤退表现。

### M4 实现状态（2026-11-09）

- `gamelog/arena_log.py`：`logs/arena/runs.jsonl` 追加事件 run_start / pick / swap / game / run_end；
  game 记录一局前后的积分 / 血量、`overflow`（输的魔方 − 血量）与实际 `forfeit`（没收规则的观测依据）。
  `ArenaFlow.observe()` 对比前后状态写 game 事件（结果与魔方由主循环 `note_game()` 告知）。
- 对局记录 `game_start` / `index.jsonl` 增加 `mode`（ranked / arena）与竞技场开局背景；排位战绩不再混入竞技场对局。
- `gamelog/arena_stats.py` + `GET /api/arena/stats`：整场列表、平均积分、档位分布、按开局血量分组（对局 / 胜 / Snap / 撤退 / 魔方）、没收观测。
- 回填：M4 之前的整场（6ada88d8，3 局 + 4 次换牌）已补进 runs.jsonl（`backfill: true`）。

### M5 实现状态（2026-11-09）

- 出征准备：玩法（排位 / 竞技场）、打几场竞技、「门票不足时用金块购买」；面板启动传 `--game-mode / --max-runs / --[no-]buy-tickets`。
- 实时：`arena_state` 事件（卡牌带名字 / 费用 / 战力 / 评分、下一档）→ 竞技场面板：血量格、积分与离下一档、换牌进度与可用次数、
  已打局数、下一次换牌候选、本场卡组；组卡 / 换牌 / 整场开始结束进战报。启动时先推一次状态（演练模式也能看到）。
- 战绩：排位 / 竞技场切换；竞技场页显示总览、档位分布、按血量表现、每场列表与竞技场对局（可进复盘）。
- 知识库 → 竞技场评分：搜索 / 标签 / 来源筛选，编辑评分 / 标签 / 要点，保存为 manual（`/api/arena/ratings`）。
- 浏览器验证：出征准备、演练模式下的实时竞技场面板（读到血量 6、积分 8、卡组 12 张）、战绩竞技场页、评分表编辑弹窗。

### 整场实战（2026-11-09，本场 6ada88d8 打完）

- 5 局：胜 4（玩家）/ 负 4 / 胜 4 / 胜 2 / 负 8 → 积分 8（5 档），头像进度 8 → 16。
- **没收规则修正**：实测两次都是超出部分全额没收——剩 6 血输 8 没收 2（9de0c74b）、剩 1 血输 8 没收 7（6961a4a4，用户手动 Snap 测试），
  不是折半。`brain/arena_strategy.forfeit()` = 超出部分。
- **整场结束界面**：「Click to Open Arena Chest」→ 奖励列表「CLAIM!」→ 回到竞技场主页；模板 `arena_chest_open` / `arena_claim`，
  `ArenaFlow` 自动点开宝箱、领取。
- 修复：Aunt May「Quickdraw: Moveable once」没触发时不能移动，`location_rules.self_moveable_text()` 只认句首的 Moveable 关键词。

### 第二场实战反馈修复（2026-11-09，用户观察）

1. **Cap's Shield 不移动**：移动能力来自 Sam Wilson「Ongoing: You can move Cap's Shield.」（其他牌赋予），
   `location_rules.granted_movers()` 解析「You can move <牌名>」，`move_targets` 认这类赋予的移动（Shield 移到 Cap 所在地点 +2）。
2. **移走后的空位没利用**：校验器原先在移动前算空位，「先移走 Shield 再把 Odin 打进满员的左路」被判已满。
   现按执行顺序先校验移动并更新空位；提示词说明移走的格子本回合可以出牌。
3. **夜魔侠第 5 回合观察**：观察期间按钮是「WAITING… 5/6」，放牌会被拒。我方有已揭示的 Daredevil 时，第 5 回合先等 END TURN 出现，
   再重读局面（对手本回合出牌已可见）后决策（`location_rules.daredevil_watch()` + `main.py`）。
4. 后期回合大模型截止 35s → 50s（三次 T6 超时被规则 AI 接手）。
5. **移动收益叠满**（用户：Shield 能移就要反复移）：`board._move_benefit_notes()` 对可移动且移动本身有收益的牌
   （牌面 / 目标场地 / 我方其他场上牌写着「when … moves / moved this turn / moves here」）出【移动收益】要点，
   提示每回合都移、只在移到某地点才触发的交替移开 / 移回；提示词加一条通用原则。

### 第二场竞技（2026-11-09，本场 6a5974d8，花 150 金块买票）

- 买票弹窗（「Do you want to spend 150 金块 to enter?」）实测并做成模板；组卡 12 轮全部由大模型完成并内存确认。
- 9 局：负 2 / 负 2 / 胜 2 / 负 2 / 胜 2 / 负 2 / 撤退 −1 / 胜 8 / 负 8 → 积分 5（5 档），头像进度 16 → 21。最后两局的 Snap 是用户手动点的，用来测超额没收。
- 验证到：Cap's Shield 主动移动（含来回移）、夜魔侠第 5 回合等待观察结束、血量 2 时按整场期望撤退保住整场、绝境（血量 1）继续打。

### 卡组内配合：衍生牌与点名联系（2026-11-09）

第二场大模型换掉 Sam Wilson 时，提示里只有 Sam 自己的文字和评分，看不到 Cap's Shield 做什么。
`brain/arena_links.py` 从资料库能力文字通用解析（按卡名精确匹配，区分大小写、整词；同名优先卡池牌）：

- **衍生牌**（creates）：牌面点名的非卡池牌，如 Sam Wilson → Cap's Shield、Thor → Mjolnir、Korg → Rock；
- **点名联系**（links）：牌面或其衍生牌点名了卡组里的另一张卡池牌（如 Mary Jane → Spider-Man），双向。

接入：组卡 / 换牌提示里每张卡附 `creates`（衍生牌身材与能力）和 `links`，系统提示加一条通用原则
（换下前看它带走的衍生牌、组件、赋予的能力）；规则打分 `synergy` 每个点名联系 +1.0（`SYNERGY_LINK`）；
`tools/build_arena_ratings.py` 生成评分时也附衍生牌（现有评分表未重跑）。

面板：`arena_state` 的每张卡带 `creates` / `links`（候选按「换进后」算联系）。实时竞技场面板的小卡片上有衍生（✦）/ 联系（🔗）
标记，悬停看详情；卡组下方「衍生牌与点名联系」分候选 / 本场卡组两块列出。知识库 → 竞技场评分的列表与编辑弹窗显示衍生牌身材和能力。
浏览器验证：注入测试状态（Sam Wilson / Cannonball / Beta Ray Bill 卡组 + Thor / Spider-Man / Korg 候选）与评分页搜索 Captain。

验证：`tools/arena_draft_trial.py --replay-swaps 6a5974d8` 按记录重放第二场 6 次换牌——前后决策基本一致；
Sam Wilson → Kate Bishop 这次大模型明确提到放弃「护盾组合」（★6.5 换 ★9），属于看清代价后的取舍，不做硬限制。
「your Cap」这类昵称不解析，交给大模型看衍生牌文字判断。

## 当前状态与待办（2026-11-09）

- M0–M5 全部完成并实战验证：进入竞技场 → 买票 / 门票开场 → 大模型组卡 → 对局（整场期望 Snap / 撤退）→ 换牌 → 整场结算开宝箱 → 回到主页。
- 待办：
  1. 后期回合大模型耗时：截止已放宽到 50s，仍需观察；「超时后重试又不合法、耗光回合时间」的问题未处理。
  2. ~~换牌时考虑卡组内配合~~（已做，见下「卡组内配合：衍生牌与点名联系」）。
  3. 评分表 489 张仍是大模型初值（source=auto），需要在面板「知识库 → 竞技场评分」逐步人工复核。
  4. `game_win_rate`（整场模型里的单局胜率）与赌注分布用初值，积累更多竞技场对局后校准。

## 实施顺序与验证

| 阶段 | 内容 | 验证 |
|---|---|---|
| M0 | 内存 / 状态文件调研，`ArenaState` | 在组卡、对局、结算各界面打印读数，与画面核对 |
| M1 | 导航、模板、门票逻辑、断线续打 | 实操走完：进入 → 组卡（先用规则兜底）→ 1 局 → 回到竞技场 |
| M2 | 评分表初值 + 组卡 / 换牌决策 | 打一整场，逐轮复核选择 |
| M3 | 卡组已知、整场期望的 Snap / 撤退 | 单测覆盖 L(c) 与阈值；实战逐回合复核 |
| M4 / M5 | 记录、统计、面板 | 面板回放竞技场对局 |

每个阶段完成后实打并逐回合复盘再进入下一阶段。

## 已确认

- 组卡 / 换牌没有时间限制：可以用较高推理强度，不需要截止时间兜底。
- 「0/1 Arena Tickets」就是开始按钮；没票时点击弹出「花 150 金块进入」确认（模板 `arena_buy_prompt` / `arena_buy_confirm`）。
- 整场结束：开宝箱 → CLAIM → 回到主页（模板 `arena_chest_open` / `arena_claim`）。
- 超额没收是全额（不是折半）；对手血量不纳入决策。

## 规则备忘

- 1 张门票 / 场（无票可花 150 金块）；12 轮三选一组 12 张；初始 10 血。
- 魔方（单局）/ 积分（整场赢的魔方累计）/ 血量 三个概念分开。
- 输 c 魔方扣 c 血；血不够扣时 `(c − 血量)` 全额从积分没收，整场结束；扣血恰好到 0 也结束。撤退同样按当前赌注扣血。
- 换牌：赢 1 魔方 = 1 点、掉 1 血 = 2 点，满 4 点换一次，可混合累计，一局可触发多次；三选一或刷新放弃。
- 每 5 积分一档，35 封顶自动结束，溢出计入下一个头像进度。
- 卡池与收藏无关，约等于国服全卡池（略滞后）。
- 中途关游戏可续；对局中中断按撤退扣血。
