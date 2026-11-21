# BrakeBridge AI · 车型候选速配（湖州黑客松 AI + GTM）

从零搭一个能现场演示的最小系统：**导入买家名单 → 接住一条询价评论 → 从产品册里查出候选规格 → 出报价草稿**。
数据来自冠英、冠隆两本真实产品册的 6 页，153 条车型适配记录，每条都能点回页码。

---

## 现在从哪开始

| 你要做的 | 看这个文件 |
| --- | --- |
| 今晚做什么、几点交、卡住怎么降级 | `docs/prd/kickoff/开工_<你的代号>.md` |
| 框架、目录归属、跨功能怎么接 | `docs/prd/contracts/C0_框架与合流契约.md` |
| 数据文件字段 | `docs/prd/contracts/C1_数据契约.md` |
| 路由与跨功能参数 | `docs/prd/contracts/C2_接口契约.md` |
| 跟 AI 怎么说话（第一条指令） | `docs/prd/05_AI协作规范.md` |
| 验收怎么判 | `docs/prd/quality/验收清单.md` |
| 海报与路演文案 | `docs/prd/demo/` |

总索引：`docs/prd/00_INDEX.md`。历史稿在 `docs/archive/`，**不作为开工依据**。

---

## 四人分工（一人一块功能，从页面到数据全包）

| 代号 | 功能 | 独占目录 | 主页 |
| --- | --- | --- | --- |
| A | 拓客：名单导入 → 证据卡 → 英文邀约 | `features/sourcing/`、`data/sourcing/` | `/sourcing` |
| B | 询盘：粘贴评论 → 意向卡 → 分类 → 跳选型 | `features/inquiry/`、`data/inquiry/` | `/inquiry` |
| C | 速配：153 条 → 候选卡 → 追问 → 报价草稿 | `features/fitment/`、`data/fitment/` | `/fitment` |
| PM | 海报、作品简介、5 分钟路演 | `docs/` | — |

**跨功能只有一个链接**：`/fitment?q=<车型>&from=inquiry:<id>`。

---

## 技术栈与运行

- Python 3.12、Flask 3、Jinja2；存储用 JSON 文件，**不用数据库**；前端原生 HTML + 一个共用 CSS。
- 共享文件只有 `app.py`、`templates/base.html`、`static/base.css`，由 A 在 15:30 前写完即冻结。

```bash
python -m pip install -r requirements.txt
python app.py
# 打开 http://127.0.0.1:5000
```

---

## Git 协作

- 主干 `master` 受保护；每人一条分支：`feat/sourcing`、`feat/inquiry`、`feat/fitment`。
- 只提交自己目录；PR 写三行：改了哪些文件 / 自测命令 / 自测输出一句。
- 冻结文件（`app.py`、`templates/base.html`、`static/base.css`、`requirements.txt`）改动必须 PM 同意。

---

## 硬截止

| 时间 | 事项 |
| --- | --- |
| 11/21 15:30 | A 的框架上 `master`，全组前置 |
| 11/21 18:00 | 联调①：干净克隆能跑，三个页面都能开 |
| 11/21 21:00 | 联调②：询盘 → 选型 → 候选 → 报价草稿 |
| 11/21 22:00 | 收工，同时提交海报 |
| 11/22 09:00 | 提交作品简介 |
| 11/22 12:00 | 开发全部结束 |

仓库内只放合成或获准展示的样例；密钥、令牌、企业原始记录不进仓库。