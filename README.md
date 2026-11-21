# BrakeBridge AI · 车型候选速配

从零搭一个能现场演示的最小系统：**导入买家名单 → 接住一条询价评论 → 从产品册查出候选规格 → 出报价草稿**。
数据来自两本真实产品册的 6 页、153 条记录，每条都能点回页码。

## 四份文档，每人只看自己那份

| 谁 | 文件 | 主页 |
| --- | --- | --- |
| A · 拓客（兼框架） | [`docs/prd/A_拓客.md`](docs/prd/A_拓客.md) | `/sourcing` |
| B · 询盘承接 | [`docs/prd/B_询盘.md`](docs/prd/B_询盘.md) | `/inquiry` |
| C · 车型速配 | [`docs/prd/C_速配.md`](docs/prd/C_速配.md) | `/fitment` |
| PM · 海报与展演 | [`docs/prd/PM_海报与展演.md`](docs/prd/PM_海报与展演.md) | — |

每份都自带：目录边界、P0 任务与时间盒、关键规则、给 AI 的第一句话、卡住怎么办、验收。**不需要读第二份文件。**

## 技术栈

Python 3.12 + Flask 3 + Jinja2 + JSON 文件，不用数据库，端口 5000。
共享文件只有 `app.py`、`templates/base.html`、`static/base.css`，A 在 15:30 前写完即冻结。

```bash
python -m pip install -r requirements.txt
python app.py
```

## 硬截止

| 时间 | 事项 |
| --- | --- |
| 11/21 15:30 | A 的框架上 `master` |
| 11/21 18:00 | 三个页面都能开 |
| 11/21 21:00 | 询盘 → 选型 → 候选 → 报价草稿 |
| 11/21 22:00 | 收工，提交海报 |
| 11/22 09:00 | 提交作品简介 |
| 11/22 12:00 | 开发结束 |

历史稿在 `docs/archive/`，不作为开工依据。仓库只放合成或获准展示的样例。