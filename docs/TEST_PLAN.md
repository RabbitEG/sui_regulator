# 首轮验收与复现

测试分三层：纯逻辑/SQLite 事务，真实本地专用进程的模拟故障，以及真实安装的 Claude CLI + loopback 假 API。第三层仍然不是真实模型或内部 GLM 集成验证。

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src python3.11 -m unittest discover -s tests -v
PYTHONPATH=src python3.12 -m unittest discover -s tests -q
PYTHONPATH=src python3.11 -m sui_regulator demo
python3.11 scripts/export_schemas.py --check
```

默认测试不需要 API、Claude、GPU 或第三方库。两个 InstalledClaudeTests 在未设置 opt-in 或无 CLI 时明确 skip；启用后只连接测试启动的 127.0.0.1 假服务，使用合成 key，并清除测试子进程的代理变量。

| 需求 | 覆盖与证据 |
|---|---|
| T01 | core 持久化 + gateway socket 提交 + lifecycle Worker 领取 |
| T02 | protocol 进度报告、Task declared 和 Event 历史；真实 CLI MCP report_progress |
| T03 | lifecycle 真正 SIGKILL Worker，新 generation 接管 |
| T04 | 同一 Runner PID/Job/execution 在 Worker 死亡后继续并完成 |
| T05 | 主动轮换产生 Handoff；冻结 Worker 无 Handoff 时仍可接管 |
| T06 | fake CLI 空 Bash 参数达到阈值，确定性检测并回收；纯函数重复文本检测 |
| T07 | 真正 SIGKILL Controller、保留 Job/Worker 后重启；额外冻结持写锁 Worker 测试 |
| T08 | 竞争提交去重、参数冲突、重复 logical_key、重复 supervisor 只有一次 execution |
| T09 | 过期写/续租拒绝；Runner 启动与取消副作用执行端二次校验 |
| T10 | resource_available=false → BLOCKED，正常等待，无实际 GPU 抢占 |
| T11 | 取消 Task 跨协调保持，默认保留 Job；显式合成 Job 取消另行授权并去重 |
| T12 | Worker 声称 COMPLETED，Job 仍显示 QUEUED；complete_task 被拒绝 |
| T13 | 实际创建子进程后抛 ConnectionResetError 模拟回执丢失，原 Job 被核验；没有重发 |
| T14 | 同 UID 机器客户端通过 Unix RPC 获得 JSON、来源、观测时间与陈旧标记 |
| T15 | 追加指令持久化、回执可追踪；成功表示记录要求，不冒充模型已执行 |
| T16 | 所有替代 Worker 不可用并隔离时，原 Job 仍完成，查询/新指令仍可工作 |

额外测试：竞争领取、未知 Runner 不盲目重启、PID 身份、未知进程不清理、单 Controller 锁、跨 Task 越权、Artifact 路径逃逸、Handoff 权威字段、stdout/stderr 背压、截断流、鉴权即时隔离、v1→v2 迁移和一致性备份。

持写锁故障使用专门的 `tests/fixtures/lock_holder.py`；它明确冻结测试 Worker，不操作训练进程。所有过程使用临时私有状态目录，清理只针对测试登记的进程。

当前结果见 [原始输出](test-output.txt)、[交付报告](DELIVERY.md)。Linux aarch64、真实容器重建、公司 GLM、真实训练/远程连接不在已验证范围；Mock 通过不能证明这些环境一定通过。
