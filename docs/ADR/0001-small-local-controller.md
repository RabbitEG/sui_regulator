# ADR 0001：独立本地控制平面

日期：2026-11-02。决定：首轮独立 Python Controller + SQLite，不 fork Gas City/AgentPulse，不引入 Temporal 服务。

依据见 [复用评估](../REUSE_REVIEW.md)。决定不是因为上游使用非 Python，而是本阶段最难验证的是独立实验生命周期、跨 Worker 的逻辑作业去重、持久 fencing 和崩溃窗口；现有通用 Agent 系统不能直接提供本项目的全部语义。

代价：自行维护事务、进程身份、迁移和故障测试。通过限制为 Linux 单 Controller、本地 Runner 和可替换接口控制范围。未来已使用 Ray/SkyPilot 的平台优先接入其 Job API；当跨机工作流成为主要复杂度时重新评估 Temporal。

不复制上游源码；复用公开 CLI、操作系统能力和 SQLite。真实 API/GLM 兼容性不能由 Mock 成功推断。
