# BOTWANFA 异步版部署说明

这是新的异步版机器人代码，只包含本次实现的 Python 项目、数据库迁移、Docker 配置、部署脚本和测试。

## 包含内容

- `src/botwanfa/`：异步机器人源码。
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
玩家上下分、群排行榜、数据报表、操作日志、备份恢复、测试模式和玩法说明。

进入群管理并选择目标群后，可以继续操作：

- 暂停或恢复该群自动运行。
- 修改下注、封盘开奖、下一局和玩家掷骰时间。
- 修改最低下注、玩家掷骰门槛及每个玩法的返还倍率。
- 修改签到范围、签到步进、连胜奖励档位和走势期数。
- 查询玩家分周期数据，并通过二次确认执行上分或下分。
- 查看群排行榜、数据报表，切换该群测试模式。
- 向目标群发送独立玩法说明。

所有设置、上下分和控制操作都会写入管理员操作日志；上下分同时写入钱包账本。

下注示例：

```text
大100
dd100
和值 10 100
顺子100
111 100
```

每期群内消息严格按以下顺序发送：

1. 固定“开始下注”图片和本期下注格式。
2. 玩家投注成功或失败回复；多项投注整条成功或整条失败。
3. 固定“停止下注”图片和本期全部玩家投注清单。
4. 三颗 Telegram 原生骰子。
5. 最近走势图片，开奖结果写在同一张图片的 caption 中。
6. 全部玩家投注额、返还额、净输赢和最终余额汇总。

封盘清单或结算内容过长时会自动生成多页图片，不截断玩家。默认走势图显示最近
84 期，每行 28 期，共 3 行；管理员修改走势期数后会自动增减行数。
