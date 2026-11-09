# 测绘报告质检面板

飞书多维表格的边栏插件（iframe 面板）。用于在表里逐条复核 AI 预审结论、人工判定通过或退回，并把结论与操作人留痕。

> 本仓库是**脱敏副本**：不含任何真实任务号、工程量、人名或 base token。数据目录里只有 `*.example.json` 三条虚构记录，clone 下来即可运行。

---

## 1. 它解决什么

测绘成果报告在提交审核前，需要把「工程量清单」与「图面注记 / 报告自述」逐项对账。人工逐份比对慢，且结论不统一。

这个面板做三件事：

1. **并排展示对账结果**：每条记录给出徽章（一致 / 不一致 / 并列核对）和一句可复核的判定理由。
2. **人工做最后一步**：AI 只给建议，人点「通过」或「不合格·退回」，可附规则号和补充说明。
3. **留痕**：AI 原判与人工结论分列保存，谁在哪一刻改了什么，可追。

关键约束：**AI 的判定永远不会被覆盖**。人工改判写另一列，两列并存，事后能看出「机器怎么判、人怎么改」。

---

## 2. 运行形态（先看这一节）

这是一个 **iframe 面板 + 本地服务** 的组合，不是纯前端 bundle：

```
多维表格（宿主）
  └─ iframe  ──  /panel  ──  静态页面 + Base JS SDK（只取身份）
                                 │
                                 └─ fetch ──  本服务（Flask）
                                                 ├─ data/records.json   判定结果（离线跑批产物）
                                                 ├─ data/people.json    open_id → 姓名
                                                 ├─ data/qc.db          留痕三表
                                                 └─ img/                对比图
```

- **SDK 的用途只有一个**：`bridge.getUserId()` 取当前登录人的 open_id，用于署名。数据的读写全部走本服务的 HTTP 接口。
- **判定结果不在服务里实时计算**，而是由离线质检链路的产物聚合而来（见第 5 节）。服务只负责展示、接收人工决定、留痕。

因此部署方式有两种：

| 方式 | 做法 | 适用 |
|---|---|---|
| A 自托管 | 起服务，在多维表格「自定义插件」里填运行地址 `/panel?k=<口令>` | 现在可用 |
| B 平台托管 | 需要把页面改成纯前端包（Vite + 只依赖 Base SDK），由平台构建部署 | **未实现**，见第 9 节 |

---

## 3. 目录结构

```
app.py                 路由、鉴权、留痕读写（单文件）
serve.py               waitress 启动入口（生产）
build_data.py          把离线跑批产物聚合成 data/records.json
build_people.py        扫多维表格人员字段，生成 open_id → 姓名 映射
templates/
  panel.html           面板本体（前端全部逻辑，无构建步骤）
  host.html            假宿主：本地不开飞书也能调界面
  probe.html           宿主能力探测页（查 SDK 到底注入了什么）
data/
  records.example.json 三条虚构记录（真实 records.json 不入库）
  people.example.json  三个虚构人员（真实映射不入库）
vendor/sdk/            多维表格 JS SDK 本地副本，不走 CDN
requirements.txt       flask / waitress / pillow
run.bat                Windows 一键启动（读 .env.local）
```

没有前端构建步骤：`panel.html` 直接用原生 JS，SDK 以 ESM 形式本地托管。这样离线可跑，也不依赖任何 CDN。

`_*.py` 是自检脚本（接口回归、界面三出口、身份链路），其中 `_regress.py` / `_ui_check.py` / `_e2e.py` 需要真实数据与 Playwright，示例数据下跑不出结果；`_user_check.py` / `_who_check.py` 用 `templates/host.html` 假宿主，示例数据可直接跑。

---

## 4. 快速开始

```bash
python -m pip install -r requirements.txt

# 本机调试（不设口令）
PORT=8776 python serve.py
# 打开 http://127.0.0.1:8776/panel
```

生产必须设口令，否则任何能访问端口的人都能读数据、写结论：

```bash
PORT=8776 QC_TOKEN=<随机串> python serve.py
# 打开 http://127.0.0.1:8776/panel?k=<同一个串>
```

本地不接飞书时，访问 `/` 会加载 `templates/host.html`——它是个假宿主，会应答 SDK 需要的 RPC 消息，让面板以正常流程跑起来，便于调试。

两个实测踩到的坑：

- 若启动报 `缺少 waitress，请先执行：pip install -r requirements.txt`，但依赖确实装过，通常是
  `python` 与 `pip` 指向了不同解释器（Git Bash / MSYS 自带的 python 常见）。用绝对路径对齐即可：

  ```bash
  "/c/Program Files/Python312/python.exe" -m pip install -r requirements.txt
  "/c/Program Files/Python312/python.exe" serve.py
  ```

- 上面的 `PORT=8776 python serve.py` 是 bash 写法。Windows cmd / PowerShell 请用 `run.bat`（已设好
  PORT 与口令），或写成 `set PORT=8776 && python serve.py`。

---

## 5. 数据从哪来

`data/records.json` 由 `build_data.py` 从两处离线产物聚合：

- 批量跑批的 `上传内容_*.csv`：序号、任务编号、record_id、徽章、版式模板、对比图文件名、拟写入文本
- 跑批结论 `stream_结论.csv`：任务名称、项目子项

一条记录长这样：

```json
{
  "record_id": "recXXXX",
  "tn": "DEMO26000001-GC001",
  "badge": "一致",
  "tone": "green",
  "mode": "area",
  "img": "对比_01_示例.png",
  "reason": "通过：清单 964.18 平方米 与图面注记 964.18 平方米 一致。",
  "ai": "通过"
}
```

`badge/tone` 三态固定：`一致`（绿）、`不一致`（红，含「登记不改判」）、`并列核对`（灰）。**不允许第四种状态**——状态一多，统计口径就散了。

`build_people.py` 走的是另一条路：不调通讯录接口（应用的通讯录数据权限范围通常只覆盖创建者部门，查外部门一律报 `41050`），而是**读多维表格自己的人员字段**——这类字段的单元格值就是 `{id, name}` 成对返回，不需要通讯录权限。四个人员字段全扫，去重后即得映射。

---

## 6. 接口

除 `/panel` 外都需要口令（`?k=` 或 `X-QC-Token` 头）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/panel` | 面板页面 |
| GET | `/api/records` | 列表（record_id / 任务号 / 徽章 / 阶段） |
| GET | `/api/record/<rid>` | 单条详情：判定理由、证据图、历史、当前状态 |
| GET | `/api/state/<rid>` | 只取状态：结论、判定人、判定时间 |
| GET | `/api/writeback` | 写回配置：开关、字段 id、动作→选项文本映射 |
| POST | `/api/decide/receipt` | 前端写完飞书回报结果，落 `qc_writeback` |
| POST | `/api/decide` | 提交人工决定（见下） |
| GET | `/api/people?ids=a,b` | open_id → 姓名；未命中进 `missing`，不编造 |
| GET | `/health` | 存活与计数（不含任何业务字段） |

`POST /api/decide` 参数：

```json
{
  "record_id": "recXXXX",
  "action": "确认 | 打回 | 撤销 | 附言",
  "actor": "姓名",
  "actor_id": "ou_xxx",
  "codes": ["P-013", "E15"],
  "note": "补充说明"
}
```

- `action` 做业务话术归一：通过 / 合格 → `确认`，不合格 / 退回 → `打回`。
- `附言` **不改变状态**，只追加一条记录。状态查询会排除它，避免「写了句备注就等于做了决定」。
- `打回` 必须带说明，否则 400。
- 返回里带 `fb_text`：一句可直接写回多维表格的成品话术。

---

## 7. 留痕：四张表与三条约束

| 表 | 作用 | 关键点 |
|---|---|---|
| `qc_run` | 一次判定的一轮运行 | 材料指纹（附件名 + 结论文字 + 模板的 sha1 前 16 位）一变，旧判定自动失效 |
| `qc_result` | 每条记录一行 | `ai_conclusion` 与 `conclusion` **分列**，AI 原判永不被覆盖 |
| `qc_action` | 只追加，不修改 | 谁、何时、点了什么、附言是什么，全部留 |
| `qc_writeback` | 写飞书的回执 | 哪个字段写成了什么、成没成、失败原因；`by_id` 是宿主判定的登录人 |

三条不可破的约束：

1. **AI 原判只写一次**，人工结论写另一列。任何「直接改 AI 判定」的实现都是错的。
2. **动作只追加**，不 update、不 delete。改主意就再追加一条 `撤销`。
3. **附言不是决策**。查当前状态时必须过滤掉，否则附言会顶掉真实的判定人。

`qc_action` 里 `actor` 存姓名、`actor_id` 存完整 open_id（独立列，不是埋在 JSON 里）——写回多维表格的人员字段时，只认 open_id，写姓名不报错但字段会是空的。

---

## 8. 界面与人工的三个出口

400px 宽度下的信息层级：顶栏（当前登录人 / SDK 来源）→ 判定卡（徽章 + 理由）→ 数值对撞条（应然 vs 实然）→ 对比图 → 标签页（明细 / 判据 / 执行 / 留痕）→ 底部三个按钮。

| 出口 | 行为 |
|---|---|
| 不合格·退回 | 弹规则号多选（chips）+ 字数计数 + 成品句预览，必须选或填说明 |
| 通过 | 二次确认，弹窗里显示将要写入的话术 |
| 附言 | 只留痕，不改状态 |

弹窗打开期间，面板会冻结对当前记录的跟随（防止点着点着记录被切走），提交前再核一次 record_id。

---

## 8.5 写回多维表格：由点按钮的那个人写，不是应用

**身份是这个设计的核心。** 服务端如果用应用密钥（bot）写，多维表格的「修改人」会记成
应用，查不出是谁判的。所以这里把写动作放在**浏览器**里：宿主用当前登录人的身份调
`table.setCellValue`，修改人就是他本人，越权则由宿主拒绝——等于免费按人鉴权一次。

```
点「通过」 → POST /api/decide            服务端只写本地 qc.db，返回 action_id
          → SDK table.setCellValue(...)  这条用登录人身份，修改人 = 他
          → POST /api/decide/receipt     成没成都回报，落 qc_writeback
```

**顺带解决「字段读不到」。** 结论字段 `fldMTrdVhY`（审核意见，动态单选）的选项源表
服务端没有读权限（返回 `800030104`），但客户端以登录人身份 `getFieldMetaList()`
能读到全部选项。所以结论能落进这个本该最合适的字段。

**默认关闭**，`QC_WB_ENABLED=0`：只写本地，不碰多维表格。开启后写入行为：

| 情况 | 表现 |
|---|---|
| 当前人在此表只读 | 直接报「宿主拒绝写入」，不去试错 |
| 字段没有对应选项 | 报「字段无「通过」选项（现有：通过/不合格）」，不瞎写 |
| 宿主返回 false / 抛异常 | 原文记进 `qc_writeback.error`，界面提示「飞书写入失败：…」 |
| 本地已落库、表没写成 | 决策保留，回执单独记 `ok=0`——两者分开才查得清 |

**两点如实说明：**

1. **撤销不往回清字段**。点撤销只把本地结论退回 AI 原判，表里已经写进去的字还在。
2. **仍不是不可否认**。身份是宿主给的，比前端自报强得多；但绕过面板直接调接口，服务端
   仍不验签。要彻底做到，只能服务端拿 `user_access_token` 反查通讯录——那就要把 token
   交给服务端，权衡后没做。

---

## 9. 已知限制

- **不是纯前端包**。页面依赖本服务的 HTTP 接口与 SQLite；若要由平台构建部署，需要把数据层改成直接读多维表格字段，这一层没做。
- **按人鉴权未做**。能「认出」人（取 open_id、查姓名、落留痕），但不能「验证」人：`actor_id` 由前端 POST 上来，服务端不验签，改请求即可冒名。现在的性质是可追溯，不是不可否认。
- **`?k=` 是门槛不是鉴权**。真正的可见范围由外层程序（谁能打开这个多维表格、谁能添加插件）决定。
- **写回只在模拟宿主上验证过**。链路已跑通（`_write_path_check.py` 14/14，且在 `git clone`
  出来的干净副本上复跑过一次，依赖装齐即可重现），但没人在真实多维表格里点过一次，所以开关
  默认关闭。真实环境的字段权限、选项文本是否一致，仍需实测。
- **撤销不清表里字段**。只回退本地结论。
- **对比图在服务端生成**，因此图目录 `img/` 必须随服务一起部署（或用 `QC_IMG_DIR` 指向别处），仓库里不含图片。

---

## 10. 安全说明

- 服务**不向任何第三方发起请求**，不加载外部 CDN，SDK 为本地副本。
- 所有凭据只来自环境变量（`QC_TOKEN` / `QC_BASE_TOKEN` / `QC_TASK_TABLE_ID`），代码里没有内置值。
- 本仓库不含真实业务数据：`data/` 只有 `*.example.json`，`.gitignore` 明确排除 `records.json`、`people.json`、`*.db`、`img/`、`*.log`、`.env.local`。
- 输出的 `fb_text` 只做文本拼接，不发网络请求。

---

## 11. 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `QC_TOKEN` | 空 | 访问口令。为空 = 不鉴权，仅限本机调试 |
| `PORT` / `QC_PORT` | 8776 | 监听端口 |
| `QC_HOST` | 0.0.0.0 | 监听地址 |
| `QC_IMG_DIR` | 项目内 `img/` | 对比图目录 |
| `QC_BASE_TOKEN` | 无 | `build_people.py` 用，指向多维表格 app token |
| `QC_TASK_TABLE_ID` | 无 | `build_people.py` 用，指向任务表 |
| `QC_WB_ENABLED` | `0` | 写回多维表格总开关 |
| `QC_WB_CONC_FIELD` | `fldMTrdVhY` | 结论写哪个字段（单选，按选项文本匹配） |
| `QC_WB_NOTE_FIELD` | `fld01QMpX1` | 说明写哪个字段（文本），留空则不写 |
| `QC_WB_ACTOR_FIELD` | 空 | 人员字段 id，填了就以登录人 open_id 写入 |
| `LARK_CLI` | 自动探测 | lark-cli 的 `run.js` 路径 |

---

## 12. 许可

内部工具，未公开发布。如需复用请联系维护者。
