# v0.4 Yan Console 交付与验收

已完成本地独立 Web 管理台，以及会话角色统一命名 **Sui Proxy（岁兽代理人）**。README 已更新整体介绍、世界观职责表、运行入口、截图与 Mermaid 架构图。原始需求和历史证据保留当时术语；v0.3 的数据、凭据、导入和旧 API 按 NAMING.md 保持兼容。

## 验收证据

| 用户验收项 | 实际验证 |
|---|---|
| 浏览全部受管会话 | 全局代理分页、历史代际、详情身份/Generation/心跳/活动/时长/关联 Task/Handoff；验证分页无重复 |
| 创建、失效、终止与换代 | 专用合成代理在 GEN-001 后撤销 lease，第二代接管；实际冻结代理在浏览器显示“进程冻结” |
| GEN-001 的证据与处理 | 同一生产检测引擎产生 text_delta 重复证据，建议、Candleholder 最终动作、APPLIED 状态和实际回执分列 |
| Task/Job 未重复 | 自动恢复和页面 Hold/Resume 后，Task/Job ID 与 Runner PID 保留，execution=1 |
| 人工干预有真实效果 | 浏览器确认/取消 Hold、Resume、追加指令、轮询真实 Receipt；SQL 账本核验对应状态和指令内容 |
| 离线/失联/UNKNOWN 语义 | 过期 tick 与可达 RPC 分开；冻结/失联代理不显示健康；Job UNKNOWN 明确禁止自动重启；Controller 离线保留原时间戳并停用操作 |
| Mock 完整演示 | console-demo 自动创建隔离 Controller/Console/代理/Runner，不安装 Claude、不需要 GLM；拒绝已有状态目录 |

Python 3.11 完整套件 **88 项通过**，包括 4 项已安装 Claude CLI + 本地假 API 合约测试。Python 3.12 **84 项通过、4 项明确跳过**。原始输出见 console-test-output.txt。真实 Chromium 133 的 **15 项浏览器检查通过**，无页面脚本错误或外部页面请求；11 张截图保存于 screenshots/，检查清单见 console-browser-report.json。

额外验证覆盖同源/Host/请求大小/方法限制、Console 退出不影响其他进程、命令回执丢失时不自动重放、查询过大不误报 Controller 离线、旧配置和旧 API 兼容、浏览器回执按账本隔离、恶意 HTML 作为文本显示、暂停刷新停止请求、移动视口不产生页面横向溢出。

## 运行与查看

```bash
# 一条命令启动独立的无模型演示：
PYTHONPATH=src python3.11 -m sui_regulator console-demo --port 8765
# 已有账本分别启动两个进程：
PYTHONPATH=src python3.11 -m sui_regulator --home .sui serve
PYTHONPATH=src python3.11 -m sui_regulator --home .sui console --port 8765
```

打开 http://127.0.0.1:8765。使用说明、SSH 转发和演示清理边界见 [Yan Console](YAN_CONSOLE.md)。[总览](screenshots/overview.png)、[代理详情](screenshots/proxy-detail.png)、[故障详情](screenshots/incident-detail.png)、[命令回执](screenshots/command-receipts.png)、[离线](screenshots/controller-offline.png)、[UNKNOWN](screenshots/job-unknown.png)、[移动视口](screenshots/mobile.png) 均为真实浏览器截图。

## 交付范围与接口

- 原生静态前端和 Python 标准库 HTTP 进程，固定绑定 127.0.0.1；不注册托管站点，不开放公网端口。
- 一条新增只读分页查询 get_console_snapshot，复用原有 Task/Job/Incident/Event/Receipt；get_proxy_health 是既有健康查询的规范命名别名。
- Controller 内存记录最近完成 tick；SQLite 仍为 schema v3，没有新迁移，也没有更改核心恢复状态机。Incident outcome 补充已落盘的 lease 撤销、代理身份、Task 状态和保留 Job 信息。
- Hold/Resume/Instruct 复用既有幂等 Command，Console 不直接控制进程；本轮不提供手动强制轮换或真实训练终止。
- SuiProxyAdapter/ProxyRegistry/proxy_process 等规范入口与旧别名并存，max_proxies 与旧 max_workers 配置均经测试，冲突配置明确拒绝。

v0.4.0 wheel 已构建、安装，并从 /tmp 在清除 PYTHONPATH 后运行；四个静态资源、13 条故障规则及兼容代理入口随包保留，静态资源内容与源码逐字节相同。安装后的 Console 演示、Unix RPC、GEN-001 接管、独立 Job 和既有 SIGKILL 恢复演示 均通过。结构化结果及哈希见 console-verification.json。

这仍是本地单 UID 原型。没有新增多用户认证，可信本机与经授权 SSH 是访问边界；没有验证真实 GLM、企业网络、GPU/NPU、真实训练 Runner、分布式 Controller 或浏览器之外的平台。模拟与合约测试通过不证明这些环境已兼容。

开发按 A（独立后端与投影）、B（界面/命名/演示/浏览器）、C（打包与交付）提交并 push；具体状态见 PROGRESS.json，Author 为 GPT-6.1 Sol。
