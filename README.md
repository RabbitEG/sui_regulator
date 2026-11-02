# Sui Regulator（司岁台）

独立于 AI Session 的持久任务控制平面：Task 持久，Sui 可替换；Candleholder 独立监控，Yan 随时发令。

首轮可运行原型，Python 3.11+ / Linux，运行与默认测试零第三方依赖。已实现 SQLite 任务/指令账本、lease/fencing、独立合成 Runner、Mock/Claude Code Worker、故障接管、Session 轮换、JSON CLI、Unix RPC、MCP 和审计。当前 Runner 仅执行合成实验；真实 GLM、GPU/NPU 与远程传输仍待接入。

## 立即验证

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator demo
python3.11 -m unittest discover -s tests -v
```

`demo` 在隔离临时目录启动真正的专用子进程，向 Worker 发送 SIGKILL，然后验证新 generation 接管、原 Runner PID 不变、只有一个 Job 和一次 execution，最后返回 JSON 与合成结果。它不使用真实模型或计算资源。

完整验收见 [测试计划](docs/TEST_PLAN.md) 和 [交付报告](docs/DELIVERY.md)。

## 持久运行

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator --home .sui init
# 可选：首次启动前将 examples/config.toml 复制到 .sui/config.toml
python3.11 -m sui_regulator --home .sui serve
```

另开终端，同样设置 PYTHONPATH：

```bash
python3.11 -m sui_regulator --home .sui submit '合成训练' --key trial-1 --spec '{"steps":100,"interval":0.1}'
python3.11 -m sui_regulator --home .sui list
python3.11 -m sui_regulator --home .sui get TASK_ID
python3.11 -m sui_regulator --home .sui instruct TASK_ID '下一步比较已有指标' --key next-analysis
python3.11 -m sui_regulator --home .sui receipt COMMAND_ID
python3.11 -m sui_regulator --home .sui rpc yan.get_task --params '{"task_id":"TASK_ID"}'
```

`submit/instruct/hold/resume/cancel` 返回持久 Command 回执，Controller 异步应用；`receipt` 查询结果。没有 Controller 时 `drain` 可只处理纯数据库指令。相同 actor/key 不同参数会报冲突。

`hold TASK_ID` 暂停 Worker，`cancel TASK_ID` 持久取消 Task；两者保留正在运行的 Job。只有单独的 `cancel-job JOB_ID` 明确请求结束受管**合成** Job；Runner 复核授权后才报告 CANCELLED。终态 Task 不能 resume。

控制器退出后 Job 保留；生产环境需要操作员配置 OS/容器守护。默认没有公网监听。状态目录需当前 UID 拥有且为 0700，不要把 `--home` 指向源码或共享目录。

## Claude Code 适配

```bash
python3.11 scripts/install_claude.py
claude --version
SUI_TEST_REAL_CLAUDE=1 python3.11 -m unittest tests.test_claude.InstalledClaudeTests -v
```

安装器获取官方原生包并校验 integrity，不需要 npm、Node 或 root。已实测版本 2.1.316。上述测试使用真实 CLI + 本地假 Messages API + 本项目 MCP，仅用合成 API key，无需真实 API。默认套件明确跳过这两项，设置 opt-in 后运行。

在真实平台提供批准的模型环境配置后可 `submit ... --adapter claude`。默认只开放受管 MCP，禁用内置 Shell；完整代码编辑、真实训练 Runner 和内部 GLM 的端到端兼容性尚未验证。详见 [Claude Adapter](docs/CLAUDE_ADAPTER.md)。

## 安装为命令

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/yan demo
```

源码运行不需要安装。`yan schema` 输出机器接口 Schema；`scripts/export_schemas.py --check` 检查 Schema 同步。`yan backup /private/backup.sqlite3` 生成一致、私有的 SQLite 备份，不覆盖既有文件。

## 文档

v0.3 故障检测正在按阶段交付，已提供离线 Registry + Corpus + Replay：

```bash
PYTHONPATH=src python3.11 -m sui_regulator failures evaluate tests/failure_corpus --summary
```

设计与复用依据见 [故障调查](docs/FAILURE_RESEARCH.md)、[检测架构](docs/FAILURE_ARCHITECTURE.md)；案例贡献说明见 [Incident Corpus](tests/failure_corpus/README.md)。

- [原始需求](docs/REQUIREMENTS.md)、[上游源码与复用判断](docs/REUSE_REVIEW.md)、[环境核验](docs/ENVIRONMENT.md)
- [架构](docs/ARCHITECTURE.md)、[数据模型](docs/DATA_MODEL.md)、[Sui Protocol](docs/SUI_PROTOCOL.md)、[Yan API](docs/YAN_API.md)
- [恢复策略](docs/RECOVERY_POLICY.md)、[安全边界](docs/SECURITY.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)
- [交付报告](docs/DELIVERY.md)、[测试原始输出](docs/test-output.txt)、[结构化进度](docs/PROGRESS.json)

MIT License。开发测试只操作专用进程和合成数据，没有启动/终止现有训练、修改宿主机配置或上传公司数据。
