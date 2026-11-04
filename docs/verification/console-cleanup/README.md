# README、世界观与界面素材整理

2026-11-04，基于 `1911ec1`。源码指纹及本轮结果见 [summary.json](summary.json)。

- 根 README 说明项目能力、职责、架构、运行方式和任务书入口；原中文叙事与英文译文迁入 [LORE.md](../../LORE.md)，正文逐段比对一致。
- 移除三张游戏原图、Console 中的副本、静态路由、图片布局与素材页脚；删除三张含有游戏图片的旧总览截图，重新拍摄当前界面。
- 保留项目自绘 SVG、已有字体和 Markdown 依赖及其许可证。历史验证报告保留当时的资源名称与哈希，不再附带被移除的图片。

当前截图：[中文总览](../../screenshots/yan-console/overview-zh.png) · [英文总览](../../screenshots/yan-console/overview-en.png) · [手机总览](../../screenshots/yan-console/overview-mobile.png)。

| 检查 | 结果与证据 |
|---|---|
| Console / 兼容接口 | 14 项通过；[原始输出](unit-tests.txt) |
| 人工操作与状态回归 | 16 项通过；Hold 确认、Resume、Instruct、真实回执、原 Job/PID、冻结、UNKNOWN 与 Controller 离线；[报告](controls.json) |
| 阅读、双语与布局 | 26 项通过，276 个布局组合，无脚本错误或外部资源请求；[设计](design.json)、[阅读器](reading.json) |
| 安装包 | 清除旧构建产物后构建 wheel，安装到新 venv；12 条静态路由与源码字节相同，旧图片地址返回 404，包内无游戏图片，开源依赖许可证齐全；[报告](wheel.json) |
| 文档 | 本轮修改文档的本地链接目标均存在，原中英文叙事逐段一致；[报告](documents.json) |

浏览器验证使用 Chromium 133 和真实 localhost HTTP / RPC / SQLite，代理与作业为合成场景。本轮没有重测真实模型或目标服务器部署。

复现现有界面回归（需已有 Playwright 与 Chromium）：

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_console tests.test_proxy_compat -v
SUI_QA_OUTPUT=/tmp/sui-console-cleanup-check
for check in console_browser console_design console_reading; do
  PYTHONPATH=src .venv/bin/python "scripts/verify_${check}.py" \
    --browser /usr/bin/chromium-browser \
    --output "$SUI_QA_OUTPUT/$check/screenshots" \
    --report "$SUI_QA_OUTPUT/$check/report.json"
done
```

这一轮没有数据库或控制协议变更。已安装阅读器版本的部署更新 Console 安装包并重启该服务即可；较早版本仍按[部署指南](../../DEPLOYMENT.md)完成后端兼容检查。非 editable 安装需要重新安装新 release，仅拉取源码不会更新正在运行的页面。

构建新包时使用干净 checkout；若复用旧源码目录，先清理该目录的 `build/` 与 `src/sui_regulator.egg-info/` 生成物，避免旧构建缓存把已经删除的文件带回 wheel。
