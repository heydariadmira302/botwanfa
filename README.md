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

下注示例：

```text
大100
dd100
和值 10 100
顺子100
111 100
```
