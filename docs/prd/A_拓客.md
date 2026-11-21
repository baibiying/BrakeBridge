# A · 拓客

> 你负责一块完整功能：**买家名单进 → 证据卡出 → 英文邀约出。**
> 你还是全组的框架搭建人：**15:30 前 `app.py`、`base.html`、`base.css` 必须上 `master`**，之后冻结。
> 硬节点：15:30 框架上线（全组等）、18:00 `/sourcing` 能导入 6 行并出卡、22:00 收工。

---

## 1. 框架与边界（15:30 前必须建好）

```
app.py                  ← 你写，之后冻结
templates/base.html     ← 你写，之后冻结（导航三条链接）
static/base.css         ← 你写，之后冻结
requirements.txt        ← 只有 flask
README.md               ← 三行启动说明
features/sourcing/      ← 你独占
  __init__.py  routes.py  service.py
  templates/sourcing/*.html
data/sourcing/          ← 你独占
features/inquiry/__init__.py   ← 只建空壳，B 填
features/fitment/__init__.py   ← 只建空壳，C 填
```

技术栈写死：**Python 3.12 + Flask 3 + Jinja2 + JSON 文件，不用数据库，不引前端库，端口 5000。**

`app.py` 必须这样注册，某个功能没写完时其他页面不能崩：

```python
import importlib
for module, name in [("features.sourcing", "sourcing_bp"),
                     ("features.inquiry", "inquiry_bp"),
                     ("features.fitment", "fitment_bp")]:
    try:
        mod = importlib.import_module(module)
        app.register_blueprint(getattr(mod, name))
    except Exception as e:
        print("skip", module, e)
```

**导航固定三条**：拓客 `/sourcing` ｜ 询盘 `/inquiry` ｜ 速配 `/fitment`。
**跨功能只有一个链接**：`/fitment?q=<车型>&from=inquiry:<id>`，你不参与实现，只要导航别写错。

**不许碰**：`features/inquiry/`、`features/fitment/`、`data/` 下别人的目录、15:30 后改冻结文件。

---

## 2. P0 任务

| 编号 | 任务 | 判据 | 时间盒 |
| --- | --- | --- | --- |
| A-01 | 框架 | 四个路径都返回 200，已推 master | 15:00–15:30 |
| A-02 | 导入与去重 | 粘 6 行 → 新增 4 / 重复 1 / 跳过 1 | 15:30–16:40 |
| A-03 | 证据卡 | 品类 / 采购能力 / 中国采购 / 展会意向 / 原文 | 16:40–17:40 |
| A-04 | 英文邀约 | 按品类选模板，可编辑可复制 | 17:40–18:00 |
| A-05 | 空态与异常 | 不白屏不 500 | 19:20–20:00 |

---

## 3. 规则（写死，不许猜）

**输入列**：`name,country,category,website_note,source_text`，只有 `name` 必填。
**去重键**：`name` 去空格转小写 + `|` + `country`。

| 判断项 | 规则 | 不满足时 |
| --- | --- | --- |
| 品类 | 关键词表匹配 `source_text`（brake、lining、pad、truck parts、clutch、filter 等 20 条） | 待核 |
| 采购能力 | 出现 import / agent / distributor / wholesale | 待核 |
| 中国采购 | 出现 China / Chinese / import from China | 未提及 |
| 展会意向 | 出现 exhibition / fair / Automechanika / Canton | 未提及 |
| 原文 | 原样展示，每条结论都能指回这句话 | — |

**邀约三条硬规则**：没有展会信息就整句省略，改问 `Will you be visiting any trade fairs this quarter?`；数量未知不写数量；生成的是可编辑文本 + 复制按钮，不是发送。

---

## 4. 你给 AI 的第一句话（复制即用）

```
你是我的结对开发，只做我指定的这一个任务，不做其他改进。
先读 docs/prd/A_拓客.md 的「框架与边界」和「规则」两节。
现在做 A-01 建框架：app.py、templates/base.html、static/base.css、
features/sourcing|inquiry|fitment 各一个 __init__.py、requirements.txt、README.md。
约束：只新建这些文件，不改已有文件，不加依赖，不引前端库，端口 5000。
交付四样：改了哪些文件、我该跑什么命令、你实际跑的输出、哪条验收没覆盖。
没跑通之前不要说完成。
```

**每次收尾四问**：① 贴实际跑的命令和输出 ② 改了哪些文件 ③ 哪条没覆盖 ④ 重启后什么会丢。

**禁止**：顺手优化、编造数据、一次做多个任务、没跑通就说完成、改冻结文件、加依赖、引入 Tabler/openpyxl。
**时间盒**：一次对话不超过 20 分钟，同一问题改三次还不过就停。

---

## 5. 卡住怎么办

| 情况 | 动作 |
| --- | --- |
| 15:30 框架没完 | 只推 `app.py` 加三个空路由，模板后补 |
| 16:40 去重没写完 | 先不做去重，回显写「新增 N」 |
| 17:40 证据卡太慢 | 只做品类 + 原文，其余显示「待核」 |
| 18:00 页面 500 | 优先修启动 |

---

## 6. 18:00 交出与验收

- [ ] 框架已上 `master`，三人本机能跑
- [ ] `/sourcing` 导入 6 行出「4 / 1 / 1」
- [ ] 每家一张卡，带原文
- [ ] 能生成一段英文邀约并复制

| # | 操作 | 预期 |
| --- | --- | --- |
| V15 | 上传 6 行样例 | 4 家企业、5 条证据、1 条重复、1 行跳过 |
| V16 | 原样重传 | 新增 0 |
| V17 | 只有官网产品页的买家 | 品类可判、采购能力待核、邀约不出现展位 |
| V18 | 有展会意向的买家 | 邀约出现展会句或行程问题 |