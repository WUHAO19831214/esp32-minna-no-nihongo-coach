# ESP32 「大家的日语」项目协作约束（与 AI Mac mini / Air 状态牌 / 番茄钟共存）

本项目与另一个项目（AI Mac mini，下称 Mini；其 Mac 服务 `com.wuhao.esp-ai-mac-mini`、`com.wuhao.esp-ai-mac-mini-kimi`）
共用同一块 ESP32（ESP-Mosaico，`192.168.31.124`）。设备固件只有 **4 个 Lua 任务槽位**，屏幕同一时刻只能属于一个应用。

## 绝对禁止
1. **禁止停用、卸载、重启其他项目的 Mac 服务**：不得对 `com.wuhao.esp-ai-mac-mini`、`com.wuhao.esp-ai-mac-mini-kimi`
   或任何非本项目 LaunchAgent 执行 `launchctl bootout / kickstart / unload / stop`，也不得 kill 其进程。
   Mac 桥接进程不占用 ESP32 Lua 槽位，**不能**以停 Mac 服务来解决四槽位问题（此前误停曾导致 Mini 掉线）。
   本项目只允许管理自己的 `com.wuhao.esp-minna-nihongo`（见 `host/service.py`）。
2. **禁止修改 Air（`ai_badge`）与番茄钟（`pomodoro_clock`）的文件和配对**；Air receiver 若已运行必须保留。
3. **禁止后台自动抢占屏幕**：桥接守护进程在后台（启动、断线重连、404 恢复、心跳）不得停止其他应用的任务，
   不得无条件启动本项目 UI，不得自动覆盖设备上正在运行的脚本。
4. 未经用户明确要求，不编辑 Codex 目录（`/Users/wuhao/LocalProjects/Codex/...`）里的文件。

## 屏幕与任务归属规则
- 屏幕前台由**用户明确切换**决定。只有用户显式动作（`/api/esp/activate`、`/api/esp/badge`、`/api/esp/deploy`、
  `device.py ensure-start | switch-to-badge | deploy`）才允许激活、交接或部署。
- 后台只做只读检查与「仅本接收器」恢复：设备重启后若 `minna_nihongo_receiver` 缺失，仅当
  (a) 无其他应用 UI 在前台，(b) 空闲槽位 ≥ 1，(c) 任务列表可读，才启动接收器并发布图标（**不启动 UI**）；
  否则记录 `receiver_state=waiting_*`，每 30 秒退避重试，等另一应用不再占用后再恢复。
- 任务识别必须使用**完整设备路径**精确匹配（如 `/nand/apps/minna_nihongo/scripts/main.lua`），
  不得用后缀/子串匹配。只停止经路径核实的本项目任务；跨应用停止仅限用户显式切换，且只停对方 UI `main.lua`，
  不停其 receiver / indicator。
- 停止后必须**再次列出任务确认已释放**才算成功；Mini 路由不可用或停止未确认时返回
  `{ok:false, recoverable:true, state:...}`，**不得谎报 ok**，也不得靠停 Mac 服务兜底。
- 部署（上传脚本）与激活（启动 UI）分开：`deploy_scripts()` 在本项目 UI 运行时拒绝覆盖脚本。
- `bootstrap()` 的设备端提示词禁止停止其他任务；槽位已满时什么都不改，只返回“槽位已满”。

## 改动后的自检（不会触碰真机）
```
python3 tests/coexistence_check.py
```
该 mock 回归覆盖：后台重启不抢屏、404 且 Mini 活跃时不 bootstrap、槽位已满/状态未知时等待、
启动时脚本检查只读、显式激活/切回状态牌的交接顺序与可恢复状态、路径精确匹配、不引用其他项目的 LaunchAgent。
真机部署与验证由 Codex 统一负责；本项目不得在设备上与其并行操作。
