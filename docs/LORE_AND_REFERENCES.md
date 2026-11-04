# 资料与风格参考

[文档目录](README.md) · [项目首页](../README.zh-CN.md) · [项目世界观](LORE.md) · [完整命名约定](NAMING.md)

本页记录命名灵感与文档组织的参考。早期资料查阅日期为 2026-11-03，文档拆分与素材清理更新于 2026-11-04。维护者提供的中文叙事与英文译文已迁入 [LORE.md](LORE.md)；README 改为项目介绍、实际能力、架构和快速开始。当前网页、文档与安装包不再包含游戏美术素材。

## 为什么叫 LORE.md

本项目选择 `docs/LORE.md` 收录叙事与背景设定，`NAMING.md` 继续说明名称对应的工程职责。`Lore` 比 `Settings` 更能表达这里的世界观；`Settings` 容易与软件配置混淆。这是本项目的组织选择，不是强制的开源规范。

参考了以下仓库的文件组织，没有复制其设定或美术：

- [deep-world-tui / LORE.md](https://github.com/mtclab/deep-world-tui/blob/master/LORE.md)：使用独立文件记录世界背景与设定。
- [Endgame / README](https://github.com/AdvaitU/endgame)：将 `Worldbuilding.md` 与技术文档分开组织。

## 《明日方舟》资料

| 资料 | 核实内容与本项目的用法 |
|---|---|
| [PRTS：左乐](https://prts.wiki/w/左乐) | 玩家 Wiki 转录的游戏档案将左乐与司岁台、秉烛人联系起来；语音中有查阅卷宗和监督代理人的叙述。用于确定“官署、值守、记录”的文案方向。 |
| [PRTS：左乐 · 模组](https://prts.wiki/w/左乐#模组) | 模组「岂苦夜长」的铭文为世界观文档短引文的出处；它与另一模组「秉烛人的记录」分列。 |
| [《明日方舟》官网：「辞岁行」活动公告](https://ak.hypergryph.com/news/4942) | 官方活动公告使用篇章与记录式栏目组织内容，为首页分卷、开卷的语气提供参考。该公告不用于证明司岁台的具体组织设定。 |

PRTS 是社区整理，以上涉及角色的内容以其转录的游戏档案、语音与模组文本为依据。游戏内容的最终表述应以游戏为准。未将自动生成的剧情概述用作设定依据。

## 原作意象与工程改编

| 命名 | 本项目的改编边界 |
|---|---|
| 司岁台 / Sui Regulator | 采用原作组织的名称意象，映射为持久任务控制系统。软件职责不是原作设定。 |
| 大炎 / Yan | 用作人类与外部客户端的入口名称；CLI、RPC、Console 是工程设计。 |
| 秉烛人 / Candleholder | 借用监视、值守意象，映射为不依赖 LLM 的确定性恢复职责。 |
| **Sui Proxy / 岁兽代理人** | 项目约定的统一 AI 会话角色名。可替换、Generation、lease 和所有权均为软件概念，不据此推演游戏中的代理关系。 |
| 敕令、案卷、移交 | 用作 Command、Task 关联记录、Handoff 的叙述层；不增设第二套协议或存储。 |

项目中“长事续行”的意象指持久任务、受管 Job 与可审计接管；恢复仍受人工意图、预算、所有权和执行事实约束。诗性表达不增加未经实现的保证。

## 开源 README 风格参考

查阅了以下公开 README，只借鉴信息组织与叙事方式：

| 项目 | 参考之处 | 早期首页的取法 |
|---|---|---|
| [Project AIRI](https://github.com/moeru-ai/airi/blob/main/README.md) | 封面、鲜明的项目愿景、演示与文档入口 | 原创封面后先交代项目所为何事，再给演示与文档导航。 |
| [NERV-CODE](https://github.com/Ax1i1om/NERV-CODE/blob/main/README.md) | 用作品世界观组织章节和工程角色 | 采用司岁台的值录、诸职与台规组织首页。仅参考 README 叙事结构，未采用其软件实现或安装方式。 |
| [结印 Ketsuin](https://github.com/huanglizhuo/Ketsuin/blob/main/README_zh.md) | 动漫意象与具体产品行为对应 | 让“敕令、案卷、换代”分别落到 Command、账本、代理接管，保留明确的工程入口。 |

未复制上述项目的正文、徽标、截图或代码。保留的项目自绘封面 [sui-regulator-header.svg](assets/sui-regulator-header.svg) 由本仓库 SVG 构成，不加载外部字体、脚本或游戏素材。Console 仅保留自绘 SVG 标记与界面图标；原游戏图片、静态路由和包含图片的旧总览截图已移除。

上述来源用于说明命名灵感，不表示本项目属于原作或得到官方背书。本文保留文本出处链接，不嵌入原作图片；本项目的工程设计与叙事改编也不作为游戏设定的依据。
