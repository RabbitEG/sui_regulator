# Sui Regulator（司岁台）

独立于 AI Session 的持久任务控制平面：Task 持久，Sui 可替换；Candleholder 独立监控，Yan 随时发令。

v0.3 可运行原型，Python 3.11+ / Linux，运行与默认测试零第三方依赖。已实现持久任务/指令账本、lease/fencing、独立合成 Runner、Mock/Claude Code Worker、Session 轮换、JSON CLI、Unix RPC、MCP，以及版本化故障库、流式检测、离线重放和持久 Incident 分级恢复。当前 Runner 仅执行合成实验；真实 GLM、GPU/NPU 与远程传输仍待接入。

## 立即验证

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator demo
python3.11 -m unittest discover -s tests -v
```

`demo` 在隔离临时目录启动真正的专用子进程，向 Worker 发送 SIGKILL，然后验证新 generation 接管、原 Runner PID 不变、只有一个 Job 和一次 execution，最后返回 JSON 与合成结果。它不使用真实模型或计算资源。

本轮验收见 [v0.3 检测与恢复报告](docs/FAILURE_VALIDATION.md)，此前基础恢复链路见 [测试计划](docs/TEST_PLAN.md) 和 [首轮交付报告](docs/DELIVERY.md)。

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
SUI_TEST_REAL_CLAUDE=1 python3.11 -m unittest tests.test_claude.InstalledClaudeTests tests.test_stream_capability -v
```

安装器获取官方原生包并校验 integrity，不需要 npm、Node 或 root。已实测版本 2.1.316。上述 4 项测试使用真实 CLI + 本地假 Messages API + 本项目 MCP，仅用合成 API key，无需真实 API；包含响应结束前的增量接收与重复生成拦截。默认套件明确跳过这 4 项，设置 opt-in 后运行。

在真实平台提供批准的模型环境配置后可 `submit ... --adapter claude`。默认只开放受管 MCP，禁用内置 Shell；完整代码编辑、真实训练 Runner 和内部 GLM 的端到端兼容性尚未验证。详见 [Claude Adapter](docs/CLAUDE_ADAPTER.md)。

## 安装为命令

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/yan demo
```

源码运行不需要安装。`yan schema` 输出机器接口 Schema；`scripts/export_schemas.py --check` 检查 Schema 同步。`yan backup /private/backup.sqlite3` 生成一致、私有的 SQLite 备份，不覆盖既有文件。

## 故障检测与恢复

13 条版本化规则、40 条故障/正常轨迹可完全离线重放：

```bash
PYTHONPATH=src python3.11 -m sui_regulator failures list
PYTHONPATH=src python3.11 -m sui_regulator failures replay tests/failure_corpus/positive/generation.jsonl --summary
PYTHONPATH=src python3.11 -m sui_regulator failures evaluate tests/failure_corpus --summary
# 在线查询已保存的 Incident，不依赖模型在线：
PYTHONPATH=src python3.11 -m sui_regulator --home .sui rpc yan.get_incidents --params '{"task_id":"TASK_ID","after":0,"limit":100}'
```

检测覆盖单次/跨轮重复、非法结构与空 Bash、相同失败结果循环、传输错误/断流/静默、进程/lease、会话冲突、进展和未知异常。严重生成退化可换新 Session；环境错误进入 WAITING，原因不明的持续工具失败隔离，无进展先只读探测。恢复保留原 Task 和运行 Job，受规则与全局预算约束；结果不明的 Job 阻止自动接管。

当前回归集召回率 23/23，正常案例误报 0/17；这不代表真实 GLM 的表现。真实 CLI 已证实 text_delta 级观察，未收到增量时查询仍显示 event-only；不宣称 tokenizer 级逐 token 监控或自动判断语义质量。

设计与复用依据见 [故障调查](docs/FAILURE_RESEARCH.md)、[检测架构](docs/FAILURE_ARCHITECTURE.md)；新增问题见 [规则与案例贡献步骤](docs/FAILURE_CONTRIBUTING.md)、[Incident Corpus](tests/failure_corpus/README.md)。

## 文档

- [原始需求](docs/REQUIREMENTS.md)、[上游源码与复用判断](docs/REUSE_REVIEW.md)、[环境核验](docs/ENVIRONMENT.md)
- [架构](docs/ARCHITECTURE.md)、[数据模型](docs/DATA_MODEL.md)、[Sui Protocol](docs/SUI_PROTOCOL.md)、[Yan API](docs/YAN_API.md)
- [恢复策略](docs/RECOVERY_POLICY.md)、[安全边界](docs/SECURITY.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)
- [交付报告](docs/DELIVERY.md)、[测试原始输出](docs/test-output.txt)、[结构化进度](docs/PROGRESS.json)
- [v0.3 验收报告](docs/FAILURE_VALIDATION.md)、[离线案例报告](docs/failure-corpus-report.json)、[本轮测试输出](docs/failure-test-output.txt)、[结构化验收](docs/failure-verification.json)

MIT License。开发测试只操作专用进程和合成数据，没有启动/终止现有训练、修改宿主机配置或上传公司数据。
