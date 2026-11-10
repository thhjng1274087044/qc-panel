# -*- coding: utf-8 -*-
"""生成「平台规则 vs 我们现状」的代码对照截图。

每个冲突一张图：A 在上（他们的规则 / 应然），B 在下（我们的现状 / 实然）。
行号与原文均从仓库直接取，不手写，避免截图与代码不一致。
"""
from __future__ import annotations

import html
import json
from pathlib import Path

OURS = Path(r"E:\质检skill\_plugin_server")
THEIRS = Path(r"E:\XDLFC\_tmp\aisr_repo")
OUT = OURS

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#fff;font-family:"Microsoft YaHei","Segoe UI",sans-serif;
     color:#0F172A;-webkit-font-smoothing:antialiased}
.card{width:1180px;padding:28px 32px 40px;background:#fff}
.head{display:flex;align-items:flex-start;gap:14px;margin-bottom:6px}
.no{flex:0 0 auto;width:38px;height:38px;border-radius:9px;background:#0F172A;color:#fff;
    font:700 17px/38px "Segoe UI",sans-serif;text-align:center}
.tt{flex:1 1 auto;padding-top:2px}
.tt h1{margin:0;font-size:20px;font-weight:700;letter-spacing:.2px}
.tt p{margin:5px 0 0;font-size:12.5px;color:#64748B}
.pill{flex:0 0 auto;padding:5px 12px;border-radius:99px;font-size:12px;font-weight:700;color:#fff}
.rule{height:3px;background:linear-gradient(90deg,#0F172A 0%,#0F172A 22%,#E2E8F0 22%);margin:14px 0 18px}
.pane{border-radius:9px;overflow:hidden;border:1px solid #E2E8F0;margin-bottom:14px}
.pane .bar{display:flex;align-items:center;gap:9px;padding:9px 13px;font-size:12.5px;font-weight:700}
.pane .bar .tag{font-size:10.5px;font-weight:700;padding:2.5px 7px;border-radius:4px;letter-spacing:.5px}
.pane .bar .path{font-family:Consolas,monospace;font-size:12px;font-weight:600}
.A{border-left:4px solid #1C5294}
.A .bar{background:#F1F6FB;color:#1C5294}
.A .bar .tag{background:#1C5294;color:#fff}
.B{border-left:4px solid #C0392B}
.B .bar{background:#FDF3F2;color:#A93226}
.B .bar .tag{background:#C0392B;color:#fff}
pre{margin:0;padding:11px 0 12px;font:12.5px/1.62 Consolas,"Cascadia Mono","Microsoft YaHei",monospace;
    background:#FCFCFD;overflow-x:auto}
.row{display:flex;white-space:pre}
.ln{flex:0 0 46px;text-align:right;padding-right:13px;color:#B6C2CF;user-select:none}
.cd{flex:1 1 auto;padding-right:14px;white-space:pre}
.A .hl{background:#E7F0FA}
.B .hl{background:#FBE9E7}
.mark{color:#C0392B;font-weight:700}
.why{border:1px solid #E2E8F0;border-left:4px solid #0F172A;border-radius:9px;
     padding:12px 15px;background:#FAFAFB;font-size:13px;line-height:1.75}
.why b{color:#C0392B}
.foot{margin-top:13px;font-size:11.5px;color:#94A3B8;display:flex;justify-content:space-between}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{background:#0F172A;color:#fff;font-weight:700;text-align:left;padding:9px 11px;font-size:12px}
td{padding:8px 11px;border-bottom:1px solid #ECF0F4;vertical-align:top;line-height:1.55}
tr:nth-child(even) td{background:#FAFBFC}
td.k{font-weight:700;white-space:nowrap}
.ok{color:#15803D;font-weight:700}
.no2{color:#C0392B;font-weight:700}
.mid{color:#B7791F;font-weight:700}
code{font-family:Consolas,monospace;font-size:12px;background:#F1F5F9;padding:1px 4px;border-radius:3px}
"""


def read_lines(path: Path, start: int, end: int) -> list[tuple[int, str]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [(i, lines[i - 1]) for i in range(start, min(end, len(lines)) + 1)]


def block(path: Path, start: int, end: int, hl: tuple[int, ...] = (), label: str = "") -> str:
    rows = []
    for no, text in read_lines(path, start, end):
        cls = " hl" if no in hl else ""
        code = html.escape(text).replace(" ", "&nbsp;")
        rows.append(f'<div class="row{cls}"><span class="ln">{no}</span><span class="cd">{code}</span></div>')
    rel = path.relative_to(OURS if OURS in path.parents else THEIRS).as_posix()
    tag = label or ""
    return (
        f'<div class="bar"><span class="tag">{tag}</span>'
        f'<span class="path">{rel}:{start}-{end}</span></div>'
        f'<pre>{"".join(rows)}</pre>'
    )


def pane(side: str, title_tag: str, blocks: list[str]) -> str:
    return f'<div class="pane {side}">{blocks[0]}</div>'


def card(no: str, title: str, sub: str, sev: str, color: str,
         a_blocks: list[str], b_blocks: list[str], why: str, foot_right: str = "") -> str:
    a = "".join(f'<div class="pane A">{b}</div>' for b in a_blocks)
    b = "".join(f'<div class="pane B">{b}</div>' for b in b_blocks)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="card">
  <div class="head"><div class="no">{no}</div>
    <div class="tt"><h1>{title}</h1><p>{sub}</p></div>
    <div class="pill" style="background:{color}">{sev}</div></div>
  <div class="rule"></div>
  {a}{b}
  <div class="why">{why}</div>
  <div class="foot"><span>左：应然 —— 平台既有实现（git clone 取原文）</span><span>{foot_right}</span></div>
</div></body></html>"""


THEIR_TAG = "他们的规则"
OUR_TAG = "我们的现状"


def make_cards() -> list[tuple[str, str]]:
    cards: list[tuple[str, str]] = []

    # ── 1 写权限红线 ─────────────────────────────────────────────
    c1 = card(
        "01", "写权限红线：任务表被登记为「只读」",
        "我们把结论写回任务表 fldtwiy2VU —— 这张表在他们代码里属于受保护目标",
        "红线 · 阻断", "#C0392B",
        [block(THEIRS / "config.py", 76, 78, hl=(76, 78), label="主系统标识"),
         block(THEIRS / "feishu/write_gateway.py", 18, 26, hl=(19, 22, 23), label="保护清单"),
         block(THEIRS / "feishu/write_gateway.py", 60, 67, hl=(62, 63, 64, 65), label="唯一闸口")],
        [block(OURS / "app.py", 425, 432, hl=(429, 431), label="现在只落本地"),
         block(OURS / "app.py", 434, 443, hl=(434, 441, 443), label="待接的回写句")],
        "我们的 <code>fb_text</code> 已经是「通过：…／不合格（P-013）：…」这种飞书成品句，"
        "但只在前端预览，<b>还没有真的发送</b>。一旦接上写回，"
        "目标 app_token <code>QQn3bB0yGacm5hsROy9cmRLVnec</code> 与 table_id "
        "<code>tblE8IpW9xctP8v9</code> <b>同时命中</b> PROTECTED_APP_CONFIGS 与 "
        "PROTECTED_TABLE_CONFIGS，<code>sdk.batch_update</code> 会在发请求前抛 WriteDenied。"
        "所以现在不报错，并进去接了才炸。",
        "因此这一条要业务裁定，不是改代码能解决的",
    )
    cards.append(("conflict_01_write.png", c1))

    # ── 2 框架 ───────────────────────────────────────────────────
    c2 = card(
        "02", "应用框架：FastAPI router 装配 vs Flask 单文件",
        "平台 27 个 router 由 create_app 装配；我们 14 条路由都挂在同一个 Flask 实例上",
        "结构性 · 必改", "#C0392B",
        [block(THEIRS / "api/app_factory.py", 73, 84, hl=(74, 76, 84), label="装配"),
         block(THEIRS / "api/routers/tables.py", 16, 26, hl=(16, 26), label="router 形态")],
        [block(OURS / "app.py", 60, 61, hl=(60,), label="实例"),
         block(OURS / "app.py", 248, 260, hl=(248, 253, 258), label="14 条路由 / 486 行")],
        "他们新增端点 = 在 <code>api/routers/</code> 建文件 + 在 <code>app_factory</code> 里 include，"
        "纯逻辑进 <code>services/</code>。我们是 486 行单文件，业务、路由、建表、压缩、鉴权全在一个模块里。"
        "拆的时候有个坑：<code>proxy</code> 的 <code>/api/{path:path}</code> 通配<b>必须仍是最后 include</b>，"
        "否则新路由会被它吞掉。",
        "拆完后 route_inventory 基线要重跑，否则 CI 红",
    )
    cards.append(("conflict_02_framework.png", c2))

    # ── 3 鉴权 ───────────────────────────────────────────────────
    c3 = card(
        "03", "鉴权：两套口令，且失效方向相反",
        "平台只认 Authorization Bearer；我们用自定义头 + query 参数，令牌为空时是「放行」而非「拒绝」",
        "安全 · 必改", "#C0392B",
        [block(THEIRS / "api/routers/_request.py", 30, 37, hl=(33, 35, 37), label="端点鉴权"),
         block(THEIRS / "infra/auth.py", 225, 233, hl=(227, 228, 231), label="常量时间 + fail-closed")],
        [block(OURS / "app.py", 68, 80, hl=(71, 76, 77), label="自定义中间件")],
        "两个致命差异：<br>"
        "① <b>失效方向</b>：他们 <code>if not expected: return False</code> —— 没配令牌就<b>全拒</b>；"
        "我们 <code>if not QC_TOKEN: return None</code> —— 没配令牌就<b>全放</b>，只有本机调试安全。"
        "并进平台后一旦漏配，等于把接口敞开。<br>"
        "② <b>头名与位置</b>：<code>X-QC-Token</code> 平台网关不认识；"
        "<code>?k=</code> 会把令牌写进访问日志和 Referer。前端 <code>apiFetch</code> 每一处都要改。",
        "我们现在发的是 ?k=qc-74f3a91c2b6e",
    )
    cards.append(("conflict_03_auth.png", c3))

    # ── 4 存储 ───────────────────────────────────────────────────
    c4 = card(
        "04", "存储：手写 SQLite DDL vs infra.models + Alembic",
        "三张留痕表是进程内 SQLite，平台事实源是 PostgreSQL，schema 只允许写 infra/models.py",
        "结构性 · 必改", "#B7791F",
        [block(THEIRS / "infra/orm.py", 79, 89, hl=(79, 80, 84), label="事务边界"),
         block(THEIRS / "infra/models.py", 25, 33, hl=(26, 28), label="唯一 schema 源")],
        [block(OURS / "app.py", 139, 152, hl=(140,), label="DDL 内嵌在代码里"),
         block(OURS / "app.py", 152, 176, hl=(153, 156, 166), label="三表 + 索引")],
        "我们是「启动时 CREATE TABLE IF NOT EXISTS」，改字段靠手写迁移甚至删库重建；"
        "平台是「schema 只在 models.py 声明，改表必须 Alembic 版本」。"
        "另外 <code>id INTEGER PRIMARY KEY AUTOINCREMENT</code>、<code>TEXT</code> 时间戳这些写法，"
        "换成 PG 后类型与自增策略都要重写（<code>Mapped[...]</code> / <code>server_default</code>）。",
        "qc_run 的幂等键要落成唯一索引才有效",
    )
    cards.append(("conflict_04_storage.png", c4))

    # ── 5 汇总体检表 ─────────────────────────────────────────────
    rows = [
        ("框架", "FastAPI + api/routers/", "Flask，486 行单文件", "改", "no2"),
        ("路由装配", "create_app 装配 27 个 router", "14 个装饰器同文件", "改", "no2"),
        ("鉴权", "Bearer + fail-closed", "X-QC-Token / ?k=，空令牌即放行", "改", "no2"),
        ("存储", "PostgreSQL + ORM + Alembic", "SQLite 手写 DDL，4 张表", "改", "no2"),
        ("二进制", "SILO / content_store", "本地 img/ 36M，凭 QC_IMG_DIR 拼路径", "改", "mid"),
        ("日志", "infra.log.get_logger", "print 66 处", "改", "mid"),
        ("路径", "config.BASE_DIR", "dirname(__file__) 3 处", "改", "mid"),
        ("测试", "tests/ + pytest（testpaths=tests）", "_regress/_ui_check 在 tools/ 下，CI 收不到", "改", "no2"),
        ("飞书写权", "主系统只读，写则 WriteDenied", "待写任务表，成品句已生成未发送", "裁定", "no2"),
        ("静态检查", "ruff 四类必须 0", "本地按同口径复跑已 0", "过", "ok"),
        ("根目录", "只留 app/config/utils/preflight", "在 tools/qc_panel/ 子目录", "过", "ok"),
        ("注释提交", "一律中文", "中文", "过", "ok"),
    ]
    body = "".join(
        f'<tr><td class="k">{k}</td><td>{a}</td><td>{b}</td>'
        f'<td class="{cls}">{v}</td></tr>'
        for k, a, b, v, cls in rows
    )
    c5 = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="card">
  <div class="head"><div class="no">05</div>
    <div class="tt"><h1>总体体检：12 项里 8 项要改，1 项待裁定，3 项已合规</h1>
      <p>口径来自 AGENTS.md 十八条、CI 三个 job、infra 里标注「唯一实现」的模块</p></div>
    <div class="pill" style="background:#0F172A">汇总</div></div>
  <div class="rule"></div>
  <table><thead><tr><th style="width:88px">项</th><th>他们</th><th>我们</th>
  <th style="width:58px">判定</th></tr></thead><tbody>{body}</tbody></table>
  <div class="why" style="margin-top:16px">
  真正会卡住并入的只有一条：<b>结论要写的那张表，在他们的保护清单里</b>。
  其余八项是工程改造，能改、也该改，但不改也能先进分支；
  这一条不改，功能在平台里跑不起来 —— 而且 <code>test_write_gateway.py</code> 有守卫，
  想绕也绕不过去。</div>
  <div class="foot"><span>数据来源：E:\\XDLFC\\_tmp\\aisr_repo（main a073bc3）</span>
  <span>核对于 2026-11-09</span></div>
</div></body></html>"""
    cards.append(("conflict_05_summary.png", c5))
    return cards


def make_overview() -> tuple[str, str]:
    """现状架构图：数据从飞书选中流到本机，三个红框是并入时的缺口。"""
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS}
.wrap{{width:1180px;padding:28px 32px 40px}}
.flow{{display:flex;align-items:stretch;gap:0;margin-top:6px}}
.box{{flex:1 1 0;border:1px solid #CBD5E1;border-radius:9px;padding:12px 13px;background:#fff;min-width:0;min-height:120px}}
.box .h{{font-size:12.5px;font-weight:700;color:#0F172A;margin-bottom:3px}}
.box .p{{font-family:Consolas,monospace;font-size:11px;color:#64748B;word-break:break-all;line-height:1.5}}
.box .d{{font-size:11.5px;color:#475569;line-height:1.6;margin-top:6px}}
.box.fs{{background:#F8FAFC;border-color:#94A3B8}}
.box.ours{{background:#F1F6FB;border-color:#1C5294}}
.box.data{{background:#FDF8F3;border-color:#D8B48A}}
.arrow{{flex:0 0 96px;display:flex;flex-direction:column;align-items:center;justify-content:center;
        color:#64748B;font-size:9.5px;text-align:center;line-height:1.35;padding:0 5px}}
.arrow span{{max-width:86px;word-break:break-word}}
.arrow .l{{height:1px;width:58px;background:#94A3B8;position:relative;margin:5px 0}}
.arrow .l:after{{content:"";position:absolute;right:-1px;top:-3px;border-left:6px solid #94A3B8;
                 border-top:3.5px solid transparent;border-bottom:3.5px solid transparent}}
.lbl{{font-size:12px;font-weight:700;color:#0F172A;margin:22px 0 8px}}
.gap{{display:flex;gap:10px;margin-top:20px}}
.g{{flex:1 1 0;border:1px solid #F3C4BE;border-left:4px solid #C0392B;border-radius:8px;
    padding:10px 12px;background:#FDF3F2}}
.g .t{{font-size:12px;font-weight:700;color:#A93226;margin-bottom:4px}}
.g .b{{font-size:11.5px;color:#475569;line-height:1.65}}
.tun{{margin-top:18px;border:1px dashed #94A3B8;border-radius:9px;padding:11px 14px;background:#FAFAFB;
      font-size:11.5px;color:#475569;line-height:1.7}}
.tun b{{color:#0F172A}}
</style></head>
<body><div class="wrap">
  <div class="head"><div class="no">00</div>
    <div class="tt"><h1>我们现在是什么形态：本机 Flask + 隧道，飞书只是外壳</h1>
      <p>结论、样本、图片、口令全在本机；飞书里那一栏只是 iframe 指向隧道地址</p></div>
    <div class="pill" style="background:#1C5294">现状</div></div>
  <div class="rule"></div>

  <div class="flow">
    <div class="box fs"><div class="h">飞书多维表格</div>
      <div class="p">左侧原生表格 400px</div>
      <div class="d">选中哪一行是飞书自己的状态，我们不参与</div></div>
    <div class="arrow"><span>postMessage</span><div class="l"></div><span>getSelection<br>返回 recordId</span></div>
    <div class="box ours"><div class="h">右栏面板 panel.html</div>
      <div class="p">templates/panel.html · 1014 行</div>
      <div class="d">官方 SDK 取选中 → 按 recordId 查结果 → 人工三个出口</div></div>
    <div class="arrow"><span>fetch</span><div class="l"></div><span>X-QC-Token<br>?k=</span></div>
    <div class="box ours"><div class="h">本机 Flask</div>
      <div class="p">app.py · 486 行 · :8776</div>
      <div class="d">14 条路由，鉴权/压缩/建表全在这一个文件</div></div>
    <div class="arrow"><span>sqlite3</span><div class="l"></div><span>直接拼路径</span></div>
    <div class="box data"><div class="h">本机数据</div>
      <div class="p">qc.db · records.json · img/ 36M</div>
      <div class="d">留痕 4 表 + 90 条样本 + 证据图</div></div>
  </div>

  <div class="tun">对外暴露：<b>cloudflared</b> 与 <b>ngrok</b> 两条隧道 → 公网 https → 填进飞书「自定义扩展 · 运行地址」。
  换地址要重填；ngrok 免费版首屏多一层 Visit Site 确认页。</div>

  <div class="lbl">并入平台时的三个缺口</div>
  <div class="gap">
    <div class="g"><div class="t">① 结论没有真的写回飞书</div>
      <div class="b">只生成了 <code>fb_text</code> 成品句在前端预览，没有发 API。<br>接上就是第 01 张图那条红线。</div></div>
    <div class="g"><div class="t">② 数据在本机，不在平台</div>
      <div class="b">SQLite 与 img/ 都在进程旁边；平台事实源是 PG + SILO，<br>多实例部署时本机文件读不到。</div></div>
    <div class="g"><div class="t">③ 自建口令与隧道</div>
      <div class="b">X-QC-Token / ?k= 平台网关不认识；隧道地址是外部依赖，<br>平台内应直接挂在自己的域名下。</div></div>
  </div>
  <div class="foot"><span>行号取自 E:\\质检skill\\_plugin_server 与 E:\\XDLFC\\_tmp\\aisr_repo</span>
  <span>核对于 2026-11-09</span></div>
</div></body></html>"""
    return ("conflict_00_overview.png", doc)


def shoot(cards):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_page(viewport={"width": 1180, "height": 900}, device_scale_factor=2)
        for name, doc in cards:
            pg.set_content(doc)
            pg.wait_for_timeout(260)
            el = pg.query_selector(".card") or pg.query_selector(".wrap")
            el.screenshot(path=str(OUT / name))
            print("写入", name)
        b.close()


if __name__ == "__main__":
    shoot([make_overview()] + make_cards())
    print(json.dumps({"ok": True}, ensure_ascii=False))
