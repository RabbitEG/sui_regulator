# Sui Regulator（司岁台）

独立于 AI Session 的持久任务控制平面。Task 持久，Sui 可替换；实验 Job 的生命周期独立于 Worker。

首轮原型正在分阶段交付，进度见 [PROGRESS.json](docs/PROGRESS.json)。Python 3.11+，Linux，运行和基础测试不依赖外部包。

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator --home .sui init
python3.11 -m sui_regulator --home .sui submit '合成训练' --key demo-task
python3.11 -m sui_regulator --home .sui drain
python3.11 -m sui_regulator --home .sui list
python3.11 -m unittest discover -s tests -v
```

`submit` 返回持久 Command ID 和预分配的 Task ID，`drain` 应用纯数据库命令；后续 Controller 将自动处理队列。相同 actor/key 的不同请求会报幂等冲突。`receipt COMMAND_ID` 查询执行回执。真实 GLM、训练和远程网络尚未验证。

设计依据：[需求](docs/REQUIREMENTS.md)、[复用调查](docs/REUSE_REVIEW.md)、[架构](docs/ARCHITECTURE.md)、[数据模型](docs/DATA_MODEL.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)。
