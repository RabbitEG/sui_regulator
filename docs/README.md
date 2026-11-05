# 文档目录

[返回中文首页](../README.zh-CN.md) · [English](../README.md)

项目首页分为英文 `README.md` 与中文 `README.zh-CN.md`，两版分别提供项目介绍、架构和快速开始；这里收录完整的安装、部署、状态语义、接口和验收证据。项目世界观与中英文叙事独立收录于 [LORE.md](LORE.md)。当前版本为 v0.4，统一会话角色名称为 **Sui Proxy（岁兽代理人）**。

当前规范以 [职责基线](RESPONSIBILITY_BASELINE.md) 为准；本轮实现与验收状态见 [迭代映射](RESPONSIBILITY_ITERATION.md)。历史交付报告不替代规范。

## 从这里开始

| 需要做什么 | 阅读入口 |
|---|---|
| 了解能力、运行 Mock、安装和测试 | [项目概览与本地验证](PROJECT_OVERVIEW.md) |
| 在另一台服务器部署、验收、备份或回滚 | [部署与验收指南](DEPLOYMENT.md) · [systemd 用户服务模板](../deploy/systemd/user/) |
| 一周无人值守、同代等待与十分钟静默策略 | [信任模式](TRUSTED_AUTONOMY.md) |
| 理解提示词结构、合作式换代与自动完成标记 | [长期 Agent 设计与参考](PROMPT_AND_LIFECYCLE_DESIGN.md) |
| 定义完整任务、启用代码调试、配置完成前核验 | [任务书与执行能力](TASK_BRIEFS.md) |
| 使用四个 Console 页面、人工干预、SSH 转发 | [Yan Console 使用与演示](YAN_CONSOLE.md) |
| 查看双语切换、Markdown、角色区分和故障位置 | [Yan Console 阅读体验更新与验收](YAN_CONSOLE_READING.md) |
| 调整阅读字号、查看代理对话、核对进程与工具返回 | [会话阅读器交付与验收](SESSION_READER_DELIVERY.md) |
| 理解世界观角色与兼容字段 | [命名体系](NAMING.md) |
| 阅读项目世界观与英文译文 | [Lore](LORE.md) |
| 查命名灵感、叙事与文档组织参考 | [资料与风格参考](LORE_AND_REFERENCES.md) |

## 接口与实现

| 主题 | 文档 |
|---|---|
| 进程边界、账本、所有权与副作用 | [整体架构](ARCHITECTURE.md) · [小型本地 Controller 决策](ADR/0001-small-local-controller.md) |
| Task / Command / Job 与持久关系 | [数据模型](DATA_MODEL.md) |
| 代理注册、进展、Job 意图与移交 | [Sui Proxy Protocol](SUI_PROTOCOL.md) |
| 查询、人工指令与回执 | [Yan API](YAN_API.md) |
| Claude Code 适配、本地假 API 与真实平台入口 | [Claude 适配](CLAUDE_ADAPTER.md) |
| 长期自主执行、重复观察、运行证据与恢复额度 | [Persistent 恢复](PERSISTENT_RECOVERY.md) |
| 故障规则、证据和恢复预算 | [检测架构](FAILURE_ARCHITECTURE.md) · [恢复策略](RECOVERY_POLICY.md) |
| 扩展故障规则和离线回归 | [故障贡献步骤](FAILURE_CONTRIBUTING.md) · [Incident Corpus](../tests/failure_corpus/README.md) |
| 本地可信边界与未开放能力 | [安全边界](SECURITY.md) |

## 验收与进度

- README 与界面素材整理：[验证记录](verification/console-cleanup/README.md)、[当前总览截图](screenshots/yan-console/)。世界观叙事收录于 [LORE.md](LORE.md)。

- Console 阅读体验：[阅读器交付](YAN_CONSOLE_READING.md)、[验证汇总](verification/yan-reader/summary.json)、[会话阅读截图](screenshots/yan-reader/)。前一轮 [界面改版记录](YAN_CONSOLE_REDESIGN.md) 保留为历史。

- 最新自主任务：[交付报告](AUTONOMY_DELIVERY.md)、[验证摘要](autonomy-verification.json)、[原始测试输出](autonomy-test-output.txt)、[安装包验收](autonomy-wheel-verification.json)。

- v0.4 Console：[交付报告](YAN_CONSOLE_DELIVERY.md)、[浏览器报告](console-browser-report.json)、[浏览器截图](screenshots/)、[验证摘要](console-verification.json)、[测试原始输出](console-test-output.txt)。
- 服务器部署：[私有目录安装与启动验证](deployment-verification.json)。systemd 服务只做过静态与等价启动测试，目标服务器仍须按指南验收。
- v0.3 故障与恢复：[验收报告](FAILURE_VALIDATION.md)、[验证摘要](failure-verification.json)、[离线语料结果](failure-corpus-report.json)、[测试原始输出](failure-test-output.txt)。
- 全局状态：[结构化进度](PROGRESS.json)。真实 GLM、GPU/NPU、远端授权和训练生命周期仍未验证；合成数据结果不代表真实平台检测效果。

## 设计依据与历史

以下文件保留阶段当时的范围、术语和测试数据；当前使用、架构和角色命名以上面的文档为准。

- v0.4：[Console 实施计划](YAN_CONSOLE_PLAN.md)。
- v0.3：[故障阶段需求](FAILURE_REQUIREMENTS_v0.3.md)、[故障调查](FAILURE_RESEARCH.md)。
- 首轮：[原始需求](REQUIREMENTS.md)、[实施计划](IMPLEMENTATION_PLAN.md)、[交付报告](DELIVERY.md)、[测试计划](TEST_PLAN.md)、[验证摘要](verification.json)、[测试输出](test-output.txt)。
- 项目起点：[复用调查](REUSE_REVIEW.md)、[环境记录](ENVIRONMENT.md)。
