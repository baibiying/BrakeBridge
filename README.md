# BrakeBridge · 冠英外贸工作台

面向商用车制动件外贸团队，覆盖买家筛选、展会邀约、询盘跟进和社媒营销。

## 功能

- **总览**：展会甘特图、待办和重点跟进客户。
- **客户名单**：导入 Excel 或 CSV，查看采购证据和评分，生成展会邀约。
- **询盘转化**：导入转单云询盘和客户回信，按意向分级，支持翻译、公开背调和竞品线索整理。
- **社媒营销**：通过飞鸽查看 10 个平台的绑定状态，生成或编辑文案，立即或定时发布，并查询发送回执。评论线索支持评分、优先级筛选和转询盘，内置 30 条中文演示评论。

技术栈为 Vue 3、TypeScript、Flask 和 SQLite。

## 本机启动

需要 Python 3.12 和 Node.js 24。以下命令在仓库根目录的 PowerShell 中运行。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
$env:BRAKEBRIDGE_DB = Join-Path $env:LOCALAPPDATA 'BrakeBridge\workbench-demo.sqlite3'
$env:LEADS_ALLOW_SYNTHETIC = '1'
.\.venv\Scripts\python.exe -m flask --app app seed demo
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8080 app:app
```

打开 [工作台](http://127.0.0.1:8080)。本机演示初始账号为 `admin`，密码为 `123456`。

连接真实飞鸽时，为 Web 服务和任务进程配置相同的 `SOCIAL_TOKEN_KEY` 和 `BRAKEBRIDGE_DB`，从社媒营销右上角连接账号。另开终端运行 `.\.venv\Scripts\python.exe -m flask --app app social-worker` 处理到期发布。AI 文案使用配置的 B1X 模型，演示环境可用企业预设。

[团队分工与 PRD](docs/prd/)
