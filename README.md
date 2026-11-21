# BrakeBridge · 冠英外贸工作台

面向商用车制动件外贸团队，覆盖买家筛选、展会邀约、询盘跟进和社媒营销。

## 功能

- **总览**：展会甘特图、待办和重点跟进客户。
- **客户名单**：导入 Excel 或 CSV，查看采购证据、规则分和 AI 分，生成展会邀约。
- **询盘转化**：导入转单云询盘和客户回信，按意向分级，支持翻译、公开背调、竞品线索和中文会面卡。
- **社媒营销**：使用企业资料和近期热点制作草稿，从素材库手选或让 Agent 配图。预览确认后由工作台自动将配图上传到飞鸽，以图文发布。独立的定时发布页面可查看和取消待发稿件，发布记录可查看各平台回执。评论线索支持评分和转询盘。

技术栈为 Vue 3、TypeScript、Flask 和 SQLite。

## 本机启动

需要 Python 3.12 和 Node.js 24。以下命令在仓库根目录的 PowerShell 中运行。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
$env:BRAKEBRIDGE_DB = Join-Path $env:LOCALAPPDATA 'BrakeBridge\workbench-demo.sqlite3'
.\.venv\Scripts\python.exe -m flask --app app seed demo
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8080 app:app
```

打开 [工作台](http://127.0.0.1:8080)。本机演示初始账号为 `admin`，密码为 `123456`。向团队部署时可在首次初始化前设置 `BRAKEBRIDGE_ADMIN_PASSWORD`。

`seed demo` 会从 `data/company-seed.sqlite3` 导入 61 条转单云询盘和 40 张企业产品图，同时保留演示客户与评论。能匹配原件的 AI 评分也会一并加载。已有工作台可对同一个运行数据库执行 `.\.venv\Scripts\python.exe -m flask --app app seed company`。重复执行会按询盘和图片去重，并保留现有编辑、跟进和回收站状态。

从社媒营销右上角填写转单云账号密码，系统会加密保存。需要验证码时在弹窗回填。可选开启会话保活，系统每 10 分钟检查已有会话，授权失效后会提示重连。

立即发布和定时发布由 `.\.venv\Scripts\python.exe -m flask --app app social-worker` 处理。任务进程和 Web 服务使用同一个 `BRAKEBRIDGE_DB`。使用带图文章时，需在飞鸽绑定支持图片的平台。YouTube 和 TikTok 保留视频发布流程。

在「系统设置 → 销售 Agent」选择模型服务商，或填写第三方 OpenAI Chat Completions 的公网 HTTPS Base URL。可获取可用模型、手填模型名称和测试连通性。客户与询盘默认规则分和 AI 分各占 50%，支持独立调权和一键重评。公开背调、竞品和实时热点可选择单独的联网取证模型。

本仓库的企业初始库供获授权的团队使用。运行数据库、飞鸽授权和模型密钥由各自部署保存。团队需要连接自己的飞鸽帐号，并启动发布工作进程后才能提交真实图文。

[团队分工与 PRD](docs/prd/)
