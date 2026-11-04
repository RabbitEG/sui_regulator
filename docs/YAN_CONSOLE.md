# Yan Console · 本地管理台

Yan Console 是大炎入口的本地 Web 管理界面，用来观察 Sui Proxy（岁兽代理人）、持久 Task、独立 Job、Incident 与秉烛人的自动恢复。Console 是独立进程，通过已有 Unix Yan RPC 查询与提交指令，不打开 SQLite，不启动/终止代理或 Runner。

在另一台服务器克隆后做持久部署，请先读 [部署与验收指南](DEPLOYMENT.md)，其中包含独立 release、用户服务、开机/退出登录、SSH 转发和目标机验收步骤。下文的源码运行命令用于开发与演示。

## 启动

Python 3.11+ / Linux，运行时只有标准库，前端使用随包安装的 HTML/CSS/JavaScript，以及本地 Markdown 组件和字体。部署者不需要 npm、前端构建或 CDN。

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
| 值守总览 / Overview | 全局真实计数、近日故障案录、值守纪事、秉烛人最近完成 tick 与进程信息 |
| 代理名册 / Sui Proxies | 点击代理 ID 或“查看对话”，首先阅读会话实录；通过“运行核验”“身份档案”定位进程证据、Task、Generation、心跳、Handoff、故障和换代历史 |
| 任务案卷 / Tasks | Desired、Declared（未核验代理声明）、Observed（Runner 观测）始终分别呈现；详情按四个分区阅读，见下表 |
| 故障案录 / Incidents | 规则版本、证据与来源；检测建议、最终动作、应用结果/outcome 分开显示 |

Task 详情顶部保留人工操作和“查看当前代理的会话实录”入口；其下的分区不会改变任务状态：

| 分区 | 阅读内容 |
|---|---|
| 任务总览 | 完整任务书、验收条件、完成标记、等待与唤醒、持续恢复配置 |
| 执行核验 | Job/PID、实际指标、命令执行回执、最近故障 |
| 敕令回执 | 追加要求的入账、投递准备与本代已读声明；右上角可打开本浏览器提交的操作回执 |
| 历史案卷 | 完整账本索引、历史事件、代理换代与 Handoff |

分区支持方向键、Home / End；自动刷新保留所选分区、键盘焦点、展开内容与事件阅读位置。切换到另一 Task 或重新加载整页时回到任务总览。手机使用带文字的底部导航。世界观名称的工程含义见 [命名体系](NAMING.md#console-展示名称)。

列表按新记录优先分页，每页 20 条，搜索和状态筛选明确作用于本页。历史事件按 Event ID 增量读取，界面最多保留已加载的最近 500 条，可继续加载后续事件；Incident 历史通过已有游标接口查询。底层 get_task 的完整关联记录仍受既有 RPC 响应大小上限约束，过大时显示查询错误，不会静默省略或伪造记录。

## 阅读字号与会话内容

默认正文 18px、对话约 19px；顶部“字号”支持舒适（18px）、大字（20px）、特大（22px），选择保存在本浏览器，对话按比例放大。标题和正文统一使用无衬线字体：中文为思源黑体，英文为 Inter；代码与标识符使用 JetBrains Mono，中文仍用思源黑体。标题采用更高字重和深绿色。字体随安装包本地提供，无外部字体请求。表格、标签和元数据一同缩放，宽表格在自身区域横向滚动。

从“代理名册”点击代理 ID 或“查看对话”，也可从 Task 顶部进入当前代理，或从历史案卷的代际历史进入旧代理。详情首先展示该代际的“会话实录”；其后的“实际运行证据”分别展示包装进程身份、模型 CLI 进程、独立 Job 与 Runner 指标及查询时间。顶部的分区链接可直接定位。Mock 明确标记为合成演示；Claude 记录来自实际 CLI 的主会话可见输出。控制台不复刻原生 CC，不新增思考计时，不推算工具耗时，不采集 thinking 正文。

默认阅读模式将用户输入、代理回复、工具活动、运行时记录分别显示为蓝色、浅纸色、浅棕色、灰蓝色卡片，同时保留角色文字。运行协议和工具细节默认折叠；具有相同 call_id 的调用与返回合并为一条，缺少关联编号时保留独立记录，不猜测配对。可筛选对话或工具、搜索已加载记录、跳到开头或最新；手机上的查找和定位按钮放在“阅读选项”中。关闭“跟随新消息”后可向上阅读。自动刷新保留手动阅读位置，暂停全局刷新也停止会话轮询；Controller 离线时保留已读取内容并标为历史快照。记录按字节游标分页，页面最多保留 400 条，释放更早内容时有提示，可重新进入从头读取。

**记录只从升级后启动的新 Sui Proxy 开始。** 未采集的旧会话显示“此会话没有留存对话”；不会根据事件拼出对话。每代记录最多 4 MiB，单条最多 8000 字符，达到上限或截断会明确提示。新版本把实际注入的用户目标和验收条件分别完整记录，长字段分段保存、在阅读器合并，仍受每代总上限约束；运行协议另作折叠记录。旧版整段输入 JSON 在可解析时提取其中的用户要求；已经截断的历史记录不能补齐。原始记录在私有状态目录 `transcripts/`；Console 经只读 Yan RPC 查询，不直接读文件或 SQLite。

核验时请分别看：进程身份是否匹配、CLI 是否存在或被冻结、工具返回的实际内容，以及 Job 的 Runner 状态/指标。仅有心跳或助手宣称“完成”不能证明任务已经完成。巡检 Task 可以只执行受管工具而没有独立 Job，此时应查看具体调用与返回；缺少 Job 记录本身不代表模型没有运行。

升级 Controller 和 Console 后，已有代理仍运行原 release，需等待正常换代，或在核对 Job 后通过明确确认的 Hold → Resume 让 Task 生成新代理，才会开始采集。只读阅读器不向 Session 直接发送聊天消息；人工追加要求仍走 Task 的 Instruct 与 Command Receipt。

顶部“语言”可切换中文和 English，选择保存在本浏览器。页面每次只使用一种界面语言；原始任务、对话、工具输出、错误证据和标识符不做机器翻译。切换语言保留表单草稿、所选分区、展开状态和阅读位置。

任务书、验收条件、用户与代理正文，以及交接摘要支持 Markdown 标题、粗体、列表、引用、表格和代码块。原文可展开与复制；代码块可直接复制。原始 HTML 作为文字显示，链接只允许 http/https，图片转换为显式链接，不自动加载远程图片。

会话中的故障标记只来自该代际的 Incident 账本。GEN-001 的检测时间窗口标注为“退化检测窗口”，不代表每一句被框住的话都已经证明有问题。点击标记可查看检测建议、最终动作与恢复结果。普通工具返回错误不会被页面自行判定为会话故障。每次最多读取 20 个标记，浏览器保留最近加载的 100 个；未读完或发生释放时都有提示。

当前截图：[中文总览](screenshots/yan-reader/overview-zh.png) · [英文总览](screenshots/yan-reader/overview-en.png) · [角色区分与工具折叠](screenshots/yan-reader/conversation-roles-zh.png) · [Markdown 任务书](screenshots/yan-reader/task-markdown-zh.png) · [故障位置](screenshots/yan-reader/incident-in-conversation.png) · [手机阅读](screenshots/yan-reader/conversation-mobile.png)。本轮验收见 [阅读体验更新](YAN_CONSOLE_READING.md)。此前 [界面设计记录](YAN_CONSOLE_REDESIGN.md) 与 [初版阅读器报告](SESSION_READER_DELIVERY.md) 保留为历史证据。

## 人工干预与回执

Task 详情提供 Hold、Resume、追加指令，复用已有 Command API。Hold 必须在对话框明确勾选确认；它停止当前 Sui Proxy，保留已启动的 Job。Resume 恢复 Task 的 ACTIVE 意图，由 Candleholder 协调后续代理与已有 Job。追加指令的 SUCCEEDED 只表示要求被记录，不声称 AI 已经完成分析。

每次提交生成稳定幂等键，收到真实 Command ID 后才能展示真实 QUEUED/最终回执。送达未确认时保留原键，只有用户明确重试才再次提交，禁止自动换键重放。回执在本浏览器保留最近 20 条；完整历史仍在账本 Event 中。没有直接杀进程、手动强制轮换、真实训练终止、任意 Shell 或聊天接口。

## 刷新与离线

默认 5 秒，可选择 2/10/30 秒；同一页面不重叠刷新，失败退避最多 30 秒，隐藏标签页暂停，用户可暂停与立即刷新。服务端读取有 2 秒缓存、单次在途合并、有限线程与 8 MiB/64 项缓存上限。所有快照保留原始查询/成功时间。

Controller 健康来自**最近完成的主循环 tick + 已登记进程身份**，RPC 线程有响应不等于在线。Console 无法联系 Controller 时显示离线与最后更新时间，保留已有快照，所有人工操作停用。暂停、超时或数据未核验时不保留绿色实时状态；前端独立检查刷新期限，避免请求挂起后无限显示在线。Job UNKNOWN 不会被显示为完成或正常训练；冻结/失联代理与历史退出分别显示。

## 本地与 SSH 访问边界

服务固定绑定 127.0.0.1，没有公网 Host 参数。仅接受 localhost/127.0.0.1 Host；RPC POST 要求同源 Origin、JSON 与专用请求头，不开放 CORS，禁止页面嵌入，限制请求大小和并发。用户与代理正文经本地 markdown-it 解析、DOMPurify 清洗；原始 HTML 不启用，日志和工具数据仍以转义原文显示。CSP 不允许 inline/eval、第三方脚本或外部图片。静态资源使用 ETag 重验证，账本响应保持 no-store。

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
# 分区、字号、键盘导航、对比度与响应式布局：
PYTHONPATH=src .venv/bin/python scripts/verify_console_design.py --browser /usr/bin/chromium-browser
# 双语、Markdown、角色区分、故障定位与手机阅读：
PYTHONPATH=src .venv/bin/python scripts/verify_console_reading.py --browser /usr/bin/chromium-browser
```

浏览器测试使用实际 Chromium、真实 HTTP/RPC、独立合成进程和实际故障注入；不拦截 API 返回假状态。它检查控制回执、相同 Job/PID 与单次执行、刷新暂停、冻结代理、UNKNOWN、Controller 离线、移动视口、文本转义和无外部页面请求。报告见 console-browser-report.json；截图位于 screenshots/。

最终测试、安装包验证和验收映射见 [v0.4 交付报告](YAN_CONSOLE_DELIVERY.md)，原始输出见 console-test-output.txt，结构化结果见 console-verification.json。浏览器的最近回执按账本标识隔离，切换工作目录不会把旧命令当作当前账本回执。

## 指令交付、等待与历史

Task 的“敕令回执”区分入账、投递准备、本代已读声明；相关操作是否执行始终需要回执与产物证明。“任务总览”的等待面板展示原因、对象、责任方和下次检查，不能把 WAITING 当作 Session 故障。“历史案卷”的完整账本索引可翻页读取历史要求、操作、Jobs、代理、产物和事件；代理历史可打开该代实际对话。恢复区的“最近运行证据”仅反映配置范围内运行观察，不证明科研进展。离线时保留最后快照和更新时间，操作停用。语义与后端验收见 [职责迭代报告](RESPONSIBILITY_ITERATION.md)。
