# 实施与提交计划

每完成一个阶段，先验证、更新进度、Git commit，然后 push 到 origin/main；push 失败时明确记录，不把本地提交说成已上传。

| 阶段 | 交付 | 完成门槛 |
|---|---|---|
| 0 调查与设计 | 需求归档、上游源码/测试评估、环境核验、架构 ADR | 固定源码版本；区分实测/Unknown；commit + push |
| 1 持久任务核心 | 项目骨架、迁移、Task/Command、审计、JSON CLI、基本 Schema | 幂等冲突、事务、持久恢复和备份测试；commit + push |
| 2 受管执行与恢复 | Sui Protocol、lease/fencing、独立 Mock Runner、Mock Worker、Candleholder | 强杀 Worker 后新 Worker 接管，只有一次 Job 执行；commit + push |
| 3 Claude 与 Gateway | 安装 CC、Claude Adapter、MCP、Unix RPC、本地 API stub | 无真实 API 的 CLI 合约/流错误/工具事件与授权测试；commit + push |
| 4 故障验收与交付 | T01–T16 映射、竞争/崩溃测试、示例、同步文档、交付报告 | 全套测试与可运行演示；明确真实平台待测事项；commit + push |

后续：真实 GLM 与长训练/GPU/NPU 联调；认证远程传输；多机 Runner；依赖调度；结构化训练解析；可选 UI。没有用户授权不接入公司网络、终止真实训练或改宿主机配置。

v0.3 按 A 调查/流式能力、B Registry/Corpus/Replay、C 持久 Incident 与分级恢复、D 真实 CLI 故障/打包/验收四阶段完成；每阶段单独提交并 push。范围和结果见 FAILURE_REQUIREMENTS_v0.3.md、FAILURE_VALIDATION.md 和 PROGRESS.json。真实科研执行能力没有在本轮扩展。
