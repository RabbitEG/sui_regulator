# 信任模式与无人值守运行

`permission_mode=trusted` 是操作员在启动任务时授予的自主执行权限。司岁台信任岁兽代理人根据完整任务书进行实验、核验、调试和下一轮尝试，不增加一个模型来审核研究计划或每条 Shell 命令。该模式不依赖模型名称；GLM-5.3 等模型仍使用部署者已经验证的 Claude Code 兼容端点和模型配置。

## 行为

| 情况 | `guarded`（兼容原策略） | `trusted` |
| --- | --- | --- |
| UNKNOWN／STARTED 操作 | 可能拦截整个任务的写操作 | 保留记录，允许 Bash、Edit、Write 和新工作，由代理核验 |
| 已知后台 RUNNING | 限制并行写操作 | 允许继续诊断、修改与执行其他工作 |
| 认证拒绝、CLI 能力不足、Session 身份异常 | 部分情况等待人工 Resume | 持续退避重试，恢复可用后继续 |
| 核验未决操作 | 操作员 Hold → resolve-attempt → Resume | 代理调用 `reconcile_operation`，不必暂停任务 |
| 相同 operation_id 或 Job logical_key 再投递 | 返回已有回执／未决提示，不重复提交 | 保留相同语义；有意重试使用新的操作编号 |
| Hold／Cancel／用户明确运行额度 | 遵守 | 遵守 |
| 实验失败、正常调试、无新观测 | 由代理处理 | 由代理处理 |

信任模式要求 `persistent` 恢复；新 Claude 任务自动选择 `claude_code` 执行能力。原生 CLI 使用 `--permission-mode bypassPermissions`，保留指令投递、执行回执及当前所有者检查的 Hooks。工具列表包含项目读写、Bash 和 Sui MCP；不会额外启用交互问答工具。

实际权限是服务账号已有的操作系统权限，不是项目目录沙箱。框架不会额外要求逐次审批；Claude Code 的组织设置和平台自身限制仍适用。上游语义见 [Claude Code permissions](https://code.claude.com/docs/en/permissions) 和 [Hooks](https://code.claude.com/docs/en/hooks)。

## 启动一周任务

先按 [部署指南](DEPLOYMENT.md) 安装固定版本，配置好已经可用的 GLM 端点，并以独立服务运行 Controller。任务书必须明确完整工作闭环及结束条件。例如：

```markdown
# 一周实验与调试任务

目标：在未来七天内持续推进项目的实验计划。不是只监控现有进程。

先检查项目当前状态和已有实验，记录每次运行的主机、容器、目录、
命令、运行编号、日志与产物路径。等待实验结束后核验结果；发现问题时
自行分析、修改代码、复测，并按实验计划开始下一轮。已有结果继续使用。

授权：在我提供的账号、项目、节点和资源范围内自主读写、执行、核验、
调试和安排后续实验，不必逐次请求确认。任务书中另列明确的资源额度和限制。

未决操作：检查实际执行目标与状态，记录核验依据，自行决定后续行动。
暂时无法访问依赖时保留检查点，安排定时再查，并继续其他有用工作。

结束条件：到指定日期和时区，或全部目标提前达成时，整理报告与产物，
收尾仍在运行的相关工作，提交 complete_task。一个实验结束不代表整个任务完成。
```

将“指定日期”替换为实际截止时间，明确允许提前完成还是必须持续到截止时间。框架没有新增日历调度器，截止时间由模型按任务书执行。`max_proxy_seconds` 是跨代累计代理墙钟时间，不是日历截止日期，也不是 API 成本。

```bash
yan --home /absolute/private/state submit '一周实验与调试' \
  --adapter claude --permission-mode trusted \
  --workspace /absolute/project \
  --objective-file /absolute/brief.md \
  --max-proxy-seconds 0
```

无需另设“失败总次数”。`0` 表示不设代理累计运行额度。配置显式非零额度后，到额仍会 Hold；这是用户额度，不是隐藏的故障预算。Console 创建任务也可选择“信任：自主执行、核验与恢复”，自动配套持续恢复与 Claude Code 工具。

Controller 可使用仓库的 systemd 服务模板，确保退出 SSH 后仍运行；详情见 [部署与服务守护](DEPLOYMENT.md#5-安装-systemd-用户服务)。Git 或 Console 离线不影响已经启动的 Controller；主机掉电、磁盘故障、始终不可用的模型服务不可能靠会话重建直接修复。

## 已有任务与升级

Schema 8 给已有任务保留 `guarded`，保留 UNKNOWN 以及所有历史；不会因升级擅自改变授权。更新安装后重启 Controller 和 Console，已有正在运行的代理会按原进程代码继续工作，因此新 Hook／CLI 权限应在下一次自然会话启动后核验。

```bash
yan --home /absolute/private/state policy TASK_ID \
  --permission-mode trusted --max-proxy-seconds 0
# 用返回的命令 ID 查询应用结果。
yan --home /absolute/private/state receipt COMMAND_ID
```

该命令会把恢复方式设为 Persistent，Claude 任务的下一代使用 Claude Code 工具。已 ACTIVE 但处于框架 `WAITING_RESOURCE`／`QUARANTINED` 的旧任务会重新进入调度；明确的 HOLD 不会解除。已 Hold 的任务需要一次 `resume TASK_ID`。当前代不会因为改变配置被强杀；CLI 启动参数在下一代生效。若部署者希望立即切换一个已经无法工作的旧代，可以自行 Hold → 修改策略 → Resume，无需逐条清除历史 UNKNOWN。

旧版与新版进程混跑只用于过渡，不是长期部署方式。Schema 升级前按部署指南备份；旧程序不能直接打开新版账本。

## 代理核验与完成

工具参数：

```json
{
  "kind": "attempt",
  "operation_id": "native-example",
  "evidence": "检查了原主机/容器和确切路径、相关进程与产物；列明已发生的效果及继续执行的依据。"
}
```

通过 `reconcile_operation` 提交，`kind` 可为 `attempt` 或 `job`，作用于本 Task。结果有持久事件和可查询的 `reconciliation` 字段，`source=proxy-declared`。UNKNOWN 仍然是 UNKNOWN，不改写为 SUCCEEDED，也不伪造退出码。核验允许框架接受该代理的后续完成申请；明确配置的验收命令仍必须真实成功。

工具处理 STARTED／UNKNOWN 命令，以及 STARTING／UNKNOWN／BLOCKED Job。已知 RUNNING 工作应先查询、等待或结束，再处理终态；不能仅用核验工具把正在运行的工作变成已结束。迟到的运行观察保留其权威状态，核验只适用于被检查的观察状态。已核验的未决命令不会阻止代理注册定时等待。

`complete_task` 的完成回执会列出所依赖的代理核验，`managed_jobs_terminal` 不会在仍有 UNKNOWN 时虚报为真。用户是否完成科研目标仍由模型判断；框架只消费任务协议与证据。

## 验证范围

`tests/test_trusted_autonomy.py` 验证未知/后台操作不锁工具、配置故障自动退避、七天虚拟时间推进、核验后完成、同编号去重、Hold/额度、跨任务和旧代约束以及 Schema 7 升级。七天虚拟时间测试不等于七天真实模型耐久运行。

`tests/test_claude.py` 的 `test_trusted_cli_debugs_past_unknown_and_reconciles_without_operator` 使用本机真实 Claude Code 与本地合成 API，实际执行 Bash、Write、核验和完成。真实 GLM 端点和目标服务器仍应按部署指南进行现场运行验证。

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_trusted_autonomy -v
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_claude.InstalledClaudeTests.test_trusted_cli_debugs_past_unknown_and_reconciles_without_operator -v
```
