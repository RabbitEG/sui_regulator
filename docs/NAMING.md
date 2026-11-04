# 司岁台的命名体系

当前约定采用《明日方舟》大炎、司岁台与岁兽的意象，同时保留 Task、Command、Job 等明确的工程语义。

名称的资料出处与工程改编边界见 [资料与风格参考](LORE_AND_REFERENCES.md)。下表描述本项目的职责分工；运行入口、完整架构和部署文档见 [文档目录](README.md)。

| 世界观名称 | 英文名称 | 工程职责 |
|---|---|---|
| 司岁台 | Sui Regulator | 持久任务控制与编排系统，维护账本、所有权和恢复策略 |
| 大炎 | Yan | 人与外部工具提交指令、查询事实的入口；Yan Console 是它的本地 Web 管理台 |
| 岁兽代理人 | **Sui Proxy** | 一个可替换的 AI 执行会话实例，领取 Task、声明进展、连接已有 Job；不拥有 Task 的生命周期 |
| 秉烛人 | Candleholder | 不依赖 LLM 的独立监控与恢复组件，执行检测、撤销旧 lease 和分配新代理 |
| 任务 | Task | 跨代理代际持续存在的目标、用户意图与执行关系 |
| 敕令 | Edict / Command | 可幂等提交、查询 Receipt 的人工指令；内部统一称 Command |
| 试验 | Trial / Experiment / Job | Trial 是展示意象；Experiment 是实验定义，Job 是由独立 Runner 管理的一次具体运行 |
| 案卷 | Dossier | Task 的历史、事件、Incident、产物和交接记录的集合意象，不新增重复存储 |
| 移交 | Handoff | 代理更替时的结构化交接信息，缺失时仍可从账本恢复 |

**Sui Proxy（岁兽代理人）是会话角色的统一名称。** 不再将该角色直接称为 Sui、Sui Worker 或 AI Worker。Session 仅表示代理所承载的会话实例/底层运行时概念，Generation 表示同一 Task 的代理所有权代际；它们不是新的管理角色。Sui 不再单独指一个可销毁的工作进程。

## Console 展示名称

| 界面栏目 | 对应工程概念 |
|---|---|
| 值守总览、秉烛值守 | Overview 与 Candleholder / Controller 的运行观察 |
| 代理名册、会话实录 | Sui Proxy 列表，以及指定代际采集的对话和工具记录 |
| 任务案卷 | Task 及其 Dossier；案卷不是另一种任务实体 |
| 敕令回执 | Command Receipt 与追加要求的投递记录；已读声明不代表执行完成 |
| 故障案录、值守纪事 | Incident 和审计 Event；展示名称不改规则 ID 或事件类型 |

中文栏目承接 README 的官署、案卷与值守意象，英文仍使用既有 Sui Proxy、Task、Command、Incident、Handoff 等术语。Desired / Declared / Observed、UNKNOWN、离线与确认操作保留明确含义。界面配色、素材和阅读安排见 [Console 设计记录](YAN_CONSOLE_REDESIGN.md)。

## 代码与协议兼容

当前规范入口为 SuiProxyAdapter、MockSuiProxyAdapter、UnavailableSuiProxyAdapter、ClaudeProxyAdapter、ProxyRegistry、ProxyCredentials、SuiProxyProtocol、proxy_process 和 `yan.get_proxy_health`。配置示例使用 `max_proxies`；旧配置的 `max_workers` 仍可读取，同时设置两者会明确拒绝。

为保持 v0.3 持久数据、外部客户端和审计历史可用，以下已有名称作为兼容字段/入口保留，不代表产品中仍有第二种 Worker 角色：

- SQLite 的 workers 表、worker_id/current_worker/authorized_worker 等列，以及 `WORKER_*` 事件类型不迁移、不重写历史。
- 现有 RPC 的 workers 数组、worker_id 凭据和 `get_worker_health` 仍有效；新健康查询返回相同代理身份，Task 另提供 current_proxy，代理详情提供 proxy_id。
- 旧 Adapter/WorkerRegistry/Credentials 导入是规范实现的别名；worker_process 是调用 proxy_process 的兼容入口。
- 既有私有 worker-registry 目录、SUI_WORKER_CREDENTIALS 环境变量与内部 bootstrap 字段保留，以免破坏运行中代理的恢复关系。
- 控制台 Sessions 的兼容路由和分页 section 仍为 sessions，导航为“代理名册 / Sui Proxies”，列表标题为“代理会话 · 名册”。

原始需求、历史交付报告、测试原始输出和故障轨迹保留当时术语作为历史证据；新的项目介绍、界面与操作文档遵循本约定。命名更新不改变 lease、generation fencing、幂等键、Runner 授权或恢复状态机。
