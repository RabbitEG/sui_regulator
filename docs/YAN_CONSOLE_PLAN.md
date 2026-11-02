# v0.4 Yan Console 实施依据

基线：v0.3，提交 de08b70，77 项测试通过。目标是在本地观察 Session、Task、Job、Incident 和自动恢复，复用控制平面，不扩展科研执行能力。

## 接口盘点与最小增量

已有 get_task 提供 desired/declared、Job observed、workers、Handoff、最近 20 条 Incident；get_incidents/get_events 提供游标历史；get_metrics/get_logs 提供 Runner 指标和有界日志；hold_task/resume_task/instruct_task + get_command_status 已有幂等请求和可查询回执。

只增加一个只读分页查询 get_console_snapshot(section, offset, limit)，用于跨 Task 的全局列表/计数及最近事件。Controller 记录最近一次完成 tick 的内存时间戳，查询同时验证 PID 身份；RPC 线程有响应不被当成主循环健康。没有新的数据库表/迁移、控制指令或 Session 强杀接口。

Console 为独立进程，固定绑定 127.0.0.1，通过已有 Unix RPC 访问账本，不创建 Service/SQLite 连接，不启动或终止 Controller/Worker/Job。退出 Console 不影响其他进程。

## 页面与数据语义

- Overview：真实计数、最近故障/事件、Candleholder tick、数据更新时间。
- Sessions：所有代际分页；身份、Task、generation、心跳、最后活动、运行时长、PID 观察、Handoff 和换代历史。
- Tasks：Desired、AI Declared 和 Runner Observed 分别呈现；Job、指标、历史事件、命令回执与人工干预。
- Incidents：规则/证据、检测建议、最终动作、处理状态和 outcome 分开呈现。

原生 HTML/CSS/JavaScript，无前端框架、CDN 或运行时依赖。有限刷新、单次在途、隐藏页面暂停、用户暂停/手动刷新、离线快照与错误提示。离线或暂停时不保持绿色实时状态，自动操作不可用。Hold 需要明确确认。界面不提供直接杀进程、任意 Shell、聊天或真实训练取消。

## 开发与推送阶段

A：只读投影、Controller tick、独立 HTTP/RPC 适配、来源检查和后端测试。
B：四个页面、真实命令回执、隔离 Mock 演示、浏览器交互和截图。
C：全套回归、离线/未知状态验收、安装包、运行/SSH/演示文档及交付。

每阶段验证后 commit + push 到 origin/main，Author 为 GPT-6.1 Sol，用户身份 RabbitEG。仅测试私有状态目录、专用进程和合成数据。
