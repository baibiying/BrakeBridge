# A · 拓客 任务书

> 你负责一块完整功能：**买家名单进 → 证据卡出 → 英文邀约出。**
> 你还是全组的**框架搭建人**：15:30 前必须把 `app.py`、`base.html`、`base.css` 推上 `master`，之后冻结。
> 硬节点：**15:30 框架上线**（全组等）、**18:00 `/sourcing` 能导入 6 行并出卡**

---

## 0. 起手 30 分钟（先做这个，别做功能）

```bash
git clone https://gitee.com/liuxiansen52/gtm.git
cd gtm
git switch -c feat/sourcing
python -m pip install flask
```

建骨架，逐项打勾：

- [ ] `app.py`：Flask 实例 + `importlib` 循环注册三个 blueprint，每个用 `try/except` 兜住
- [ ] `features/sourcing/__init__.py`：定义 `sourcing_bp = Blueprint("sourcing", __name__, url_prefix="/sourcing", template_folder="templates")` 并 import routes
- [ ] `features/inquiry/__init__.py`、`features/fitment/__init__.py`：同结构，**只建空壳**，路由文件留空
- [ ] `templates/base.html`：导航三条链接 `/sourcing` `/inquiry` `/fitment`
- [ ] `static/base.css`：简单排版，够用就行
- [ ] `requirements.txt`：`flask`
- [ ] `README.md`：三行，装依赖 / 启动 / 打开地址
- [ ] `data/.gitkeep`、`data/sourcing/.gitkeep` 等占位
- [ ] `python app.py` 能起，`/`、`/sourcing`、`/inquiry`、`/fitment` 都返回 200（后两个是空页）

推上去，群里喊一句「框架已上 master」。**没有这句话别人不动手。**

---

## 1. P0 任务

| 编号 | 任务 | 完成判据 | 时间盒 |
| --- | --- | --- | --- |
| **A-01** | 框架落地 | 见上，全部打勾，已推送 | 15:00–15:30 |
| **A-02** | 导入与去重 | 粘贴 6 行 → 回显「新增 4 / 重复 1 / 跳过 1」 | 15:30–16:40 |
| **A-03** | 证据卡 | 每家一张卡：品类 / 采购能力 / 中国采购 / 展会意向 / 原文 | 16:40–17:40 |
| **A-04** | 英文邀约 | 选品类出模板，展位缺失时改问行程，可编辑可复制 | 17:40–18:00 |
| **A-05** | 空态与异常 | 无数据、CSV 解析失败都给一句人话，不白屏不 500 | 19:20–20:00 |

### A-02 解析规则

- 只认表头 `name,country,category,website_note,source_text`，缺列不报错，缺值留空。
- 去重键：`name` 去空格转小写 + `|` + `country`。
- 回显：`新增 N 家 / 重复 M 家 / 跳过 K 行`，重复项可点开看是哪一个。
- 解析失败的行（列数不对、全空）计入「跳过」。

### A-03 证据卡规则（写死，不许猜）

| 字段 | 规则 |
| --- | --- |
| 品类 | 关键词表匹配 `source_text`（20 条：brake、lining、pad、truck parts、clutch、filter…） |
| 采购能力 | 出现 import/agent/distributor/wholesale → `有迹象`；否则 `待核` |
| 中国采购 | 出现 China/Chinese/import from China → `有`；否则 `未提及` |
| 展会意向 | 出现 exhibition/fair/Automechanika/Canton → `有`；否则 `未提及` |
| 原文 | 原样显示 `source_text`，**每条结论必须能指回这句** |

**证据不足就写「待核」，不许推断“很可能有采购能力”。**

### A-04 邀约模板

三套：brake lining / truck parts / 通用。硬规则：

1. 没有展会信息时，展位那句话整句省略，改成 `Will you be visiting any trade fairs this quarter?`
2. 数量未知不写数量。
3. 生成的是可编辑文本框 + 「复制」按钮，不是发送。

---

## 2. P1（P0 全绿后）

| 编号 | 任务 | 时间盒 |
| --- | --- | --- |
| A-06 | 文件上传（多编码兜底） | 40m |
| A-07 | 品类关键词表扩到 60 条 | 30m |
| A-08 | 导出买家清单 CSV | 30m |
| A-09 | 买家卡跳 `/inquiry?q=` | 20m |

---

## 3. 边界

| 只碰 | 不许碰 |
| --- | --- |
| `features/sourcing/` | `features/inquiry/`、`features/fitment/` |
| `data/sourcing/` | 别人的数据文件 |
| `app.py` / `base.html` / `base.css`（仅 15:30 前） | 15:30 后这三份一律不许改 |

---

## 4. 卡住怎么办

| 情况 | 动作 |
| --- | --- |
| 15:30 框架没做完 | 只推 `app.py` 加三个空路由，模板后补 |
| 16:40 去重没写完 | 先不做去重，导入直接全收，回显写「新增 N」 |
| 17:40 证据卡规则太慢 | 只做品类 + 原文两栏，其余显示「待核」 |
| 18:00 页面 500 | 优先修启动，功能往后放 |

---

## 5. 18:00 前必须交出

- [ ] 框架已上 `master`，三人本机能跑
- [ ] `/sourcing` 粘贴 6 行出「4 / 1 / 1」回显
- [ ] 每家一张证据卡，带原文
- [ ] 点「生成邀约」出英文草稿，可复制
- [ ] 群里报：`/sourcing 通，导入 4 家`