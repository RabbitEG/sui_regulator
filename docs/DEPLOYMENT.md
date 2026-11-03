# 新服务器部署与验收指南（v0.4）

本指南部署的是 **司岁台持久控制平面 + Yan Console**。默认可完整运行 Mock、故障检测、代理恢复和人工干预；真实 GLM 要额外验证 Messages/SSE/工具格式，真实训练/GPU/NPU Runner 尚未实现。把 Web 服务跑起来，不代表真实科研执行端已经通过验收。

目录采用“固定源码版本 → 独立 release 虚拟环境 → current 链接”，与持久状态、密钥分开。不要在运行中的 release 里 pip upgrade，也不要把生产状态放进 Git checkout 或 /tmp。

## 1. 准备服务器和账号

使用专用的普通 Linux 账号登录，Controller、Console 和 CLI 必须以同一 UID 访问同一状态目录。默认仅用 127.0.0.1:8765，不需要开放入站 Web 端口。准备 Git、Python 3.11 或 3.12 及其 venv/pip；不要替换系统 Python。本项目已在 Linux x86-64、Python 3.11/3.12 验证；其他架构需在目标机重跑测试。

```bash
git --version
python3.11 --version
python3.11 -m venv --help >/dev/null
python3.11 -c 'import sqlite3,sys; print(sys.version); print("SQLite",sqlite3.sqlite_version)'
systemctl --user --version
```

只有 Python 3.12 时，把下文的 python3.11 换成 python3.12。Python/venv、发行版包及 systemd 用户服务由服务器管理员按本机政策准备；本指南不会自动改宿主机配置。

**状态卷必须支持本地文件锁、SQLite WAL 和可靠持久化。** 所有数据库进程在同一服务器；不要把正在使用的数据库直接放在 NFS/SMB 或跨主机共享卷上。[SQLite WAL 官方说明](https://sqlite.org/wal.html#overview)

正式环境还应确认 Python 实际链接的 SQLite 已包含 WAL-reset 并发问题修复：例如 3.51.3+，或官方列出的 3.44.6/3.50.7 回移版本，或发行版确认的等效补丁。仓库在 SQLite 3.26.0 通过的功能测试不能证明其补丁状态；不要用替换系统动态库的方式临时升级。[SQLite 修复范围](https://sqlite.org/wal.html#the_wal_reset_bug)

## 2. 克隆、固定版本并安装 release

以下在部署账号下执行。示例固定本次克隆得到的完整 commit；有团队批准的版本时，先 checkout 该版本。第一次安装要求 repository、release 和 current 尚不存在，命令失败后先查原因，不覆盖旧部署。

```bash
set -e
umask 077
SUI_ROOT="$HOME/sui-regulator"
SUI_REPO="$SUI_ROOT/repository"
mkdir -p "$SUI_ROOT/releases"
git clone https://github.com/RabbitEG/sui_regulator.git "$SUI_REPO"
SUI_REV="$(git -C "$SUI_REPO" rev-parse HEAD)"
git -C "$SUI_REPO" checkout --detach "$SUI_REV"
SUI_RELEASE="$SUI_ROOT/releases/$SUI_REV"
mkdir "$SUI_RELEASE"
python3.11 -m venv "$SUI_RELEASE/venv"
"$SUI_RELEASE/venv/bin/python" -m pip install --upgrade pip
"$SUI_RELEASE/venv/bin/python" -m pip install --no-deps "$SUI_REPO"
printf '%s\n' "$SUI_REV" > "$SUI_RELEASE/SOURCE_COMMIT"
"$SUI_RELEASE/venv/bin/python" -m pip freeze > "$SUI_RELEASE/pip-freeze.txt"
ln -s "$SUI_RELEASE" "$SUI_ROOT/current"
"$SUI_ROOT/current/venv/bin/python" -c 'import sui_regulator; print(sui_regulator.__version__,sui_regulator.__file__)'
```

私有仓库需要认证时，可将克隆地址换成已授权的 `git@github.com:RabbitEG/sui_regulator.git`；不要在 URL 中放 token。

这是非 editable 安装。运行时零第三方依赖，pip 构建阶段仍可能从配置的包源获取 setuptools 等构建工具。受限网络应由管理员准备批准的 wheelhouse，在可联网的兼容构建机先执行 `python3.11 -m pip wheel --wheel-dir wheelhouse .`，把完整 wheelhouse 带到目标机，再以 `"$SUI_RELEASE/venv/bin/python" -m pip install --no-index --find-links /path/to/wheelhouse sui-regulator==0.4.0` 安装到同一 release venv；成功后继续写入 SOURCE_COMMIT 并创建 current 链接。不要把真实凭据放进 Git URL 或命令行。

## 3. 在隔离目录先验收

离线规则评估和 SIGKILL 演示使用已安装的包；默认测试使用固定 commit 的源码。它们只创建临时专用进程，不连接模型。

```bash
SUI_ROOT="$HOME/sui-regulator"
SUI_YAN="$SUI_ROOT/current/venv/bin/yan"
SUI_PY="$SUI_ROOT/current/venv/bin/python"
cd "$SUI_ROOT/repository"
PYTHONPATH=src "$SUI_PY" -m unittest discover -s tests -v
"$SUI_YAN" failures evaluate tests/failure_corpus --summary
"$SUI_YAN" demo
"$SUI_YAN" console-demo --port 8766
```

演示输出 URL 后，从本机授权 SSH 转发 8766，按 [Console 指南](YAN_CONSOLE.md) 检查 GEN-001、代理换代、原 Job/PID 与单次 execution、Hold/Resume/指令回执、UNKNOWN。Ctrl+C 结束演示会清理它自己的专用进程。**不要用 console-demo 作为正式服务。**

v0.4 当前基线：默认套件 88 项，其中 4 项未安装/未启用 Claude 时 skip；开启 CLI 合约测试后 88 项全部通过。案例集 42 条 passed=true；`yan demo` 的所有 checks 应为 true。这些是本版本基线，不是未来版本固定数量。

可选真实浏览器验收放在独立 QA checkout，避免改写正式源码中的参考报告：

```bash
SUI_ROOT="$HOME/sui-regulator"
SUI_REV="$(cat "$SUI_ROOT/current/SOURCE_COMMIT")"
SUI_QA="$SUI_ROOT/qa-$SUI_REV"
git -C "$SUI_ROOT/repository" worktree add --detach "$SUI_QA" "$SUI_REV"
python3.11 -m venv "$SUI_QA/.venv"
"$SUI_QA/.venv/bin/python" -m pip install playwright
"$SUI_QA/.venv/bin/python" -m playwright install chromium
cd "$SUI_QA"
SUI_BROWSER="$(.venv/bin/python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()')"
PYTHONPATH=src .venv/bin/python scripts/verify_console_browser.py --browser "$SUI_BROWSER"
```

浏览器系统库由目标机管理员准备，生产服务本身不需要 Chromium/Playwright。基线为 15 个浏览器检查和 11 张截图；目标机的报告、截图留在该 QA checkout。[Playwright 浏览器安装说明](https://playwright.dev/python/docs/browsers)

## 4. 初始化持久状态和私有配置

```bash
umask 077
SUI_ROOT="$HOME/sui-regulator"
SUI_STATE="$HOME/.local/state/sui-regulator"
install -d -m 700 "$SUI_STATE" "$HOME/.config/sui-regulator"
# 仅首次初始化时复制；已有配置保持原样。
if [ ! -e "$SUI_STATE/config.toml" ]; then
  install -m 600 "$SUI_ROOT/repository/examples/config.toml" "$SUI_STATE/config.toml"
fi
"$SUI_ROOT/current/venv/bin/yan" --home "$SUI_STATE" init
```

保持该目录所有权为部署 UID、权限 0700。Console/Controller 两个服务必须使用完全相同的绝对路径。config.toml 中 heartbeat_seconds 必须小于 lease_seconds；max_proxies 是规范名称，旧 max_workers 可读但不可同时出现。默认配置即可验收 Mock；failure_registry 留空使用随包规则。

如没有可用的 systemd 用户管理器，先在两个终端以前台方式启动，或由管理员提供等效守护；不要假定 tmux、断开 SSH 后的后台命令具有可靠开机恢复：

```bash
"$HOME/sui-regulator/current/venv/bin/yan" --home "$HOME/.local/state/sui-regulator" serve
# 另一个终端：
"$HOME/sui-regulator/current/venv/bin/yan" --home "$HOME/.local/state/sui-regulator" console --port 8765
```

## 5. 安装 systemd 用户服务

仓库模板：

- [sui-regulator-controller.service](../deploy/systemd/user/sui-regulator-controller.service)
- [yan-console.service](../deploy/systemd/user/yan-console.service)
- [可选模型环境文件](../deploy/controller.env.example)

模板的 %h 由 systemd 展开为部署账号 home，路径与上述布局一致；自定义路径时同步修改模板。普通用户服务不填写 User=，不要用 sudo 启动这两个应用进程。先退出同一目录的前台试跑 Controller/Console，避免单实例锁与端口冲突；不要按进程名称批量终止。

```bash
install -d -m 700 "$HOME/.config/systemd/user"
install -m 644 "$HOME/sui-regulator/repository/deploy/systemd/user/sui-regulator-controller.service" "$HOME/.config/systemd/user/"
install -m 644 "$HOME/sui-regulator/repository/deploy/systemd/user/yan-console.service" "$HOME/.config/systemd/user/"
systemd-analyze --user verify "$HOME/.config/systemd/user/sui-regulator-controller.service" "$HOME/.config/systemd/user/yan-console.service"
systemctl --user daemon-reload
systemctl --user enable --now sui-regulator-controller.service yan-console.service
systemctl --user status sui-regulator-controller.service yan-console.service --no-pager
```

要使退出登录后/开机时仍运行，管理员为该账号批准并启用 lingering，例如管理员执行 `loginctl enable-linger DEPLOY_USER`，部署账号用 `loginctl show-user "$(id -un)" -p Linger` 核验。不能连接 user bus 时，先由管理员修复用户服务会话，不要手工伪造 XDG_RUNTIME_DIR。[loginctl 官方说明](https://www.freedesktop.org/software/systemd/man/latest/loginctl.html)

**Controller 的 KillMode=process 是有意的生命周期取舍。** 默认 control-group 会在停止/重启 Controller 时连带终止同组代理和 Runner；本模板只停止主控制进程，让已有工作保留。systemd 官方通常不推荐 process，因为留下的进程超出普通服务停机语义；因此 `stop controller` 不代表整个应用已停止，完整停机见第 9 节。Console 没有这些子作业，采用 control-group，且不使用 Requires/PartOf 绑定 Controller，以便控制进程离线时仍展示快照。[systemd KillMode 语义](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)

不要添加 ExecStop=pkill/killall 或清理整个 cgroup 的脚本。也不要在服务中使用可变的源码 PYTHONPATH；release 独立安装使旧代理继续使用原版本环境。

## 6. 访问、健康检查与首个持久任务

在自己的电脑建立授权隧道，不在服务器上开放 0.0.0.0：

```bash
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 DEPLOY_USER@YOUR_SERVER
```

浏览器打开 http://127.0.0.1:8765。本机端口被占用可把第一个 8765 换成 18765。localhost Host/Origin 检查兼容该端口变化；不要把 Console 接到公网反向代理。它没有多用户鉴权，访问范围是可信本机及获准的 SSH 转发。若主机有不受信任的本地账号，应先由管理员落实网络/访问隔离；专用 UID 本身不会给这个 HTTP 端口增加身份认证。

服务器上核验真实 tick，不能只看 systemctl active 或首页 HTTP 200：

```bash
"$HOME/sui-regulator/current/venv/bin/yan" --home "$HOME/.local/state/sui-regulator" rpc yan.get_console_snapshot --params '{"section":"overview","limit":1}'
journalctl --user -u sui-regulator-controller.service -u yan-console.service -n 100 --no-pager
```

响应必须有 result.controller.state=ONLINE、近期 last_tick_at 和正确 PID。初始 STARTING 可稍后重试。提交一个持久的合成任务：

```bash
SUI_YAN="$HOME/sui-regulator/current/venv/bin/yan"
SUI_STATE="$HOME/.local/state/sui-regulator"
"$SUI_YAN" --home "$SUI_STATE" submit '目标机部署验收' --key target-acceptance-001 --adapter mock --spec '{"steps":1200,"interval":0.1}'
# 使用回执中的真实 ID 替换以下占位符：
"$SUI_YAN" --home "$SUI_STATE" receipt COMMAND_ID
"$SUI_YAN" --home "$SUI_STATE" get TASK_ID
```

在 Console 检查 Desired/Declared/Observed、原 Job/PID、execution_count=1 和指标。用 Hold（明确确认）→ Resume → 追加指令，核验 Receipt；指令成功不等于 AI 已完成分析。CLI 与 Web 的 actor 分别是 local:<user> 和 uid:<uid>，幂等键作用域包含 actor，重复验证同一请求要使用同一入口和同一键。

在没有真实任务的验收窗口，可以依次测试：

```bash
systemctl --user restart yan-console.service
systemctl --user restart sui-regulator-controller.service
# 观察一次真实离线，再恢复：
systemctl --user stop sui-regulator-controller.service
systemctl --user start sui-regulator-controller.service
```

重启后检查原 Task/Job ID、Runner PID 和 execution_count。停止 Controller 时 Console 应变为离线/快照，停用操作；恢复后重新核验。此测试验证目标机的 user manager/cgroup 行为，不得拿真实训练做故障注入。GEN-001/进程强杀测试继续使用第 3 节的专用演示。

## 7. 接入 Claude CLI / 真实 GLM（可选）

先固定并验证本地 CLI 合约，不需要真实 key：

```bash
cd "$HOME/sui-regulator/repository"
"$HOME/sui-regulator/current/venv/bin/python" scripts/install_claude.py --version 2.1.316
"$HOME/.local/share/sui-regulator/tools/claude-code/2.1.316/claude" --version
PATH="$HOME/.local/bin:$PATH" SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src "$HOME/sui-regulator/current/venv/bin/python" -m unittest tests.test_claude.InstalledClaudeTests tests.test_stream_capability -v
```

安装器当前支持 Linux x86-64/aarch64 的官方原生包并校验 SHA-512；本仓库实际验证的是 x86-64。如果 ~/.local/bin/claude 已存在，安装器不会覆盖它；合约测试会使用 PATH 中的实际 CLI，应先核对 `command -v claude` / `claude --version`，需要时将 PATH 首项设为上面已固定二进制的目录。

真实接入由操作员填入**已批准的** Anthropic Messages/SSE 兼容端点与私有凭据。不要猜 GLM endpoint 或声称所有 OpenAI-compatible 服务可直接替换：

```bash
if [ ! -e "$HOME/.config/sui-regulator/controller.env" ]; then
  install -m 600 "$HOME/sui-regulator/repository/deploy/controller.env.example" "$HOME/.config/sui-regulator/controller.env"
fi
# 用本机编辑器填写 controller.env，并修改私有 config.toml：
# claude_binary = "/absolute/home/.local/share/sui-regulator/tools/claude-code/2.1.316/claude"
# claude_model = "PLATFORM_MODEL_ID"
systemctl --user restart sui-regulator-controller.service
```

不要把密钥写入命令行或仓库；不要直接覆盖已有 controller.env。EnvironmentFile 是 systemd 语法，不写 export 或 shell 命令。只有 Controller 读取该文件，Console 不需要模型凭据。更换凭据/模型前先 Hold 现有代理：已启动的旧代理不会因 Controller 重启自动获得新环境。

先提交一个独立、低成本的 `--adapter claude` 验收 Task，核验工具调用、实际 text_delta、401/限流/断流及 fresh Session。默认只开放受管 MCP 和合成 Job，不能据此认定真实训练、代码编辑、GPU 调度或远程取消已经可用。

## 8. 日常检查与故障定位

| 现象 | 检查 |
|---|---|
| Console 在线，Candleholder 过期/离线 | 查 get_console_snapshot 的最近完成 tick、Controller journal、磁盘/SQLite 锁；静态 HTTP 200 不是健康证明 |
| 无法连接 user bus | 确认真实部署账号的登录会话、user manager 与 lingering；找管理员修复，不以 root 启动同一账本 |
| 目录权限报错 | state 必须属于部署 UID、0700，不能是符号链接；不要开放到 0777 |
| 端口被占用 | 保留一个 Console，或通过 unit drop-in 修改 --port，再同步 SSH；不开放公网监听 |
| Claude 未找到/版本不符 | 私有 config 用绝对 claude_binary；服务 PATH 与交互式 shell 可能不同 |
| 401/配额/未知异常 | 看 Incident 的 evidence、建议/最终动作/outcome 与隔离状态；修复后由明确 Resume 重新授权 |
| Job UNKNOWN | 保留不明结果，核验执行端；不要删除 executions、改逻辑键或再次提交同一作业来强行重跑 |
| 已收到 QUEUED 但效果未出现 | 查 get_command_status、Controller tick 和 REJECTED 原因；丢回执时保留原 actor/key，不自动新建键重发 |

## 9. 备份、升级、回滚和完整停机

在线账本备份使用 SQLite backup API，目标文件必须不存在：

```bash
umask 077
install -d -m 700 "$HOME/sui-regulator/backups"
SUI_BACKUP="$HOME/sui-regulator/backups/ledger-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
"$HOME/sui-regulator/current/venv/bin/yan" --home "$HOME/.local/state/sui-regulator" backup "$SUI_BACKUP"
```

该文件只有账本，不包含 config、workspace、Job 日志/产物和私有模型配置。完整备份/停机应：逐个 Hold 活跃 Task 并确认回执 → 等待 Job 终态（或按明确授权取消受管合成 Job）→ 停止 Console/Controller → 核验无仍在写入的受管代理/Job → 对整个私有状态目录做文件系统快照/归档，同时保存 release SHA 和私有环境配置。不要复制运行中的主数据库文件冒充一致备份，不要删除仍被进程使用的 WAL/SHM/锁目录。

升级在维护窗口进行：

1. 记录 `readlink -f ~/sui-regulator/current`、SOURCE_COMMIT 和数据库 schema，完成备份。Hold 活跃代理、等待受管 Job 终态；保留旧 release。
2. 在 repository fetch/checkout 经批准的新 commit；按第 2 节为它创建**新的** release venv，非 editable 安装，重跑第 3 节验收。不要覆盖旧 release，也不要盲目覆盖私有配置。
3. 停止两个用户服务。用 `ln -s NEW_ABSOLUTE_RELEASE ~/sui-regulator/current.next`、`mv -Tf ~/sui-regulator/current.next ~/sui-regulator/current` 原子切换链接（占位符须替换为实际路径）。
4. 如模板有变，按第 5 节更新 unit 并 daemon-reload。启动 Controller/Console，核验 tick、旧 Task/Job/Incident；最后由操作员按需 Resume。

回滚代码同样切回保留的旧 release 后重启，但必须确认旧代码能读取当前 schema。应用会拒绝较新的数据库版本；不能通过降低 user_version 强行降级。需要恢复旧备份时先确保所有相关进程停止，并一起恢复匹配的状态/产物。持久字段含绝对 workspace/产物路径，恢复到另一服务器应维持原绝对路径；跨路径迁移尚无自动工具。新主机不能恢复已丢失的物理进程，旧 Job 应核验为 UNKNOWN，不会自动复活训练。

不要删除旧 release，直到旧代理和 Runner 全部退出；它们可能仍在使用旧解释器/包。`KillMode=process` 只影响该 Controller unit 的停机动作，不能保证服务器重启、用户管理器退出、其他 cgroup 管理或容器销毁时进程存活。

## 10. 交付与验证边界

仓库已验证 Python 3.11/3.12、Mock 专用进程、已安装 Claude CLI + 假 API、真实浏览器、wheel 安装及 Console/Controller 的进程独立性。systemd 模板可静态核验，但本开发会话没有在宿主机安装/启用服务或 lingering；**目标服务器必须完成第 5–6 节的真实守护、退出登录/开机与 cgroup 验收**。真实 GLM、企业平台、持久卷和训练 Runner 仍由目标环境验证。

本指南的命令已在独立临时目录验证离线 wheelhouse 安装、8 项启动/重启/控制/备份检查，以及 systemd 239 的渲染模板静态校验，结果见 [部署验证记录](deployment-verification.json)。在线包源测试因当前环境代理连接失败未完成，未将其记为通过；离线路径已通过。使用的是全新本地 Git 克隆，目标机的 GitHub 认证仍需自行配置。

相关资料：[Console 使用](YAN_CONSOLE.md)、[v0.4 验收](YAN_CONSOLE_DELIVERY.md)、[命名与兼容](NAMING.md)、[安全边界](SECURITY.md)。
