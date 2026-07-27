# BOTWANFA 异步版部署说明

这是新的异步版机器人代码，只包含本次实现的 Python 项目、数据库迁移、Docker 配置、部署脚本和测试。

## 包含内容

- `src/botwanfa/`：异步机器人源码。
- `src/botwanfa/assets/`：群内开始下注和封盘 GIF 素材。
- `migrations/`：PostgreSQL 数据库迁移。
- `scripts/linux/`：Linux 一键安装、更新、启停、状态、备份、恢复脚本。
- `scripts/windows/`：Windows Docker Desktop 部署脚本。
- `Dockerfile`、`docker-compose.yml`：容器化部署配置。
- `tests/`：核心玩法、结算、消息和备份加密测试。
- `开发实现说明.md`：当前阶段实现范围、验证方式和后续开发清单。

## Linux 部署（全新 Ubuntu 24）

在新服务器上直接执行：

```bash
sudo apt-get update && sudo apt-get install -y git ca-certificates curl && git clone https://github.com/heydariadmira302/botwanfa.git && cd botwanfa && bash scripts/linux/install.sh
```

脚本会自动检查并安装缺失依赖，包括 Docker Engine、Docker Compose 插件、Git、curl、openssl 等；随后生成 `.env`、构建镜像、执行迁移并启动服务。

安装完成后，机器人会主动给 `SUPER_ADMIN_IDS` 里的超级管理员发送部署完成通知。以后执行 `bash scripts/linux/update.sh` 更新完成后，也会主动发送更新完成通知。

安装过程中会询问三项：

- **机器人 Bot Token**：从 Telegram 的 BotFather 获取。
- **超级管理员 Telegram 数字ID**：这是你的 Telegram 用户数字 ID，不是用户名。可以在 Telegram 里私聊 `@userinfobot` 或 `@RawDataBot` 查询。多个管理员用英文逗号分隔，例如 `123456789,987654321`。
- **备份加密密钥**：用于加密数据库备份，至少 12 个字符，请自己保存好。

如果已经 clone 过项目，只需：

```bash
cd botwanfa
bash scripts/linux/install.sh
```

日常命令：

```bash
bash scripts/linux/status.sh
bash scripts/linux/update.sh
bash scripts/linux/backup.sh
bash scripts/linux/restore.sh backups/xxx.bwf
```

## Windows 部署

Windows 需要 Docker Desktop，并且机器必须支持虚拟化。云 Windows/VPS 需要开启嵌套虚拟化。

```powershell
cd C:\path\to\botwanfa
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\install.ps1
```

如果 Docker Desktop 提示 `Virtualization support not detected`，请先在 BIOS 或云厂商面板开启 AMD-V/SVM、Intel VT-x 或 Nested Virtualization。

日常命令：

```powershell
.\scripts\windows\status.ps1
.\scripts\windows\update.ps1
.\scripts\windows\backup.ps1
.\scripts\windows\restore.ps1 -Source .\backups\xxx.bwf
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests migrations
```

已验证核心测试通过：玩法解析、216 种三骰组合、111/666 叠加命中、状态机、消息转义、备份加密。

## 运行后使用

把机器人加入群后，在群内发送：

```text
/start
/余额
```

超级管理员请私聊机器人发送：

```text
/start
```

或：

```text
/menu
```

机器人会打开完整中文管理后台。一级菜单包括运行总览、群管理、查询玩家、
玩家上下分、群排行榜、数据报表、发送队列、操作日志、备份恢复、测试模式和玩法说明。

进入群管理并选择目标群后，可以继续操作：

- 暂停或恢复该群自动运行。
- 修改下注、封盘开奖、下一局和玩家掷骰时间。
- 修改最低下注、玩家掷骰门槛及每个玩法的返还倍率；倍率按大小单双、组合、和值、顺子豹子分区，无需逐页翻找，并支持分类批量设置和单项启停。
- 修改签到范围、签到步进、连胜奖励档位和走势期数。
- 查询玩家分周期数据，并通过二次确认执行上分或下分。
- 查看群排行榜、数据报表，切换该群测试模式。
- 为开始下注、停止下注、玩家掷骰邀请、开奖结果和玩法说明分别配置最多 8 个跳转按钮。
- 查看发送失败原因，并执行单条重试、全部重试、单条删除或批量删除。
- 向目标群发送独立玩法说明。

所有设置、上下分和控制操作都会写入管理员操作日志；上下分同时写入钱包账本。

下注示例：

```text
大100
dd100
小单100
和值 10 100
顺子100
111 100
```

每期群内消息严格按以下顺序发送：

1. 固定“开始下注”GIF 和本期下注格式。
2. 玩家投注成功或失败回复；多项投注整条成功或整条失败；封盘后的下注会明确回复失败且不扣分。
3. 固定“封盘”GIF 和本期全部玩家投注清单；普通清单优先使用文字，不为每个正常投注用户制作图片。
4. 默认邀请本期累计有效下注最高的玩家在 25 秒内发送三颗 Telegram 原生骰子；每颗有效骰子都会记录点数、原消息和时间，并回复对应骰子消息，最多处理前三颗。超时由机器人补发；无人下注时机器人直接掷骰，不等待玩家。
5. 每期只发送一条开奖消息：走势图图片的 caption 同时包含开奖结果和全员结算，每位下注玩家都会被艾特。
6. 合并内容超过 Telegram 图片 caption 限制时，自动生成一张“走势 + 完整结算”的合并长图；无论人数多少都不会拆成两条消息。

封盘清单和全员结算都只在超过 Telegram 文本限制时生成图片，不截断玩家。走势图采用最多最近
84 期的横屏滚动窗口，每行 14 期，格内不显示超长期号；运行到一万期以后仍只查询和绘制最近窗口，
不会把全部历史期次塞进一张图。群消息使用稳定的 32 位公开奖期代码，管理员可设置最近 14 至 84 期。

执行 `bash scripts/linux/update.sh` 时会先在旧服务继续运行期间拉取代码并构建镜像，然后自动进入
全局排空模式：所有群各自完成当前期的下注、掷骰、开奖、结算和关键消息发送，但不再开始新一期。
进度最慢的群不会阻塞其他群收尾；全部排空后才停止应用进程、执行数据库迁移并启动新版本。

超级管理员也可以先在私聊菜单选择 `平滑更新 -> 开始准备更新`，收到准备完成通知后再执行更新命令。
如果存在异常期次或本次排空后发送失败的关键消息，更新会中止并保持排空状态，管理员处理后直接
重新执行脚本即可。新版本服务全部进入运行状态后，脚本才会解除排空并恢复各群自动开新期次。
默认最多等待 900 秒，可通过 `DRAIN_TIMEOUT_SECONDS=1800 bash scripts/linux/update.sh` 调整。
`--force` 只用于管理员明确接受中断当前异常期次的场景。

首次从旧版更新到带平滑更新功能的版本，请使用：

```bash
git pull --ff-only && bash scripts/linux/update.sh
```

下注、钱包、骰子进度和期次状态均保存在 PostgreSQL 中。
