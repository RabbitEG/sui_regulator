# 职责基线迭代：实现映射与验收

规范入口：[职责基线](RESPONSIBILITY_BASELINE.md)。实际开始基线：`30f0242ba4675d5261adf8cf0e744196543b7ae8`，2026-11-04 已 fetch 核对 origin/main，工作区干净。本文区分已有实现、实施中与验收结果；不是通过声明。

## 实施映射

| 要求 | 当前路径 | 本轮处理 | 状态 |
|---|---|---|---|
| A01–A06、A12–A16 | recovery / Controller / completion / task_prompt | 保留自主调试、Persistent、合作式换代与可选验收，引用并补充职责回归 | 待验收 |
| A07–A11 | attempts / tool_hook / claude_process / commands | 区分后台运行与 UNKNOWN；身份关联、通知乱序、受限诊断与自动核验 | 实施中 |
| A17、A20 | Service / Protocol / Hooks / Console | 追加指令的持久化、交付、显式读取确认分开记录 | 实施中 |
| A18–A19 | fencing / Controller / completion | 保留用户控制优先和过期代际拒绝 | 待验收 |
| A21 | get_task_context / task_prompt / Gateway | 有界上下文、分页历史、未决记录和完整要求引用 | 实施中 |
| A22 | store / schemas / deployment | 可逆部署安排、备份、迁移保留权限与旧数据、快进推送 | 实施中 |
| 等待与唤醒 | Protocol / Controller / Console | 持久记录等待对象、责任方和下次检查，明确可用唤醒范围 | 实施中 |
| 运行长期服务后完成 | completion | 审计现有终态保护；无可靠责任转移契约则明确不支持，不直接解除保护 | 待审计 |
| 观察措辞 | recovery / Console / docs | 展示运行证据更新，不将其解释为业务进展 | 实施中 |

## 提交阶段

1. 规范与审计映射（本文）；不声称新增运行能力。
2. 操作状态与可用诊断路径，通知身份、乱序和停止语义。
3. 指令交付、等待/接续与有界上下文，Console、兼容迁移与最终证据。

每阶段提交并 push；不重写其他作者历史、不 force push。真实 GLM/目标服务器无凭据，继续做确定性、真实 CLI + loopback 与浏览器验收，真实环境单列待验收。
