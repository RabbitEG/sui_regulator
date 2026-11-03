# 文档目录

[返回项目首页](../README.md)

README 是项目门面；安装、部署、状态语义、接口和验收证据以这里的工程文档为准。当前版本为 v0.4，统一会话角色名称为 **Sui Proxy（岁兽代理人）**。

## 从这里开始

| 需要做什么 | 阅读入口 |
|---|---|
| 了解能力、运行 Mock、安装和测试 | [项目概览与本地验证](PROJECT_OVERVIEW.md) |
| 在另一台服务器部署、验收、备份或回滚 | [部署与验收指南](DEPLOYMENT.md) · [systemd 用户服务模板](../deploy/systemd/user/) |
| 使用四个 Console 页面、人工干预、SSH 转发 | [Yan Console 使用与演示](YAN_CONSOLE.md) |
| 调整阅读字号、查看代理对话、核对进程与工具返回 | [会话阅读器交付与验收](SESSION_READER_DELIVERY.md) |
| 理解世界观角色与兼容字段 | [命名体系](NAMING.md) |
| 查《明日方舟》典故和 README 风格出处 | [资料与风格参考](LORE_AND_REFERENCES.md) |

## 接口与实现

| 主题 | 文档 |
|---|---|
| 进程边界、账本、所有权与副作用 | [整体架构](ARCHITECTURE.md) · [小型本地 Controller 决策](ADR/0001-small-local-controller.md) |
| Task / Command / Job 与持久关系 | [数据模型](DATA_MODEL.md) |
| 代理注册、进展、Job 意图与移交 | [Sui Proxy Protocol](SUI_PROTOCOL.md) |
| 查询、人工指令与回执 | [Yan API](YAN_API.md) |
| Claude Code 适配、本地假 API 与真实平台入口 | [Claude 适配](CLAUDE_ADAPTER.md) |
| 故障规则、证据和恢复预算 | [检测架构](FAILURE_ARCHITECTURE.md) · [恢复策略](RECOVERY_POLICY.md) |
| 扩展故障规则和离线回归 | [故障贡献步骤](FAILURE_CONTRIBUTING.md) · [Incident Corpus](../tests/failure_corpus/README.md) |
| 本地可信边界与未开放能力 | [安全边界](SECURITY.md) |

## 验收与进度

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
