# -*- coding: utf-8 -*-
"""质检插件本地服务 —— 飞书多维表格扩展的「运行地址」指向这里。

为什么能这么干（已从 @lark-base-open/js-sdk@1.0.2 源码逐字确认）：
  插件页与飞书宿主之间只有一种通信：window.parent.postMessage 握手协议。
    page -> host   {type:"handshake", payload:{proactive:true}}
    host -> page   {type:"handshake", payload:{prefixId}}
    page -> host   {type:"invoke", id, payload:{key, params, prefixId}}
    host -> page   {type:"callback", id, payload:{res}}
    host -> page   {type:"event", payload:{eventName, params}}
  且握手类里写的是 allowedHostOrigin=["*"] —— 不校验来源域名。
  所以：页面放自己域名、SDK 由自己域名托管，照样通，不需要 opdev 打包。

唯一硬要求：页面必须被 iframe 嵌进飞书（window.parent !== window），
否则 SDK 直接把握手判失败，getSelection() 永远不返回。

启动：python app.py
接口：
  GET  /                     本地「假飞书宿主」模拟器（不用于生产）
  GET  /panel                插件面板页（飞书里填这个地址）
  GET  /probe                上下文探针
  GET  /api/records          样本列表
  GET  /api/record/<rid>     单条样本 + 判据 + 留痕
  GET  /api/state/<rid>      当前人工决策状态
  POST /api/decide           人工通过 / 打回（只追加，可撤销）
  POST /api/diag             探针上报（落 qc_diag，便于远程排查）
  GET  /api/stats            统计
  GET  /health               健康检查（穿透后先打这个）
"""
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, send_file

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
# 图片目录可外部指定；默认落在项目内 img/，便于整包搬到服务器
IMG_DIR = os.environ.get("QC_IMG_DIR") or os.path.join(HERE, "img")
DB = os.path.join(DATA, "qc.db")
THUMB = os.path.join(DATA, "thumb")
RECORDS_JSON = os.path.join(DATA, "records.json")
# 开源/审核副本不含真实业务数据：records.json 不存在时读 .example.json，
# 保证 clone 下来直接能跑、界面有内容，且库里没有任何任务号或工程量。
RECORDS_DEMO = os.path.join(DATA, "records.example.json")

RULE_VER = "2026-11-05.1"      # 判据版本：口径一变就要涨，否则留痕对不上
WORKER_VER = "stream_run/1.4"

# 访问口令：设置后所有数据接口与页面都必须带它（?k= 或 X-QC-Token 头）
# 不设置 = 不做鉴权，只适合本机调试。生产环境务必设置。
QC_TOKEN = (os.environ.get("QC_TOKEN") or "").strip()

# ── 写回飞书 ──────────────────────────────────────────────────────────────
# 写回由**浏览器**用登录人身份完成（SDK setCellValue），服务端不下场、不持 token。
# 这样飞书侧的「修改人」就是点按钮的人，而不是应用的机器人身份。
#   默认关闭：先保证「库里有记录、飞书不被误改」。开启：QC_WB_ENABLED=1
WB_ENABLED = (os.environ.get("QC_WB_ENABLED") or "0").strip() == "1"
# 结论落哪：默认「审核意见」动态单选。服务端读它的选项源会 800030104，
# 但客户端以登录人身份读 meta 能拿到选项 —— 这正是走前端写的另一个好处。
WB_CONC_FIELD = (os.environ.get("QC_WB_CONC_FIELD") or "fldMTrdVhY").strip()
# 说明落哪：默认「质量问题说明」文本，实测干净、与 AI 原话不冲突
WB_NOTE_FIELD = (os.environ.get("QC_WB_NOTE_FIELD") or "fld01QMpX1").strip()
# 人员字段：留空 = 不写。填字段 id 则以登录人 open_id 写入。
WB_ACTOR_FIELD = (os.environ.get("QC_WB_ACTOR_FIELD") or "").strip()
# 动作 → 字段选项文本。写之前会拿实际选项名核对，对不上就不写。
WB_VALUE_MAP = {"确认": "通过", "打回": "不合格"}

app = Flask(__name__, static_folder=None)
app.config["JSON_AS_ASCII"] = False

# 隧道带宽往往只有几十 KB/s，必须压缩：SDK 主包 761KB → gzip 后约 150KB
COMPRESSIBLE = ("text/", "application/json", "application/javascript",
                "image/svg+xml", "application/xml")


@app.before_request
def _auth():
    """访问口令。只有 SDK 静态文件与健康检查放行 —— 它们不含业务数据。"""
    if not QC_TOKEN or request.method == "OPTIONS":
        return None
    p = request.path
    if p.startswith("/health") or p.startswith("/vendor/") or p.startswith("/favicon"):
        return None
    tok = request.headers.get("X-QC-Token") or request.args.get("k") or ""
    if hmac.compare_digest(tok, QC_TOKEN):
        return None
    if p.startswith("/api/"):
        return jsonify(ok=False, error="unauthorized · 缺少访问口令"), 401
    return ("需要访问口令：请在地址后加 ?k=…", 401,
            {"Content-Type": "text/plain; charset=utf-8"})


@app.after_request
def _gzip(resp):
    try:
        # 页面永不缓存：改了模板必须立刻生效，否则飞书里会跑旧版
        if (resp.mimetype or "") == "text/html":
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        if resp.status_code != 200:
            return resp
        if "gzip" not in (request.headers.get("Accept-Encoding") or ""):
            return resp
        ct = (resp.content_type or "").lower()
        # 先筛类型：图片不压（压了反而更大，且没必要读进内存）
        if not any(ct.startswith(p) for p in COMPRESSIBLE):
            return resp
        if resp.direct_passthrough:      # send_from_directory 是流式，需转成内存态才能压
            resp.direct_passthrough = False
        data = resp.get_data()
        if len(data) < 1024:
            return resp
        packed = gzip.compress(data, 6)
        if len(packed) >= len(data):
            return resp
        resp.set_data(packed)
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(packed))
        resp.headers.add("Vary", "Accept-Encoding")
    except Exception:
        pass
    return resp


# ---------------------------------------------------------------- 数据
_records = []
_by_rid = {}


def load_records():
    global _records, _by_rid
    path = RECORDS_JSON if os.path.isfile(RECORDS_JSON) else RECORDS_DEMO
    if not os.path.isfile(path):
        _records, _by_rid = [], {}
        return
    with open(path, encoding="utf-8") as f:
        _records = json.load(f)
    _by_rid = {r["record_id"]: r for r in _records}


# ------------------------------------------------------------ 人员姓名
# 为什么不用通讯录接口：应用（bot）的通讯录数据权限只覆盖「办公室」2 人，
# 查工程测绘部 / 房产测绘部的人一律 41050 no user authority error。
# 但任务表的人员字段值是 {id, name} 成对返回的，读记录就有姓名，不需要通讯录权限。
# 映射表由 build_people.py 生成；miss 的 id 不编造，前端显示缩写。
PEOPLE_JSON = os.path.join(DATA, "people.json")
PEOPLE_DEMO = os.path.join(DATA, "people.example.json")
_people = None


def load_people():
    global _people
    if _people is not None:
        return _people
    _people = {}
    path = PEOPLE_JSON if os.path.isfile(PEOPLE_JSON) else PEOPLE_DEMO
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            _people = {
                k: v.strip()
                for k, v in raw.items()
                if isinstance(k, str) and isinstance(v, str) and v.strip()
            }
        except Exception as e:
            print(f"[people] 读取失败，按空处理：{e}")
            _people = {}
    return _people


def material_hash(rec):
    """材料指纹：附件或结论文字一变，指纹就变，旧判定自动作废。"""
    raw = "|".join([
        str(rec.get("img") or ""),
        str(rec.get("reason") or ""),
        str(rec.get("mode") or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 留痕三表
DDL = """
CREATE TABLE IF NOT EXISTS qc_run (
  id           TEXT PRIMARY KEY,      -- run_id
  record_id    TEXT NOT NULL,
  material_hash TEXT NOT NULL,
  rule_ver     TEXT NOT NULL,
  worker_ver   TEXT NOT NULL,
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  status       TEXT NOT NULL          -- running / done / failed
);
CREATE INDEX IF NOT EXISTS ix_run_rec ON qc_run(record_id, material_hash);

CREATE TABLE IF NOT EXISTS qc_result (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL,
  record_id    TEXT NOT NULL,
  ai_conclusion TEXT NOT NULL,        -- AI 判定的原值，永不被人工覆盖
  conclusion   TEXT NOT NULL,         -- 当前生效结论
  reason       TEXT,
  evidence_img TEXT,
  mode         TEXT,
  version      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL,
  UNIQUE(record_id, run_id, version)
);

CREATE TABLE IF NOT EXISTS qc_action (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id    TEXT NOT NULL,
  run_id       TEXT,
  action       TEXT NOT NULL,         -- 确认 / 打回 / 撤销 / 附言
  actor        TEXT,                  -- 姓名（查不到时是 id 缩写）
  actor_id     TEXT,                  -- 登录人 open_id；写飞书人员字段用这个，不是 actor
  note         TEXT,
  payload      TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_act_rec ON qc_action(record_id, id);

-- 写飞书回执：谁、在什么时间、把哪条记录哪个字段写成了什么，成没成。
-- 写入动作由浏览器用登录人身份发出（SDK setCellValue），服务端只收结果。
-- 这一表不替代 qc_action：qc_action 是业务决策，qc_writeback 是「飞书那边到底变没变」。
CREATE TABLE IF NOT EXISTS qc_writeback (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id    TEXT NOT NULL,
  action_id    INTEGER,               -- 关联 qc_action.id
  field_id     TEXT NOT NULL,
  value        TEXT,                  -- 实际写入值的 JSON
  ok           INTEGER NOT NULL,      -- 1 成功 / 0 失败
  error        TEXT,                  -- 失败原因，成功时为空
  by_id        TEXT,                  -- 登录人 open_id（宿主判定，不是自报）
  by_name      TEXT,
  written_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wb_rec ON qc_writeback(record_id, id);

CREATE TABLE IF NOT EXISTS qc_diag (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source       TEXT,
  payload      TEXT,
  ip           TEXT,
  ua           TEXT,
  created_at   TEXT NOT NULL
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(DATA, exist_ok=True)
    with db() as conn:
        conn.executescript(DDL)
        _migrate_actor_id(conn)


def _migrate_actor_id(conn):
    """老库补 qc_action.actor_id 列，并从 payload 里回填一次。

    为什么要独立列：actor_id 之前只埋在 payload 整包 JSON 里，写飞书人员字段时
    得先抠 JSON，容易漏、也容易被后面的 body 覆盖。落成列后一句 SELECT 就能拿到。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(qc_action)")}
    if "actor_id" not in cols:
        conn.execute("ALTER TABLE qc_action ADD COLUMN actor_id TEXT")
        print("[db] qc_action 补列 actor_id")
    # 回填：优先取 payload.actor_id，取不到就空着（不拿 actor 猜，actor 可能是缩写）
    rows = conn.execute(
        "SELECT id, payload FROM qc_action WHERE (actor_id IS NULL OR actor_id='') "
        "AND payload IS NOT NULL AND payload LIKE '%actor_id%'"
    ).fetchall()
    filled = 0
    for rid_, payload in rows:
        try:
            aid = (json.loads(payload or "{}") or {}).get("actor_id") or ""
        except Exception:
            aid = ""
        if isinstance(aid, str) and aid.strip():
            conn.execute("UPDATE qc_action SET actor_id=? WHERE id=?", (aid.strip(), rid_))
            filled += 1
    if filled:
        print(f"[db] qc_action 回填 actor_id {filled} 条")


def ensure_run(rec):
    """幂等：同 (record_id, material_hash, rule_ver) 只建一次 run。"""
    mh = material_hash(rec)
    run_id = f"{rec['record_id']}@{mh}"
    with db() as conn:
        row = conn.execute("SELECT id FROM qc_run WHERE id=?", (run_id,)).fetchone()
        if row:
            return run_id
        conn.execute(
            "INSERT INTO qc_run(id, record_id, material_hash, rule_ver, worker_ver, started_at, finished_at, status)"
            " VALUES (?,?,?,?,?,?,?,'done')",
            (run_id, rec["record_id"], mh, RULE_VER, WORKER_VER, now(), now()),
        )
        conn.execute(
            "INSERT INTO qc_result(run_id, record_id, ai_conclusion, conclusion, reason, evidence_img, mode, version, created_at)"
            " VALUES (?,?,?,?,?,?,?,1,?)",
            (run_id, rec["record_id"], rec.get("ai") or "通过", rec.get("ai") or "通过",
             rec.get("reason"), rec.get("img"), rec.get("mode"), now()),
        )
    return run_id


def latest_action(record_id):
    """最近一次「决策」动作。

    附言（备注）只补充意见、不改变结论，因此不能算决策 —— 否则一条已确认的
    记录在加过一句备注后，state 会变成「附言」，前端就把它当回「待确认」。
    """
    with db() as conn:
        return conn.execute(
            "SELECT * FROM qc_action WHERE record_id=? AND action IN ('确认','打回','撤销') "
            "ORDER BY id DESC LIMIT 1", (record_id,)
        ).fetchone()


def action_history(record_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM qc_action WHERE record_id=? ORDER BY id ASC", (record_id,)
        ).fetchall()


# ---------------------------------------------------------------- 页面
@app.get("/")
@app.get("/host")
def host():
    """假飞书宿主：在本地把面板放进 iframe 里，走和真飞书同一条协议路径。

    真飞书里用不到它；它的作用是让「面板 ↔ 宿主」这套消息能被自动测试覆盖，
    没有它就只能靠人点。"""
    return send_from_directory(os.path.join(HERE, "templates"), "host.html")


@app.get("/panel")
def panel():
    return send_from_directory(os.path.join(HERE, "templates"), "panel.html")


@app.get("/probe")
def probe():
    return send_from_directory(os.path.join(HERE, "templates"), "probe.html")


@app.get("/static/<path:fn>")
def static_files(fn):
    return send_from_directory(os.path.join(HERE, "static"), fn)


@app.get("/vendor/sdk/<path:fn>")
def sdk(fn):
    return send_from_directory(os.path.join(HERE, "vendor", "sdk"), fn)


@app.get("/img/<path:fn>")
def img(fn):
    base = os.path.basename(fn)                      # 防目录穿越
    if not base.lower().endswith(".png"):
        return jsonify(ok=False, error="only png"), 404
    w = request.args.get("w", type=int)
    if w and 100 <= w <= 2000:
        return send_file(_thumb(base, w), mimetype="image/webp",
                         max_age=86400, conditional=True)
    return send_from_directory(IMG_DIR, base, max_age=86400)


def _thumb(base, w):
    """按需生成窄图并落盘复用：隧道带宽只有几十 KB/s，原图 200-800KB 太重。"""
    dst = os.path.join(THUMB, f"{w}_{base}.webp")
    if os.path.isfile(dst):
        return dst
    os.makedirs(THUMB, exist_ok=True)
    from PIL import Image
    src = os.path.join(IMG_DIR, base)
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > w:
            im = im.resize((w, max(1, int(im.height * w / im.width))), Image.LANCZOS)
        im.save(dst, "WEBP", quality=82, method=4)
    return dst


# ---------------------------------------------------------------- 接口
@app.get("/health")
def health():
    return jsonify(ok=True, service="qc-plugin", records=len(_records),
                   people=len(load_people()), server_time=now())


@app.get("/favicon.ico")
def favicon():
    return ("", 204)     # 免掉一条 404 噪音


@app.get("/api/people")
def api_people():
    """按 open_id 批量换姓名。查不到的不编造，放进 missing 让前端退回缩写。"""
    book = load_people()
    ids = (request.args.get("ids") or "").strip()
    if not ids:
        return jsonify(ok=True, total=len(book))
    want = [s.strip() for s in ids.split(",") if s.strip()]
    miss = [k for k in want if k not in book]
    # 全都没命中时先怀疑「id 命名空间和映射表不一致」（例如 SDK 给的不是 open_id），
    # 而不是「这批人恰好都不在表里」——否则姓名功能会整块静默失效，没人发现。
    if want and len(miss) == len(want):
        print(f"[people] 全部未命中，疑似 id 体系不一致：{want[:3]}（共 {len(want)} 个）")
    elif miss:
        print(f"[people] 未命中 {len(miss)}/{len(want)}：{miss[:3]}")
    return jsonify(
        ok=True,
        names={k: book[k] for k in want if k in book},
        missing=miss,
        total=len(book),
    )


@app.get("/api/records")
def api_records():
    if not _records:
        load_records()
    items = [{
        "record_id": r["record_id"],
        "tn": r["tn"],
        "name": r.get("name") or "",
        "sub": r.get("sub") or "",
        "stage": r.get("stage") or "部门审核",
        "badge": r["badge"],
        "tone": r["tone"],
        "mode": r.get("mode"),
    } for r in _records]
    return jsonify(ok=True, total=len(items), items=items)


@app.get("/api/record/<rid>")
def api_record(rid):
    if not _records:
        load_records()
    rec = _by_rid.get(rid)
    if not rec:
        return jsonify(ok=False, error="record not found", record_id=rid), 404
    run_id = ensure_run(rec)
    act = latest_action(rid)
    with db() as conn:
        res = conn.execute("SELECT * FROM qc_result WHERE record_id=? ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    detail = dict(rec)
    detail.update({
        "run_id": run_id,
        "material_hash": material_hash(rec),
        "rule_ver": RULE_VER,
        "worker_ver": WORKER_VER,
        "conclusion": res["conclusion"] if res else rec.get("ai"),
        "img_url": "/img/" + os.path.basename(rec["img"]) if rec.get("img") else None,
        "state": (act["action"] if act else "待确认"),
        "decided_by": act["actor"] if act else None,
        "decided_by_id": act["actor_id"] if act else None,
        "decided_at": act["created_at"] if act else None,
        "decided_note": act["note"] if act else None,
        "history": [dict(h) for h in action_history(rid)],
    })
    return jsonify(ok=True, record=detail)


@app.get("/api/state/<rid>")
def api_state(rid):
    act = latest_action(rid)
    return jsonify(ok=True, record_id=rid,
                   state=act["action"] if act else "待确认",
                   actor=act["actor"] if act else None,
                   actor_id=act["actor_id"] if act else None,
                   note=act["note"] if act else None,
                   at=act["created_at"] if act else None)


@app.post("/api/decide")
def api_decide():
    """人工动作四种，语义在这里定死：

      通过（确认）  同意放行；可带附言。AI 原判不合格时即人工改判，需写理由。
      不合格（打回）退回整改；必须有问题说明，建议带规则号。
      附言（备注）  只补充意见，不改结论；可随时追加，不打断已有决策。
      撤销          结论回退到 AI 原判。

    存法：qc_result.conclusion 存业务值（通过 / 不合格），
          qc_action.action 存动作名，两者分开 —— 否则「通过」和「确认」
          会混成一个词，写飞书时没法区分是 AI 判的还是人判的。
    """
    body = request.get_json(force=True, silent=True) or {}
    rid = (body.get("record_id") or "").strip()

    # 前端说业务话，库里存动作名；旧调用（确认/打回）继续有效
    alias = {"通过": "确认", "合格": "确认",
             "不合格": "打回", "退回": "打回", "不通过": "打回",
             "备注": "附言", "意见": "附言"}
    raw_action = (body.get("action") or "").strip()
    action = alias.get(raw_action, raw_action)
    actor = (body.get("actor") or "未知用户").strip()
    # 登录人 open_id：由 panel 通过 bridge.getUserId() 取到后带上。
    # 这里是「可追溯」，不是「不可否认」——前端可伪造，服务端不验签（见 README 未做项）。
    actor_id = (body.get("actor_id") or "").strip()
    note = (body.get("note") or "").strip()

    codes = body.get("codes") or []
    if isinstance(codes, str):
        codes = re.split(r"[,，、\s]+", codes)
    codes = [str(c).strip().upper() for c in codes if str(c).strip()]

    if not rid or action not in ("确认", "打回", "撤销", "附言"):
        return jsonify(ok=False, error="record_id / action 不合法"), 400
    if action == "打回" and not note:
        return jsonify(ok=False, error="判不合格必须写问题说明：哪一条对不上、差多少"), 400
    if not _records:
        load_records()
    rec = _by_rid.get(rid)
    if not rec:
        return jsonify(ok=False, error="record_id 不存在于本批样本"), 404
    run_id = ensure_run(rec)

    # 有效决策唯一：已有「确认/打回」再下决策，必须显式带 override；
    # 附言不算决策，任何时候都能追加。
    prev = latest_action(rid)
    if (prev and prev["action"] in ("确认", "打回")
            and action in ("确认", "打回") and not body.get("override")):
        return jsonify(ok=False, error="该记录已有决策，如需改判请先撤销或带 override"), 409

    stored = note
    if codes:
        stored = "[" + ",".join(codes) + "] " + note

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO qc_action(record_id, run_id, action, actor, actor_id, note, payload, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, run_id, action, actor, actor_id or None, stored,
             json.dumps(body, ensure_ascii=False), now()),
        )
        action_id = cur.lastrowid
        if action == "撤销":
            # 撤销：结论回退到 AI 原判
            conn.execute("UPDATE qc_result SET conclusion=?, created_at=? WHERE record_id=?",
                         (rec.get("ai") or "通过", now(), rid))
        elif action != "附言":
            # 附言不动结论
            conn.execute("UPDATE qc_result SET conclusion=?, created_at=? WHERE record_id=?",
                         ({"确认": "通过", "打回": "不合格"}[action], now(), rid))

    # 写飞书字段的成品句，前端可直接预览/复制
    ai_val = str(rec.get("ai") or "通过")
    fb_text = ""
    if action == "确认":
        if ai_val != "通过":
            fb_text = f"通过（人工改判，AI 原判 {ai_val}）：" + (stored or "图面与清单核对一致")
        else:
            fb_text = "通过：" + (stored or "经人工复核，与 AI 预审一致")
    elif action == "打回":
        fb_text = ("不合格（" + ",".join(codes) + "）：" if codes else "不合格：") + note
    elif action == "附言":
        fb_text = stored

    return jsonify(ok=True, record_id=rid, state=action, action_id=action_id,
                   conclusion={"确认": "通过", "打回": "不合格"}.get(action),
                   fb_text=fb_text, run_id=run_id, at=now(),
                   actor=actor, actor_id=actor_id)


@app.get("/api/writeback")
def api_writeback_cfg():
    """告诉前端「写不写、写哪个字段、动作怎么映射成选项文本」。

    真正发请求的是浏览器，用当前登录人的身份调 SDK setCellValue。
    服务端不持 token、不调飞书 —— 这样飞书那边「修改人」记的才是点按钮的人。
    """
    return jsonify(
        ok=True,
        enabled=WB_ENABLED,
        conclusion_field=WB_CONC_FIELD,
        note_field=WB_NOTE_FIELD,
        value_map=WB_VALUE_MAP,
        actor_field=WB_ACTOR_FIELD,
    )


@app.post("/api/decide/receipt")
def api_decide_receipt():
    """前端写完飞书后回报结果。

    分成功和失败两种都记：写失败是「飞书那边没变」，必须留痕，
    否则库里显示已通过、表里还是空，事后无从追。
    """
    body = request.get_json(force=True, silent=True) or {}
    rid = (body.get("record_id") or "").strip()
    rows = body.get("results")
    # 单字段回报时 field_id 在顶层；多字段时在 results 里各带各的
    if isinstance(rows, list) and rows:
        field_id = ""
    else:
        field_id = (body.get("field_id") or "").strip()
    if not rid or (not field_id and not (isinstance(rows, list) and rows)):
        return jsonify(ok=False, error="record_id / field_id 必填"), 400
    items = rows if isinstance(rows, list) and rows else [body]
    with db() as conn:
        for it in items:
            conn.execute(
                "INSERT INTO qc_writeback(record_id, action_id, field_id, value, ok, error,"
                " by_id, by_name, written_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, body.get("action_id"), (it.get("field_id") or field_id),
                 json.dumps(it.get("value"), ensure_ascii=False),
                 1 if it.get("ok") else 0, (it.get("error") or None),
                 body.get("actor_id") or None, body.get("actor") or None, now()),
            )
    return jsonify(ok=True, recorded=len(items))


@app.post("/api/diag")
def api_diag():
    body = request.get_json(force=True, silent=True) or {}
    with db() as conn:
        conn.execute(
            "INSERT INTO qc_diag(source, payload, ip, ua, created_at) VALUES (?,?,?,?,?)",
            (body.get("source") or "probe", json.dumps(body, ensure_ascii=False),
             request.headers.get("X-Forwarded-For", request.remote_addr),
             (request.headers.get("User-Agent") or "")[:300], now()),
        )
    return jsonify(ok=True)


@app.get("/api/stats")
def api_stats():
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM qc_run").fetchone()["c"]
        acts = conn.execute("SELECT action, COUNT(*) c FROM qc_action GROUP BY action").fetchall()
        diag = conn.execute("SELECT COUNT(*) c FROM qc_diag").fetchone()["c"]
    return jsonify(ok=True, runs=total, actions={r["action"]: r["c"] for r in acts}, diag=diag,
                   samples=len(_records), rule_ver=RULE_VER)


if __name__ == "__main__":
    load_records()
    init_db()
    port = int(os.environ.get("PORT") or os.environ.get("QC_PORT") or "8776")
    host = os.environ.get("QC_HOST") or "0.0.0.0"
    print(f"\n  本地面板      http://127.0.0.1:{port}/panel")
    print(f"  假飞书宿主    http://127.0.0.1:{port}/          <- 先用这个验证链路")
    print(f"  探针          http://127.0.0.1:{port}/probe")
    print(f"  健康检查      http://127.0.0.1:{port}/health")
    print(f"  样本 {len(_records)} 条 · 判据版本 {RULE_VER}")
    print(f"  鉴权 {'已开启' if QC_TOKEN else '未开启（调试模式）'}\n")
    app.run(host=host, port=port, debug=False, threaded=True)
