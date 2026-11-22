# BrakeBridge · 冠英外贸工作台

面向商用车制动件外贸销售，将买家筛选、询盘跟进、线索承接、车型速配和社媒营销放进同一个工作台。

**公网体验** [打开 BrakeBridge](https://bb.supermans.top/login) · 账号 **admin** · 密码 **123456**

## 主要功能

| 模块 | 可以做什么 |
| --- | --- |
| 业务总览 | 查看重点买家、待跟进询盘和展会预约，用甘特图管理排期。 |
| 客户名单 | 导入客户表格，结合产品匹配、进口证据和到展意向评分，生成展会邀约草稿。 |
| 询盘转化 | 汇集转单云询盘和客户回信，查看评分依据、跟进状态及企业背调。 |
| 线索承接 | 识别询价、约见和待补充信息，拆分多行采购明细，生成回复草稿和待核价资料。 |
| 车型速配 | 按件号、车桥或车型查询 153 条目录记录，核对规格和目录页码后生成待核价单。 |
| 社媒营销 | AI 辅助写稿和选图，保存草稿，通过飞鸽发布或排期，按意向跟进评论。 |

**快速体验** 登录后打开「线索承接 → 演示样本」，点击多行询价，查看逐行核价资料和速配入口。也可以在「车型速配」搜索件号 `DF-PAD-188A`。

[总览截图](docs/screenshots/01-dashboard.jpg) · [车型速配截图](docs/screenshots/14-fitment.jpg) · [团队 PRD](docs/prd/)

## 本地运行

技术栈为 Vue 3、TypeScript、Naive UI、ECharts、Flask 和 SQLite。以下命令适用于安装了 Python 3.12 和 Node.js 24 的 Windows PowerShell。

```powershell
git clone https://gitee.com/liuxiansen52/gtm.git
cd gtm
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build

$env:BRAKEBRIDGE_DB = Join-Path $env:LOCALAPPDATA 'BrakeBridge\workbench-demo.sqlite3'
$env:LEADS_ALLOW_SYNTHETIC = '1'
.\.venv\Scripts\python.exe -m flask --app app seed demo
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8080 app:app
```

启动后访问 [本地登录页](http://127.0.0.1:8080/login)。新演示库的默认凭据为 `admin` / `123456`。

销售模型和飞鸽账号可在工作台内配置。定时发帖使用同一数据库启动 Flask 的 `social-worker`。许可证见 [LICENSE](LICENSE)。
