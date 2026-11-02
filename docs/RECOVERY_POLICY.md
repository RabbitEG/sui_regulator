# Candleholder 恢复策略

独立 Python Controller 持有私有 flock 单实例锁。每个 tick：处理 Command → 协调 Job → 观察受管 Worker → 处理轮换/回收 → 按预算创建新 generation → 清理明确已撤销的受管 Worker。

| 证据 | 决策 |
|---|---|
| Worker 进程消失/退出、lease 过期 | 撤销 lease，记录故障，按持久预算/冷却重建 |
| 连续工具异常达到阈值 | 归为 Worker 异常，保留实验 Job，按预算恢复 |
| 无有效活动超时 | 没有正常等待声明或最近可信 Job 进展时归为 STALLED |
| 正常等待用户/资源/外部依赖 | 保留任务，继续观测；不机械重启 |
| 最大 Session 年龄 | 请求 Handoff，有限等待后仅轮换 Worker |
| Runner 消失/启动确认不明 | UNKNOWN，禁止自动重新 dispatch |
| 资源暂时不可用 | BLOCKED，保留原 Job，无重启风暴 |
| Yan HOLD/CANCEL | 撤销 Worker 所有权，持久保留意图，运行 Job 保留 |
| 恢复次数达到窗口上限 | QUARANTINED，需用户 resume 后重新启用预算 |
| 未登记或身份不符进程 | 只观察，不扫描名称并杀进程 |

恢复计数由持久 WORKER_FAILED Event 计算；Controller 重启不清空。正常主动轮换不消耗故障预算。用户 resume 隔离任务是明确的恢复授权，会更新预算起点。取消是终态，不能 resume。

进程身份使用 boot ID、PID、/proc start ticks。优先 pidfd 发信号；老内核无 pidfd 时使用身份检查再发信号，最后检查到 signal 间仍有很窄的 PID 复用竞态，见 SECURITY.md。首轮只针对合成进程测试；没有终止真实训练。

Controller 退出故意保留 Worker 和 Job；生产需用管理员批准的 OS/容器进程守护启动 Controller，首轮不自动安装服务。整个容器/主机丢失不能由本地进程 Runner 自动恢复执行：持久卷上的 Job 会显示 UNKNOWN，需真实平台 Adapter 核验。
