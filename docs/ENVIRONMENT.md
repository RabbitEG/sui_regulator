# 环境核验

核验日期：2026-11-02。只做只读探测，没有改宿主机配置、启动容器或占用 GPU。

| 项目 | 实测 |
|---|---|
| OS | Linux x86-64；PID 1 是 systemd；未发现常见容器标记，不能假定当前就是持久容器 |
| Python | 默认 python3 为 3.6.8；python3.11 为 3.11.13，另有 python3.12；项目要求 3.11+ |
| SQLite | Python 3.11 链接 SQLite 3.26.0，支持 WAL；不使用 RETURNING 等较新语法 |
| Python 依赖 | 无 pytest/FastAPI/Pydantic；首轮运行与测试采用标准库，不需要全局 pip |
| Agent | Codex CLI 0.141.0：`exec --help` 确认非交互入口；没有发起模型调用 |
| Claude Code | 初始没有。用户已要求安装并适配；官方 install.sh 返回 403，改用官方 npm 的原生平台包，安装结果见交付报告 |
| 进程管理 | tmux 2.7；systemd user 状态 degraded，不能假定其守护可用 |
| 容器 | Docker daemon 只读版本查询成功（29.1.3）；不启动/修改任何容器 |
| 计算资源 | nvidia-smi 可查询设备；不申请、抢占或终止真实计算任务 |
| 调度器 | 未发现 srun/sbatch/ray/sky 可执行文件 |
| 上游测试工具 | 无 Go、Rust、Bun、Node、bats；Python 标准库测试可运行 |
| Git | 已配置 origin/main，按用户要求每阶段 commit + push |

仍为 **Unknown / 未验证**：内部 GLM 地址、API dialect/凭据、Claude 的内部模型配置、真实 Session 恢复/导出、VS Code Extension 的外部控制、容器重建后的卷与 PID namespace、训练命令/指标格式、真实 GPU/NPU 作业、远程网络和数据传输许可。

首轮只用合成输入、独立私有状态目录和专用子进程。没有向模型服务发送仓库/公司数据。安装二进制与公开源码调查属于用户授权的开发工作；真实平台联调另行进行。
