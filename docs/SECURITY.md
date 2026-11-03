# 安全边界

威胁模型：单个 Linux UID 下的本地控制平面，处理可能错误的 AI 声明和过期请求。它不是针对已取得同 UID 任意代码执行权的恶意进程的沙箱，也不是多租户远程服务。

- 状态目录由当前 UID 拥有，权限 0700；数据库、socket、备份和合成产物采用私有权限；拒绝状态目录/数据库的直接符号链接。不要将 home 指向共享目录或源码根目录。
- Yan RPC 仅 Unix socket，SO_PEERCRED 验证 UID；CLI/MCP owner 角色继承本地操作员权限。Yan Console 仅绑定 127.0.0.1，可经授权 SSH 转发；同源保护不是多用户鉴权。独立远程身份、TLS/RBAC 与公网 RPC 没有实现。
- Sui Proxy token 随机生成，数据库只存 hash；generation 和 lease 在领域事务与 Runner 执行端校验。Sui Proxy 无 register/抢占工具，Task 取消不能自动复活。
- Claude 默认 bare、strict MCP、禁用内置 tools 和 slash commands，不加载仓库自定义 hooks，不允许任意 Shell，也不使用 dangerously-skip-permissions。没有自动读取或上传内部数据。
- 仅提供受管合成 Job 的显式取消并在 Runner 复核身份。真实训练启动/终止、抢占其他用户资源、宿主机修改等操作未开放。因此首轮没有以「提示词审批」冒充执行端 Approval；未来开放时必须提供真实授权对象和执行端验证。
- 任务文本、日志和 Handoff 只保存为数据；模型自述保留 worker-declared 来源，不直接覆盖 Runner 观测。
- 进程清理只针对登记身份（boot ID/PID/start ticks），从不按 claude/codex 名称扫描。优先 pidfd；当前老内核不支持 pidfd，fallback 最后检查到 signal 仍有窄的 PID 复用竞态。生产平台建议支持 pidfd 或使用受管 cgroup/作业服务。
- CLI 子进程绑定 Wrapper 的 parent-death signal，实验 Job 独立；取消 Task 不等于授权杀训练。
- SQLite busy timeout、命令/Job 幂等、持久恢复预算与隔离限制重试。进程内 JSON/流读取有大小上限。审计仍需操作员设置保留与归档策略，不能保证无限磁盘容量。

秘密只放在操作员批准的环境或私有配置中，不提交 `.env`、数据库、真实日志、API key、权重与数据。安装器仅下载公开官方包，校验 SHA-512，但不把包 registry 的 integrity 当作独立供应链签名证明。

代理会话记录位于状态目录的 `transcripts/`（0700），文件为 0600；只读 Yan 查询验证 Task/Proxy 归属，并拒绝符号链接和非普通文件。记录任务输入、可见助手输出和工具轨迹，不保存 thinking；已知环境凭据、lease token 和常见秘密字段会被替换。对话可能包含业务内容，与账本一样只供可信本机用户和经授权的 SSH 转发使用，备份/归档时按私有运行数据处理。记录不进入 Incident、恢复提示词或 Git；每代达到 4 MiB 后显式停止采集。采集失败不停止 Controller、Sui Proxy 或 Job。
