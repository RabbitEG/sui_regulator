# Candleholder 恢复策略

独立 Python Controller 持有私有 flock 单实例锁。每个 tick：处理 Command → 协调 Job → 产生健康 Incident → 消费持久 Incident → 处理轮换/回收 → 按预算创建新 generation → 清理明确已撤销的受管 Sui Proxy。

| 证据 | 决策 |
|---|---|
| Sui Proxy 进程消失/退出、lease 过期 | 撤销 lease，记录故障，按持久预算/冷却重建 |
| 单次生成严重重复、连续非法结构/空 Bash | Incident 达阈值后撤销旧 lease，按规则及全局预算创建新 Session |
| 相似工具重复得到同一失败结果 | 原因未确认；先告警，持续失败后隔离，不推断换 Session 能修复外部工具 |
| 协议/Job 无进展超时、完成声明与 Job 冲突 | PROG-001 安全探测账本及受管 PID；不能仅据此推断模型失常并重启 |
| 正常等待用户/资源/外部依赖 | 保留任务，继续观测；不机械重启 |
| 最大 Session 年龄 | 请求 Handoff，有限等待后仅轮换 Sui Proxy |
| Runner 消失/启动确认不明 | UNKNOWN，禁止自动重新 dispatch |
| 结构化环境故障（EROFS/ENOSPC 等） | Task WAITING，保留运行 Job，无重启风暴；Job 自身资源阻塞仍为 BLOCKED |
| 有限传输失败 | 允许 CLI 已有的有限重试；终止失败才按退避创建新 Session，不注入 Continue 或重放工具 |
| CLI turn 预算耗尽（MAX_TURNS_EXHAUSTED） | 永不 complete 的循环任务（如巡检）按设计触顶；TURN-001 按规则预算直接轮换新 Session，durable context 恢复循环，不计为故障，不隔离 |
| 未知结构化异常 | UNKNOWN_ANOMALY，先探测；重复或明确致命则隔离，原因保持未确认 |
| Yan HOLD/CANCEL | 撤销 Sui Proxy 所有权，持久保留意图，运行 Job 保留 |
| 恢复次数达到窗口上限 | QUARANTINED，需用户 resume 后重新启用预算 |
| 未登记或身份不符进程 | 只观察，不扫描名称并杀进程 |

恢复计数由持久 WORKER_FAILED Event 计算；Controller 重启不清空。正常主动轮换不消耗故障预算。用户 resume 隔离任务是明确的恢复授权，会更新预算起点。取消是终态，不能 resume。

v0.3 同时从 incidents 表计算各规则的已执行 fresh_session 预算（默认每窗口 2 次）。最终动作可以比规则建议更保守：预算耗尽或存在 STARTING/UNKNOWN Job 时改为隔离。旧 Sui Proxy/generation 的 Incident 标记 STALE，持久 HOLD/CANCEL 优先，不能被恢复覆盖。撤销所有权、Task 状态、Incident 回执和审计在一个事务中落盘，之后才停止受管旧 Sui Proxy。Job 与执行账本不被该事务修改。

成功工具结果或真实用户新指令结束对应的工具/跨消息错误累计；成功完整响应结束传输错误累计。警告在新故障阶段可以重新产生。Incident 以规则版本、Session、动作与故障起点去重，数据库记录推荐动作与最终动作/原因。observe/warn 仅记录；probe 只读受管进程身份和账本，不启动新工具。WAITING 和 QUARANTINED 都需要明确 resume 才重新分配 Session。

进展观察使用去除 updated_at 的协议报告摘要及 Job 状态/观测指纹，单纯心跳、文本流活动或重复提交同一报告不刷新进展起点。正常等待与近期运行的 Job 有豁免。协议报告仍是 Sui Proxy 声明，所以超时只触发探测，不证明模型的语义质量下降。

受管 Sui Proxy 身份和最近心跳还写入私有 worker-registry，供数据库锁住时核验冻结进程。到期冻结 Sui Proxy 在数据库访问前回收，随后恢复审计。退出的受管 Sui Proxy 先 SIGTERM，超过 grace 后在身份核验基础上升级 SIGKILL。

进程身份使用 boot ID、PID、/proc start ticks。优先 pidfd 发信号；老内核无 pidfd 时使用身份检查再发信号，最后检查到 signal 间仍有很窄的 PID 复用竞态，见 SECURITY.md。首轮只针对合成进程测试；没有终止真实训练。

Controller 退出故意保留 Sui Proxy 和 Job；生产需用管理员批准的 OS/容器进程守护启动 Controller，首轮不自动安装服务。整个容器/主机丢失不能由本地进程 Runner 自动恢复执行：持久卷上的 Job 会显示 UNKNOWN，需真实平台 Adapter 核验。
