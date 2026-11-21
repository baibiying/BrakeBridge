# 开工单 · A 拓客

> 一块完整功能归你：**买家名单进 → 证据卡出 → 英文邀约出。**
> 你还是全组的框架搭建人，**15:30 前框架必须上 `master`**，全组等你。

---

## 1. 起手（复制执行）

```bash
git clone https://gitee.com/liuxiansen52/gtm.git
cd gtm
git switch -c feat/sourcing
python -m pip install flask
python -c "import flask; print(flask.__version__)"
```

---

## 2. 15:00–15:30 建框架（做完推上去，群里喊一声）

- [ ] `app.py`：Flask 实例 + 循环注册三个 blueprint，每个 `try/except` 兜住
- [ ] `features/sourcing/__init__.py`：`sourcing_bp`，`url_prefix="/sourcing"`
- [ ] `features/inquiry/__init__.py`、`features/fitment/__init__.py`：只建空壳
- [ ] `templates/base.html`：导航三条链接
- [ ] `static/base.css`
- [ ] `requirements.txt`（只写 `flask`）、`README.md`（三行）
- [ ] `python app.py` 能起，四个路径都返回 200

**三份文件 15:30 后冻结**：`app.py`、`templates/base.html`、`static/base.css`。

---

## 3. P0 任务

| 编号 | 任务 | 判据 | 时间盒 |
| --- | --- | --- | --- |
| A-01 | 框架 | 见上 | 15:00–15:30 |
| A-02 | 导入与去重 | 6 行 → 新增 4 / 重复 1 / 跳过 1 | 15:30–16:40 |
| A-03 | 证据卡 | 品类 / 采购能力 / 中国采购 / 展会意向 / 原文 | 16:40–17:40 |
| A-04 | 英文邀约 | 按品类选模板，可编辑可复制 | 17:40–18:00 |
| A-05 | 空态与异常 | 不白屏不 500 | 19:20–20:00 |

---

## 4. 关键规则（写死，不许猜）

| 项 | 规则 |
| --- | --- |
| 去重键 | `name` 去空格转小写 + `|` + `country` |
| 品类 | 关键词表匹配 `source_text`，20 条 |
| 采购能力 | 有 import/agent/distributor → `有迹象`，否则 `待核` |
| 中国采购 | 有 China/import → `有`，否则 `未提及` |
| 展会意向 | 有 fair/exhibition/Automechanika → `有` |
| 原文 | 原样展示，每条结论必须指回这句 |
| 邀约 | 展位缺失时整句省略，改问 `Will you be visiting any trade fairs this quarter?` |

**证据不足写「待核」，不许推断。**

---

## 5. 边界

只碰 `features/sourcing/` 和 `data/sourcing/`。不许碰 `features/inquiry/`、`features/fitment/`、别人的数据文件、冻结文件。

---

## 6. 卡住怎么办

| 情况 | 动作 |
| --- | --- |
| 15:30 框架没完 | 只推 `app.py` 加三个空路由，模板后补 |
| 16:40 去重没写完 | 先不做去重，回显写「新增 N」 |
| 17:40 证据卡太慢 | 只做品类 + 原文，其余显示「待核」 |

---

## 7. 18:00 交出

- [ ] 框架已上 `master`，三人本机能跑
- [ ] `/sourcing` 导入 6 行出「4 / 1 / 1」
- [ ] 每家一张卡，带原文
- [ ] 能生成一段英文邀约并复制