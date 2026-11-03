# Yan Console · 本地管理台

Yan Console 是大炎入口的本地 Web 管理界面，用来观察 Sui Proxy（岁兽代理人）、持久 Task、独立 Job、Incident 与秉烛人的自动恢复。Console 是独立进程，通过已有 Unix Yan RPC 查询与提交指令，不打开 SQLite，不启动/终止代理或 Runner。

在另一台服务器克隆后做持久部署，请先读 [部署与验收指南](DEPLOYMENT.md)，其中包含独立 release、用户服务、开机/退出登录、SSH 转发和目标机验收步骤。下文的源码运行命令用于开发与演示。

## 启动

Python 3.11+ / Linux，运行时只有标准库，前端是随包安装的原生 HTML/CSS/JavaScript，无 CDN、npm 或构建步骤。

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator --home .sui init
python3.11 -m sui_regulator --home .sui serve
```

另一终端：

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator --home .sui console --port 8765
```

浏览器打开 **http://127.0.0.1:8765**。也可通过已安装的 `yan --home .sui console --port 8765` 启动。Console 可先于 Controller 启动，显示等待连接；退出 Console 不会停止 Controller、Sui Proxy 或 Job。

## 无模型演示

```bash
PYTHONPATH=src python3.11 -m sui_regulator console-demo --port 8765
# 可选保留演示账本，目录必须不存在：
PYTHONPATH=src python3.11 -m sui_regulator console-demo --port 8765 --state-dir /tmp/yan-console-new-demo
```

演示创建私有新目录、独立 Controller 和独立 Console，全部为合成数据，不使用 Claude 或模型 API。就绪时输出 URL、目录与场景 Task ID。包含：

1. “跨代际连续性验证”：第一代代理连接 Job 后注入真实检测引擎可识别的文本重复，产生 GEN-001；第二代接管同一 Task/Job，execution 仍为一条。
2. “合成指标稳态观测”：真实专用合成 Runner 持续产生 step/loss，详情绘制实际采样点。
3. “未知作业结果核验”：只终止演示创建的专用 Runner，账本核验为 UNKNOWN；不会启动第二份 Job。
4. 已完成与暂停待指令的 Task，便于观察历史状态及人工 Resume。
5. 演示将最大代理年龄设为 30 秒，可观察正常 Handoff 与代际轮换；运行 Job 保持独立。

演示结束 Ctrl+C 会清理**该演示自己登记的专用进程**。这是演示工具的显式清理行为；普通 Console 的退出从不清理代理或 Job。工具拒绝使用任何已存在的状态目录。

## 四个页面

| 页面 | 内容 |
|---|---|
| Overview / 总览 | 全局真实计数、最近 Incident、控制事件、Candleholder 最近完成 tick 与进程信息 |
| Sui Proxies / 代理会话 | 包含历史代际的分页列表、身份、Task、Generation、lease/心跳、进程冻结/失联、运行时长；详情含最后活动、Handoff、最近故障和换代历史 |
| Tasks / 持久任务 | Desired、Declared（未核验 AI 声明）、Observed（Runner 观测）分别呈现；Job/PID、execution 数、指标、事件及人工干预 |
| Incidents / 故障事件 | 规则版本、证据与来源；检测建议、最终动作、应用结果/outcome 分开显示 |

列表按新记录优先分页，每页 20 条，搜索和状态筛选明确作用于本页。历史事件按 Event ID 增量读取，界面最多保留已加载的最近 500 条，可继续加载后续事件；Incident 历史通过已有游标接口查询。底层 get_task 的完整关联记录仍受既有 RPC 响应大小上限约束，过大时显示查询错误，不会静默省略或伪造记录。

## 人工干预与回执

Task 详情提供 Hold、Resume、追加指令，复用已有 Command API。Hold 必须在对话框明确勾选确认；它停止当前 Sui Proxy，保留已启动的 Job。Resume 恢复 Task 的 ACTIVE 意图，由 Candleholder 协调后续代理与已有 Job。追加指令的 SUCCEEDED 只表示要求被记录，不声称 AI 已经完成分析。

每次提交生成稳定幂等键，收到真实 Command ID 后才能展示真实 QUEUED/最终回执。送达未确认时保留原键，只有用户明确重试才再次提交，禁止自动换键重放。回执在本浏览器保留最近 20 条；完整历史仍在账本 Event 中。没有直接杀进程、手动强制轮换、真实训练终止、任意 Shell 或聊天接口。

## 刷新与离线

默认 5 秒，可选择 2/10/30 秒；同一页面不重叠刷新，失败退避最多 30 秒，隐藏标签页暂停，用户可暂停与立即刷新。服务端读取有 2 秒缓存、单次在途合并、有限线程与 8 MiB/64 项缓存上限。所有快照保留原始查询/成功时间。

Controller 健康来自**最近完成的主循环 tick + 已登记进程身份**，RPC 线程有响应不等于在线。Console 无法联系 Controller 时显示离线与最后更新时间，保留已有快照，所有人工操作停用。暂停、超时或数据未核验时不保留绿色实时状态；前端独立检查刷新期限，避免请求挂起后无限显示在线。Job UNKNOWN 不会被显示为完成或正常训练；冻结/失联代理与历史退出分别显示。

## 本地与 SSH 访问边界

服务固定绑定 127.0.0.1，没有公网 Host 参数。仅接受 localhost/127.0.0.1 Host；RPC POST 要求同源 Origin、JSON 与专用请求头，不开放 CORS，禁止页面嵌入，限制请求大小和并发。数据按文本转义，不执行指令、日志、Handoff 中的 HTML。

经授权的 SSH 转发示例，在自己的电脑执行：

```bash
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 YOUR_AUTHORIZED_HOST
```

然后打开本机 http://127.0.0.1:8765。可将前一个端口换成其他本机端口；同源检查兼容不同的 loopback 转发端口。没有新建用户认证或 SaaS 身份体系：本机可信进程和拥有转发权限的人能够使用 Console，同源检查不是多用户身份隔离。不得把控制端口反向代理到公网。

## 验证与截图

```bash
PYTHONPATH=src python3.11 -m unittest tests.test_console tests.test_proxy_compat -v
# 可选浏览器开发依赖，仅用于验收；运行 Console 不需要它：
.venv/bin/python -m pip install playwright
PYTHONPATH=src .venv/bin/python scripts/verify_console_browser.py --browser /usr/bin/chromium-browser
```

浏览器测试使用实际 Chromium、真实 HTTP/RPC、独立合成进程和实际故障注入；不拦截 API 返回假状态。它检查控制回执、相同 Job/PID 与单次执行、刷新暂停、冻结代理、UNKNOWN、Controller 离线、移动视口、文本转义和无外部页面请求。报告见 console-browser-report.json；截图位于 screenshots/。

最终测试、安装包验证和验收映射见 [v0.4 交付报告](YAN_CONSOLE_DELIVERY.md)，原始输出见 console-test-output.txt，结构化结果见 console-verification.json。浏览器的最近回执按账本标识隔离，切换工作目录不会把旧命令当作当前账本回执。
