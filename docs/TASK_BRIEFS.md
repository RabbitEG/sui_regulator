# 定义并执行完整任务

Task 的目标由用户定义。固定监督提示只说明所有权、取证、换代和交接，不把工作改写成 watchdog。创建时提交的多行任务书、验收条件、项目目录和执行能力保存在 SQLite，所有新 Generation 都读取同一份要求；追加指令仍走 Yan Command，并保留回执。Console 的 Task 详情显示原文，Session 阅读器显示监督协议和初始任务输入。

## 创建任务

Yan Console → 任务 → 创建任务，填写完整任务书。建议说明现有实验 ID 和查询方式、结束后的核验步骤、允许修改的项目、失败后的调试要求、下一轮实验条件、资源与停止边界。这些只是写作提示，不是强制的实验模板。

CLI 支持直接读取 UTF-8 Markdown（最多 16384 字符，超过会拒绝而非静默截断）：

```bash
yan --home /absolute/private/state submit '实验核验、调试与后续实验' \
  --adapter claude --execution-profile claude_code \
  --workspace /absolute/project \
  --objective-file /absolute/task-brief.md \
  --completion-criteria '目标测试全部通过；保存实验指标、修改说明与结果路径'
```

`--objective` 与 `--objective-file` 二选一。提交返回 Command Receipt；Controller 处理后使用 `yan --home ... receipt COMMAND_ID` 核对 `SUCCEEDED`。它表示任务已创建，不表示实验已完成。项目目录必须已经存在；省略目录则使用私有任务目录。Mock 只演示控制平面，不会理解或执行任务书。

任务书示例：

```text
目标：继续 /srv/my-project 的实验，而不是只做存活巡检。
现有实验：填写实验 ID、状态查询命令、日志和产物目录。
先等待该实验进入可核验的终态；状态不明时禁止重复提交。
结束后核对退出状态、预期产物、测试结果及验证集指标。
若失败，定位原因，修改本项目代码，运行相关测试并保留失败证据。
修复验证后按约定参数运行下一轮实验；记录独立实验 ID。
若需要换资源，在已有授权范围内自行选择；无法取得资源时记录阻塞。
只有满足列出的验收条件才完成 Task；每次交接留下结果、未解问题和下一步。
```

## 执行能力必须匹配任务

| execution_profile | 实际工具 | 适用范围 |
|---|---|---|
| `managed`（API / CLI 默认） | Read / Glob / Grep、Sui MCP、部署者批准的固定命令模板 | 受管观察、合成演示、已配置工具的任务 |
| `claude_code`（创建时明确选择） | Sui MCP + 原生 Read / Edit / Write / Glob / Grep / Bash | 项目代码调试、检查环境、执行真实命令和实验 |

Claude Code 模式不是项目目录沙箱。原生命令与文件工具具有服务账户的权限；`--workspace` 指定工作目录，不限制文件系统访问。请用专用账户和项目权限部署。没有启用 `dangerously-skip-permissions`；工具能力通过显式 allowlist 授予。Console 没有交互 Shell 或聊天 UI。

**托管 Runner 目前只实现合成 Job。** 真实实验可以由 Claude Code 操作，但通过 Bash / SSH / 外部脚本启动的进程不自动成为托管 Job。Controller 不能为它们保证单次启动、自动追踪或 Hold 后存活；脱离代理的后台进程需独立核验。任务书应要求保存实验 ID、提交回执与状态证据，换代后先查询再决定。原生工具现在通过独立 Hook 检查所有权并记录 Bash 回执，详见 [Persistent 恢复](PERSISTENT_RECOVERY.md)。它不等于 Sui MCP 的启动事务；Hold 不能撤回已发生的外部副作用。

## 完成前的真实核验

验收条件不等于机器可判定的测试。配置 `completion_criteria` 后，`complete_task` 必须附 `evidence`；该文本仍标为代理声明，不会转换成 Observed State。

如果已有可执行的验收脚本，可在私有命令注册表中配置无参数模板：

```toml
[command.accept_experiment]
argv = ["/absolute/venv/bin/python", "/absolute/check_acceptance.py"]
patterns = {}
```

注册表必须为当前账户所有且禁止组和其他用户读写（例如 `chmod 600`），路径写入私有 `config.toml` 的 `[regulator] command_registry`。脚本应进行有界的结果检查，不用于启动长实验；命令最长 120 秒。创建时加：

```bash
# 添加到上面的 submit 命令
--verification-template accept_experiment
```

完成前由后端重新执行此模板，非零退出码会拒绝完成，让代理继续分析。通过后记录真实退出码和输出，并写入持久完成标记；这只证明该脚本通过，不能证明脚本没有覆盖的科学结论。所有托管 Job 仍必须进入已知终态；存在未知或运行中的 Job 时不能完成。允许保留历史失败 / 已取消实验，避免调试成功后被早期失败永久阻塞。

固定命令执行已在启动前检查当前所有权，并记录执行开始和实际结果。命令运行中所有权变化仍保留真实结果，不将结果记为当前代理的活动。输出有界，超时终止该命令进程组。原生工具使用独立的 Hook 检查路径。

## 验证与限制

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_task_brief -v
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_claude.InstalledClaudeTests.test_native_cli_verifies_edits_and_runs_followup_experiment -v
.venv/bin/python scripts/verify_task_brief_browser.py
```

任务书首阶段曾通过 111 项回归（含 4 项真实 CLI + loopback 测试），[历史原始输出](task-brief-test-output.txt)。后续 Persistent、完成标记与合作式换代已实现，最新结果见 [自主任务交付报告](AUTONOMY_DELIVERY.md)；任务书页面现有 7 项浏览器检查，[报告](task-brief-browser-report.json)。

本地真实 Claude Code + loopback 模拟 API 已验证：执行失败 → Read → Edit → 重跑 → 产物落盘 → 完成。模拟 API 提供预设工具决策；这验证了执行链路，不验证真实 GLM 的研究、诊断或遵循任务能力。另有后端测试验证失败的验收脚本拒绝完成、修复后通过、换代保留任务书、已撤销代理不能执行命令。

截图：[创建任务](screenshots/task-brief/create-task.png) · [完整任务详情](screenshots/task-brief/task-requirements.png)。

升级前按部署指南备份；任务书阶段迁移到 schema 4，后续 Persistent 阶段为 schema 5，当前职责接续阶段为 schema 7，旧任务保持 `managed`，不会静默扩大权限。旧版本不能直接读取迁移后的数据库。

完整提示词注入、合作式换代和完成标记字段见 [长期 Agent 设计](PROMPT_AND_LIFECYCLE_DESIGN.md)。实验错误和重复测试均不会被作为 Session 故障；恢复额度针对运行基础设施。

## 持续工作和等待如何表达

任务书可以完整规定“等实验结束 → 核验 → debug → 复测 → 继续实验”，也可以定义与实验无关的普通工作。可写明“持续运行直到我取消，每轮结果只做记录，不作为整个委托的完成标记”。语义由代理判断，Controller 不根据实验失败、成功或固定次数替模型决定下一步。

追加要求可在 Console / Yan 录入，当前 Session 的下一次工具往返或停止边界会收到提醒；慢工具运行期间不会被为此硬中断。长要求经完整分页读取，再提交已读 ACK。需要省下等待期间的 Session 时，代理可调用 wait_task，框架负责登记和到期接续；它不会替代理查询或迁移未配置的远端资源。

当前没有“交付长期服务、移交运行责任后结束 Task”的受管契约。有关 Job 或副作用仍未终态时请保持任务继续或使用明确支持的等待方式；详见 [完成边界](PROMPT_AND_LIFECYCLE_DESIGN.md#本轮接续协议schema-7)。

## 无人值守授权

在任务书中完整定义目标、授权和停止条件，提交时使用 `--permission-mode trusted`。它配套 Persistent 和 Claude Code 工具，让代理自主处理 UNKNOWN、调试与后续实验，不要求逐次确认；明确的 Hold、Cancel 和运行额度仍有效。[完整一周任务模板与命令](TRUSTED_AUTONOMY.md)。
