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
页面：
  GET  /                     入口：登录后选模块（审核 / 工程量统计）
  GET  /login                登录页（可带 next）
  GET  /host                 假飞书宿主（只在本地验证用）
  GET  /panel                插件面板页（飞书里填这个，带 ?k=票据）
  GET  /qty                  工程量统计页（需 qty 权限）
  GET  /shrink               文件压缩页（浏览器内完成，需 shrink 权限）
  GET  /watermark            成果水印：探通后直接 302 到远端原程序（需 watermark 权限）
  GET  /admin                账号与授权管理（仅 admin）
  GET  /probe                上下文探针
接口：
  GET  /api/records          样本列表
  GET  /api/record/<rid>     单条样本 + 判据 + 留痕
  GET  /api/state/<rid>      当前人工决策状态
  POST /api/decide           人工通过 / 打回（只追加，可撤销）
  POST /api/diag             探针上报（落 qc_diag，便于远程排查）
  GET  /api/stats            统计
  GET  /health               健康检查（穿透后先打这个）
  POST /api/login            登录（表单或 JSON）—— 只留给管理员应急，内业不用
  POST /api/ident            按飞书 open_id 换票据 / 记待开通（面板自动认人靠它）
  GET  /api/me               当前登录人与可用模块
  POST /api/qty/run          上传 dwg/dxf + 清单，跑一次工程量统计
  GET  /api/qty/job/<id>     取回一次统计结果
  GET  /api/qty/job/<id>/xlsx 导出 xlsx
  GET  /api/qty/jobs         历史任务（本人；管理员看全部）
  GET  /api/watermark/conf   水印远端地址 + 可达性探测（服务端探，绕代理）
  GET  /api/admin/users      账号与授权列表
  POST /api/admin/user       新增 / 修改 / 停用账号与授权
"""
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import time
import urllib.request
from collections import deque
from datetime import datetime

from flask import Flask, g, jsonify, redirect, request, send_from_directory, send_file

import perm
import qty

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")

HERE = os.path.dirname(os.path.abspath(__file__))
# QC_DATA 指向临时目录时，库、缩略图、上传件全部跟着走（自测用，别让测试写生产库）
DATA = os.environ.get("QC_DATA") or os.path.join(HERE, "data")
# 图片目录可外部指定；默认落在项目内 img/，便于整包搬到服务器
IMG_DIR = os.environ.get("QC_IMG_DIR") or os.path.join(HERE, "img")
DB = os.environ.get("QC_DB") or os.path.join(DATA, "qc.db")
THUMB = os.path.join(DATA, "thumb")
RECORDS_JSON = os.path.join(DATA, "records.json")
QTY_DIR = os.path.join(DATA, "qty")          # 每次统计的原始上传与导出一份，便于复算

# 成果水印是「另一台机器上的独立服务」，本服务只做入口与可达性探测，不代理文件。
# 地址只在这里出现一处：换机器 / 换端口只改环境变量，不用动模板。
WM_URL = (os.environ.get("QC_WM_URL") or "http://192.168.0.101:8765").rstrip("/") + "/"
WM_TIMEOUT = float(os.environ.get("QC_WM_TIMEOUT") or 4)

RULE_VER = "2026-11-05.1"      # 判据版本：口径一变就要涨，否则留痕对不上
WORKER_VER = "stream_run/1.4"

# 访问口令：设置后所有数据接口与页面都必须带它（?k= 或 X-QC-Token 头）
# 不设置 = 不做鉴权，只适合本机调试。生产环境务必设置。
QC_TOKEN = (os.environ.get("QC_TOKEN") or "").strip()

# 灰度/预览副本标记：QC_PREVIEW=1 时页面顶部挂一条醒目提示。
# 为什么要有：副本和正式站长得一模一样，人在副本里点了「同意」，
# 会以为正式账本记上了 —— 实际写的是副本库。只靠口头说明一定会混淆。
PREVIEW = (os.environ.get("QC_PREVIEW") or "").strip().lower() in ("1", "true", "yes")

# ── 写回飞书 ──────────────────────────────────────────────────────────────
# 写回由**浏览器**用登录人身份完成（SDK setCellValue），服务端不下场、不持 token。
# 这样飞书侧的「修改人」就是点按钮的人，而不是应用的机器人身份。
#   默认关闭：字段归属还没最终拍板，先保证「库里有记录、飞书不被误改」。
#   开启：QC_WB_ENABLED=1
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
    """一道门，两条通道，三个项目层。

    通道一：`?k=` / `X-QC-Token`。里面的值有两种可能 ——
      · 主口令（QC_TOKEN）：管理通道，全通，用于运维与飞书首次配置；
      · 票据（perm.new_ticket 签的）：按人签发，**带项目号**，只通他那一个项目。
      面板在 iframe 里跑，第三方 cookie 会被拦，所以只能走这条。
    通道二：`qc_sess` cookie，顶层浏览器用（入口页、工程量页、管理页）。

    项目层：`audit` 审核 / `qty` 工程量 / `admin` 管理。票据的 proj 对不上就是 401/403，
    不是「登录了就能看全部」。
    """
    if request.method == "OPTIONS":
        return None
    p = request.path
    if (p.startswith("/health") or p.startswith("/vendor/") or p.startswith("/favicon")
            or p in ("/login", "/logout") or p.startswith("/static/")
            or p.startswith("/api/login") or p == "/api/ident"):
        return None

    need = _need_of(p)
    uid = ""

    tok = (request.headers.get("X-QC-Token") or request.args.get("k") or "").strip()
    if tok:
        if QC_TOKEN and hmac.compare_digest(tok, QC_TOKEN):
            g.uid, g.proj, g.via = "", need, "master"
            return None                                   # 主口令：全通（管理通道）
        t = perm.read_ticket(tok)
        if t:
            # 「管理」不看票据里写的项目号，只看持票人此刻是不是管理员 ——
            # 票据是可转发的链接凭据，光凭 p=admin 就放行，等于谁转发谁当管理员。
            ok = _is_admin(t["uid"]) if need == "admin" else (need in ("any", t["p"]))
            if ok:
                g.uid, g.proj, g.via = t["uid"], t["p"], "ticket"
                # 顶层浏览器里把票据顺手换成 cookie：管理员发的入口链接点一次就够，
                # 之后直接开首页，不用天天拿链接。iframe 里 Set-Cookie 会被丢掉，无害。
                if request.method == "GET" and not p.startswith("/api/"):
                    cur = request.cookies.get(perm.COOKIE, "")
                    if perm.read_session(cur) != t["uid"]:
                        g.set_sess = perm.new_session(t["uid"])
                return None
            return _deny(p, "这个链接只开通了「%s」，不能进「%s」"
                         % (perm.PROJS.get(t["p"], t["p"]), perm.PROJS.get(need, need)), 403)
        # 票据过期/被改动：面板重新走一次身份识别，别拿着一个废 k 一路 401
        # （飞书里的人重开面板会自动换到新票据；外面的人还是进不来）
        if p == "/panel":
            return redirect("/panel")

    sess = request.cookies.get(perm.COOKIE, "")
    if sess:
        uid = perm.read_session(sess) or ""
        u = perm.get_user(uid=uid) if uid else None
        if u and u.get("active"):
            if need == "any" or (need == "admin" and _is_admin(uid)) or perm.can(uid, need):
                g.uid, g.proj, g.via = uid, need, "cookie"
                return None
            return _deny(p, "你没有「%s」的权限，找管理员开通" % perm.PROJS.get(need, need), 403)
        if u:
            # 停用要立刻生效：光靠 perm.can 拦不住 need=="any" 的路径，这里直接否掉
            return _deny(p, "账号已停用，找管理员", 403)

    # 一个人都没建（全新部署）：保留旧行为，只认主口令，别把人锁在门外
    if not perm.list_users() and (QC_TOKEN or not need):
        g.uid, g.proj, g.via = "", need, "open"
        return None

    # 面板页本身只是外壳、不含任何业务数据：没凭据也放行，让它自己去向飞书宿主要 open_id
    # 换票据（见 /api/ident）。数据接口仍然在上面的分支里被拦，所以放行外壳不泄露东西。
    if p == "/panel":
        g.uid, g.proj, g.via = "", "audit", "boot"
        return None

    if p.startswith("/api/"):
        return jsonify(ok=False, error="未登录或登录已过期", login="/login"), 401
    # 嵌在飞书里又没凭据：送去登录页是死路 —— 飞书里没人有账号口令，而且第三方 cookie
    # 被拦，就算登录了也记不住。回面板，那里能向飞书宿主要 open_id 换票据。
    # 判据用 Sec-Fetch-Dest（浏览器自己填，页面改不了），顶层打开仍然走登录页当应急通道。
    if (request.headers.get("Sec-Fetch-Dest") or "").lower() == "iframe":
        return redirect("/panel")
    nxt = request.full_path.rstrip("?") if request.query_string else p
    return redirect("/login?next=" + nxt)


def _deny(p, msg, code):
    if p.startswith("/api/"):
        return jsonify(ok=False, error=msg), code
    return (msg + "　<a href='/'>← 返回首页</a>", code,
            {"Content-Type": "text/html; charset=utf-8"})


def _is_admin(uid):
    u = perm.get_user(uid=uid) if uid else None
    return bool(u and u.get("role") == "admin" and u.get("active"))


def _active_admin_count():
    """还有几个可用的管理员。用于挡住「把最后一个管理员降权/停用/删除」。"""
    n = 0
    for u in perm.list_users():
        if u.get("role") == "admin" and u.get("active"):
            n += 1
    return n


def _is_https():
    """当前请求是不是 HTTPS。

    不能只看 request.is_secure：服务挂在 ngrok / 反向代理后面，到这里永远是
    明文 HTTP，is_secure 恒为 False。协议真相在 X-Forwarded-Proto 里。
    这个判断决定「能不能给内网 http 服务嵌 iframe」——判错就是一片白框。
    """
    xf = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    return bool(request.is_secure or xf == "https")


def _need_of(path):
    """路径 → 需要哪个项目。顺序敏感：/api/qty 必须先于 /qty 判。"""
    if path.startswith("/watermark") or path.startswith("/api/watermark"):
        return "watermark"
    if path.startswith("/qty") or path.startswith("/api/qty"):
        return "qty"
    if path.startswith("/shrink") or path.startswith("/api/shrink"):
        return "shrink"
    if path.startswith("/admin") or path.startswith("/api/admin"):
        return "admin"
    if path.startswith(("/panel", "/host", "/probe", "/img/", "/api/records", "/api/record",
                        "/api/state", "/api/decide", "/api/people", "/api/diag", "/api/stats",
                        "/review", "/api/review")):
        return "audit"
    return "any"


@app.after_request
def _gzip(resp):
    tok = g.get("set_sess")
    if tok:
        resp.headers["Set-Cookie"] = perm.cookie_header(tok, secure=request.is_secure)
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
    if not os.path.isfile(RECORDS_JSON):
        _records, _by_rid = [], {}
        return
    with open(RECORDS_JSON, encoding="utf-8") as f:
        _records = json.load(f)
    _by_rid = {r["record_id"]: r for r in _records}


# ------------------------------------------------------------ 人员姓名
# 为什么不用通讯录接口：应用（bot）的通讯录数据权限只覆盖「办公室」2 人，
# 查工程测绘部 / 房产测绘部的人一律 41050 no user authority error。
# 但任务表的人员字段值是 {id, name} 成对返回的，读记录就有姓名，不需要通讯录权限。
# 映射表由 build_people.py 生成；miss 的 id 不编造，前端显示缩写。
PEOPLE_JSON = os.path.join(DATA, "people.json")
_people = None


def load_people():
    global _people
    if _people is not None:
        return _people
    _people = {}
    if os.path.isfile(PEOPLE_JSON):
        try:
            with open(PEOPLE_JSON, encoding="utf-8") as f:
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

-- 工程量统计：一次上传算一次，结果整包存 result（JSON），重算能力靠原始文件
CREATE TABLE IF NOT EXISTS qty_job (
  id         TEXT PRIMARY KEY,
  uid        TEXT NOT NULL,
  name       TEXT,                    -- 清单项目名，没清单就用文件名
  created_at TEXT NOT NULL,
  files      TEXT,                    -- 原始文件名，逗号分隔
  list_file  TEXT,
  result     TEXT,                    -- qty.build() 的 JSON
  note       TEXT
);
CREATE INDEX IF NOT EXISTS ix_qty_uid ON qty_job(uid, created_at);
"""


# ------------------------------------------------- 核对台：判断事实表（只追加）
# 出处《质检第一层-网站改动与采集库设计-v1.0》§4，相对原 DDL 有三处调整，
# 原因都写在列注释里，不是随手加的：
#   1. 加 stance / dispute_reason —— 方案 v2.1 §8.2 要求「有意见」只三选一、
#      不打字，原 DDL 却要求 fail 必写 ≥2 字，两条直接冲突。拆成
#      「人的态度」（stance）和「人的结论」（verdict）两列才同时成立。
#   2. 加 src / src_key —— 页面同时接两类材料：飞书记录（records.json）
#      和比对明细（projects.db）。两者没有共同键（已实测：报告编号 vs
#      项目名，零重叠），只能各带各的来源主键。
#   3. 去掉 fail 强写 note 的 CHECK —— 有意见时人只选原因不写字，这是刻意的。
#      改判要写理由那条约束留在「写回飞书」那个动作上，不在采集口。
REVIEW_DDL = """
-- 规则表：判断的锚点。先建空表，规则码陆续灌，不阻塞采集。
CREATE TABLE IF NOT EXISTS qc_rule (
  code        TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  scope       TEXT NOT NULL,
  rule_text   TEXT NOT NULL,
  tolerance   REAL,
  unit        TEXT,
  ver         TEXT NOT NULL,
  active      INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

-- 判断事实表：一行 = 一个人对一份材料的一个检查项的一次表态。
CREATE TABLE IF NOT EXISTS qc_judgement (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  uid            TEXT NOT NULL,            -- qc_user.id，不存姓名（改名不断链）
  oid            TEXT,                     -- 飞书 open_id，可追溯
  actor          TEXT,                     -- 当时的显示名快照，只作展示
  src            TEXT NOT NULL DEFAULT 'records',  -- records / compare
  record_id      TEXT NOT NULL DEFAULT '',          -- 飞书记录 id（src=records）
  src_key        TEXT NOT NULL DEFAULT '',          -- 来源主键（src=compare 用）
  run_id         TEXT,
  check_item     TEXT NOT NULL DEFAULT '',  -- 最小单元；'' = 只到整条记录
  check_cat      TEXT,                      -- 大类：redline_corner / 数据完整性 …
  building       TEXT,
  rule_code      TEXT,
  rule_ver       TEXT,
  verdict        TEXT NOT NULL CHECK (verdict IN ('pass','fail','hold')),
  stance         TEXT NOT NULL CHECK (stance IN ('agree','dispute','hold')),
  dispute_reason TEXT,                      -- 仅 stance='dispute'
  hold_reason    TEXT,                      -- 仅 stance='hold'
  note           TEXT,                      -- 人的原话，可空
  corrected_json TEXT,                      -- 人改的数值（本期 UI 不做，列先留着）
  ai_verdict     TEXT,                      -- 当时的 AI 结论（快照）
  material_hash  TEXT NOT NULL,             -- 材料指纹快照
  evidence_json  TEXT NOT NULL DEFAULT '{}',-- 他看到的数值/理由（快照，内联）
  elapsed_ms     INTEGER,
  device         TEXT,
  supersedes_id  INTEGER REFERENCES qc_judgement(id),
  created_at     TEXT NOT NULL,
  CHECK (stance <> 'dispute' OR dispute_reason IS NOT NULL
         OR (note IS NOT NULL AND length(trim(note)) >= 2)),
  CHECK (dispute_reason IS NULL OR dispute_reason IN ('图不对','机器看错','规矩不对')),
  CHECK (stance <> 'hold' OR hold_reason IS NOT NULL),
  CHECK (hold_reason IS NULL OR hold_reason IN ('材料不清','缺规划依据','无对应规则','需现场核实')),
  CHECK (verdict <> 'hold' OR stance = 'hold')
);
CREATE INDEX IF NOT EXISTS ix_j_key ON qc_judgement(src, src_key, check_item, created_at);
CREATE INDEX IF NOT EXISTS ix_j_rec ON qc_judgement(record_id, check_item, created_at);
CREATE INDEX IF NOT EXISTS ix_j_uid ON qc_judgement(uid, created_at);

-- 盲复判：第二个人独立判。表先建，接口本期不启用。
CREATE TABLE IF NOT EXISTS qc_recheck (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  judgement_id INTEGER NOT NULL REFERENCES qc_judgement(id),
  uid          TEXT NOT NULL,
  oid          TEXT,
  verdict      TEXT NOT NULL CHECK (verdict IN ('pass','fail','hold')),
  hold_reason  TEXT,
  note         TEXT,
  elapsed_ms   INTEGER,
  created_at   TEXT NOT NULL
);

-- 一致率：按「来源 + 大类」算，不按整库算（图纸类和报告类判据不同，不能混）
-- 注意「当前生效」的判据：新行用 supersedes_id 指向被它取代的旧行，
-- 所以生效行 = **没有别人指向它**的那一行，不是 supersedes_id IS NULL 的那一行。
-- （写成 IS NULL 会永远只认第一条，改主意之后反而读回旧结论。）
DROP VIEW IF EXISTS v_agreement;
CREATE VIEW v_agreement AS
SELECT src, COALESCE(check_cat, '(未分类)') AS cat,
       COUNT(*) AS n,
       SUM(CASE WHEN stance = 'agree' THEN 1 ELSE 0 END) AS same,
       SUM(CASE WHEN stance = 'dispute' THEN 1 ELSE 0 END) AS dispute,
       SUM(CASE WHEN stance = 'hold' THEN 1 ELSE 0 END) AS holds
FROM qc_judgement j
WHERE NOT EXISTS (SELECT 1 FROM qc_judgement x WHERE x.supersedes_id = j.id)
GROUP BY src, COALESCE(check_cat, '(未分类)');

-- 分歧池：唯一值得进人工讨论的样本
DROP VIEW IF EXISTS v_dispute;
CREATE VIEW v_dispute AS
SELECT * FROM qc_judgement j
WHERE stance IN ('dispute', 'hold')
  AND NOT EXISTS (SELECT 1 FROM qc_judgement x WHERE x.supersedes_id = j.id);
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
        _migrate_review(conn)
        conn.executescript(REVIEW_DDL)
        _migrate_actor_id(conn)
    perm.init()


def _migrate_review(conn):
    """qc_judgement 的约束改过一次：老版的 CHECK 要求「有意见必须三选一」，
    但飞书面板那条路径只有一句手写理由、没有三选一，会被这条约束挡死。
    表还是空的话直接重建（没有任何数据可丢）；已经有数据就不动，只提示。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='qc_judgement'").fetchone()
    if not row or not row["sql"]:
        return
    if "dispute_reason IS NOT NULL\n         OR" in row["sql"] or "OR (note IS NOT NULL" in row["sql"]:
        return
    n = conn.execute("SELECT COUNT(*) c FROM qc_judgement").fetchone()["c"]
    if n:
        print(f"[db] qc_judgement 有 {n} 行，约束没升级；飞书面板那条路会写不进来")
        return
    conn.execute("DROP TABLE qc_judgement")
    print("[db] qc_judgement 为空，按新约束重建")


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
TPL = os.path.join(HERE, "templates")


def _tpl(name):
    return send_from_directory(TPL, name)


@app.get("/")
def entry():
    """入口：登录后按权限出卡片。没登录的人被 _auth 送去 /login。"""
    return _tpl("entry.html")


@app.get("/login")
def login_page():
    return _tpl("login.html")


@app.get("/host")
def host():
    return _tpl("host.html")


@app.get("/qty")
def qty_page():
    return _tpl("qty.html")


@app.get("/shrink")
def shrink_page():
    """文件压缩。压缩在**浏览器**里做，服务端只发页面和 JS 库 —— 文件不上传、不落盘。"""
    return _tpl("shrink.html")


@app.get("/watermark")
def watermark_page():
    """成果水印：点进来就是原程序，不做中间层。

    为什么可以直接跳：混合内容策略管的是**子资源**（iframe / script / img），
    https 页里嵌 http 的框架会被拦；但**顶层文档跳转不受这条约束** —— 浏览器只会
    给目标页标一个「不安全」，不会拒绝导航。所以 https 页面上点水印，302 过去即可。

    代价：跳走就离开了本服务的页面（没有「返回首页」）。所以首页那张卡片用新标签
    打开，首页本身留着。带 ?stay=1 才停在本服务的说明页，用于排障。
    """
    if request.args.get("stay"):
        return _tpl("watermark.html")
    ok, _ver, _ms, _err = _wm_probe()
    if ok:
        return redirect(WM_URL, code=302)
    return _tpl("watermark.html")        # 连不上：停下来把原因说清，别给白屏


def _wm_probe():
    """探远端水印服务是否活着，返回 (reachable, version, ms, err)。

    为什么在服务端探、不在前端探：
      浏览器给 iframe 一个宕机/不存在的内网地址，onload 照样触发（拿到的是
      浏览器自己的错误页），JS 判不出来，人看到的就是一片白。服务端拿到的是
      真实 HTTP 结果，能明说「那边没起来」。
    为什么必须绕代理：
      本机环境常年带 HTTP_PROXY=127.0.0.1:7011，urllib 默认会把发往内网
      192.168.* 的请求也丢给代理，直连必然失败。这里显式禁用代理。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    try:
        with opener.open(WM_URL + "openapi.json", timeout=WM_TIMEOUT) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
        ver = str((info.get("info") or {}).get("version") or "")
        return True, ver, int((time.time() - t0) * 1000), ""
    except Exception as exc:                      # 连不上 / 超时 / 返回不是 JSON
        return False, "", int((time.time() - t0) * 1000), type(exc).__name__


@app.get("/api/watermark/conf")
def api_wm_conf():
    """给前端：远端地址、可达性、版本、探测耗时、当前页面是不是 HTTPS。

    https 一定要回给前端：https 页里嵌 http:// 的 iframe 会被浏览器当
    混合内容直接拦掉（白框、控制台一条 Mixed Content）。这种情况不能再
    硬嵌，得改成「新窗口打开」——顶层跳转不算混合内容，允许。
    """
    ok, ver, ms, err = _wm_probe()
    return jsonify(ok=True, url=WM_URL, reachable=ok, version=ver, ms=ms,
                   err=err, https=_is_https(), timeout=WM_TIMEOUT)


@app.get("/admin")
def admin_page():
    return _tpl("admin.html")


@app.get("/panel")
def panel():
    return send_from_directory(os.path.join(HERE, "templates"), "panel.html")


@app.get("/review")
def review_page():
    """核对台：逐检查项表态。与 /panel 不同 —— panel 是飞书里一条记录卡，
    这里是宽屏工作台，同一个页面在窄屏（飞书侧栏）会自动收成单列。"""
    return _tpl("review.html")


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


# ---------------------------------------------------------------- 登录 / 身份
def _page_of(code):
    """项目号 → 页面路径。

    审核现在进「核对台」：一屏看一份材料的全部检查项，能逐项表态。
    飞书插件那个入口仍然指向 /panel —— 那是「一条记录一张卡」的形态，
    两者并存，不是二选一。
    """
    return "/review" if code == "audit" else "/" + code


def _links(uid):
    """按权限生成模块链接。

    每一条都自带票据，不只是审核面板 —— 因为链接要在飞书 iframe 里点得动。
    iframe 里第三方 cookie 被拦，链接里不带凭据的话，「工程量」「账号」点进去
    必然是 302 到登录页，人就被困在框架里（实测过一次）。
    票据仍然一条只开通一个项目（p=audit / qty / admin），越权由 _auth 按路径判。
    """
    out = []
    if not uid:                                    # 主口令：全通，链接里带主口令
        for code, name in perm.PROJS.items():
            href = _page_of(code) + "?k=" + QC_TOKEN
            out.append({"code": code, "name": name, "desc": perm.PROJ_DESC[code],
                        "href": href, "ok": True})
        out.append({"code": "admin", "name": "账号与授权", "desc": "谁能进哪个模块",
                    "href": "/admin?k=" + QC_TOKEN, "ok": True})
        return out
    for code, name in perm.PROJS.items():
        # 没权限就不出现在返回里，也不签票据。
        # 不这么写会漏两件事：① 卡片灰显等于把「有这个东西」告诉所有人；
        # ② 顺手签出来的 ?k= 是可用的真票据，等于给没权限的人发了门禁卡。
        if not perm.can(uid, code):
            continue
        href = _page_of(code) + "?k=" + perm.new_ticket(uid, code)
        out.append({"code": code, "name": name, "desc": perm.PROJ_DESC[code],
                    "href": href, "ok": True})
    if _is_admin(uid):
        out.append({"code": "admin", "name": "账号与授权", "desc": "谁能进哪个模块",
                    "href": "/admin?k=" + perm.new_ticket(uid, "admin"), "ok": True})
    return out


@app.post("/api/login")
def api_login():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or request.form.get("name") or "").strip()
    pw = body.get("password") or request.form.get("password") or ""
    nxt = (body.get("next") or request.args.get("next") or "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"                                     # 挡开放重定向
    as_json = bool(body)
    u = perm.auth_user(name, pw)
    if not u:
        if as_json:
            return jsonify(ok=False, error="账号或口令不对"), 401
        return redirect("/login?e=1&next=" + nxt)
    if not (u["role"] == "admin" or perm.perms_of(u["id"])):
        if as_json:
            return jsonify(ok=False, error="账号还没开通任何模块，找管理员开通"), 403
        return redirect("/login?e=2&next=" + nxt)
    tok = perm.new_session(u["id"])
    if as_json:
        resp = jsonify(ok=True, name=u["name"], role=u["role"], next=nxt)
        resp.headers["Set-Cookie"] = perm.cookie_header(tok, secure=request.is_secure)
        return resp
    resp = redirect(nxt)
    resp.headers["Set-Cookie"] = perm.cookie_header(tok, secure=request.is_secure)
    return resp


@app.post("/api/ident")
def api_ident():
    """按飞书身份换票据 —— 内业人员不用输账号口令，认人全靠这一步。

    面板在飞书里打开时，宿主把 `bridge.getUserId()`（open_id）给前端，前端 POST 过来：
      · 这个 id 绑了账号且开着模块 → 发会话 cookie + 返回一条 12 小时票据；
        前端带 `?k=<票据>` 重进自己，后面所有请求照旧。
      · 没绑过 → 记进 `qc_visit` 待开通名单，返回 403；管理员在 /admin 一键开通。
    没拿到 id（普通浏览器、假宿主）就返回 400，让前端说清楚「请在飞书里打开」，
    不要伪装成「没权限」——那是两种完全不同的故障。
    """
    b = request.get_json(silent=True) or {}
    oid = (b.get("oid") or request.form.get("oid") or "").strip()
    name = (b.get("name") or "").strip()
    if not oid or len(oid) < 8:
        return jsonify(ok=False, error="没拿到飞书用户 id"), 400
    u = perm.get_by_oid(oid)
    if not u:
        perm.record_visit(oid, name)
        return jsonify(ok=False, pending=True, oid=oid,
                       error="你的飞书身份还没开通，已把申请交给管理员"), 403
    if not u.get("active"):
        return jsonify(ok=False, oid=oid, error="账号已停用，找管理员"), 403
    if u["role"] != "admin" and not perm.perms_of(u["id"]):
        perm.record_visit(oid, name)
        return jsonify(ok=False, pending=True, oid=oid,
                       error="还没给你开通任何模块，找管理员"), 403
    with perm.db() as conn:
        conn.execute("UPDATE qc_user SET last_login=? WHERE id=?", (perm.now(), u["id"]))
    resp = jsonify(ok=True, name=u["name"], role=u["role"], oid=oid,
                   perms=sorted(perm.perms_of(u["id"])),
                   modules=_links(u["id"]),
                   ticket=perm.new_ticket(u["id"], "audit"))
    resp.headers["Set-Cookie"] = perm.cookie_header(perm.new_session(u["id"]),
                                                    secure=request.is_secure)
    return resp


@app.get("/logout")
def logout():
    if os.environ.get("QC_LOGOUT_JSON"):
        return jsonify(ok=True)
    resp = redirect("/login")
    resp.headers["Set-Cookie"] = perm.clear_cookie()
    return resp


@app.get("/api/me")
def api_me():
    uid = g.get("uid") or ""
    if not uid:
        return jsonify(ok=True, master=True, name="主口令", role="admin",
                       preview=PREVIEW, modules=_links(""))
    u = perm.get_user(uid=uid) or {}
    return jsonify(ok=True, master=False, uid=uid, name=u.get("name", ""),
                   role=u.get("role", ""), perms=sorted(perm.perms_of(uid)),
                   oid=u.get("oid") or "", has_pw=bool(u.get("pw")),
                   via=g.get("via") or "", preview=PREVIEW, modules=_links(uid))


@app.post("/api/passwd")
def api_passwd():
    """自己改口令。要求先验旧口令 —— 会话被人拿到也不能直接改掉。"""
    uid = g.get("uid") or ""
    if not uid:
        return jsonify(ok=False, error="主口令通道不用改口令"), 400
    b = request.get_json(silent=True) or {}
    old, new = b.get("old") or "", b.get("new") or ""
    u = perm.get_user(uid=uid) or {}
    if not perm.verify_pw(old, u.get("pw") or ""):
        return jsonify(ok=False, error="原口令不对"), 400
    try:
        perm.set_user(uid, pw=new)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=True, note="口令已改，下次登录用新口令")


# ---------------------------------------------------------------- 工程量统计
ALLOW_EXT = {".dxf", ".dwg", ".xls", ".xlsx"}


@app.post("/api/qty/run")
def api_qty_run():
    """收图 + 清单 → 统计。上传件落 data/qty/<job>/，结果整包存 qty_job.result。"""
    up = request.files.getlist("dwg") + request.files.getlist("files")
    lst = request.files.get("list")
    files = [f for f in up if f and f.filename]
    if not files and not (lst and lst.filename):
        return jsonify(ok=False, error="什么都没上传：至少要一份 dwg/dxf 或一份清单"), 400
    for f in files + ([lst] if lst else []):
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOW_EXT:
            return jsonify(ok=False, error=f"不支持的文件类型 {ext}（收 dwg/dxf/xls/xlsx）"), 400

    job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    work = os.path.join(QTY_DIR, job_id)
    indir = os.path.join(work, "in")
    os.makedirs(indir, exist_ok=True)
    saved, list_path = [], ""
    for f in files:
        base = os.path.basename(f.filename)
        dst = os.path.join(indir, base)
        f.save(dst)
        saved.append(dst)
    if lst and lst.filename:
        list_path = os.path.join(indir, os.path.basename(lst.filename))
        lst.save(list_path)

    drawings = [p for p in saved if os.path.splitext(p)[1].lower() in (".dxf", ".dwg")]
    lists = [p for p in saved if os.path.splitext(p)[1].lower() in (".xls", ".xlsx")]
    if not list_path and lists:
        list_path = lists[0]
    if not drawings:
        return jsonify(ok=False, error="这次只传了清单，没有图。工程量统计要至少一份 dwg/dxf"), 400
    try:
        tol = float(request.form.get("tol") or 0.5)
    except ValueError:
        tol = 0.5

    try:
        result = qty.build(drawings, list_path, work, tol_pct=tol)
    except Exception as exc:
        return jsonify(ok=False, error=str(exc)), 400

    name = (result["list"].get("project") or result["list"].get("title")
            or os.path.basename(drawings[0]))
    with db() as conn:
        conn.execute(
            "INSERT INTO qty_job(id,uid,name,created_at,files,list_file,result,note) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, g.get("uid") or "", name[:120], now(),
             ",".join(os.path.basename(p) for p in saved),
             os.path.basename(list_path) if list_path else "",
             json.dumps(result, ensure_ascii=False), ""),
        )
    return jsonify(ok=True, job_id=job_id, result=result)


def _job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM qty_job WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def _may_touch(row):
    if not row:
        return False
    uid = g.get("uid") or ""
    if not uid or g.get("via") == "master":
        return True
    return row["uid"] == uid or _is_admin(uid)


@app.get("/api/qty/job/<job_id>")
def api_qty_job(job_id):
    row = _job(job_id)
    if not _may_touch(row):
        return jsonify(ok=False, error="这条任务不是你的"), 403
    return jsonify(ok=True, job_id=job_id, name=row["name"], created_at=row["created_at"],
                   files=row["files"], list_file=row["list_file"],
                   result=json.loads(row["result"] or "{}"))


@app.get("/api/qty/job/<job_id>/xlsx")
def api_qty_xlsx(job_id):
    row = _job(job_id)
    if not _may_touch(row):
        return jsonify(ok=False, error="这条任务不是你的"), 403
    out = os.path.join(QTY_DIR, job_id, "工程量统计.xlsx")
    qty.export_xlsx(json.loads(row["result"] or "{}"), out)
    fn = re.sub(r"[\\/:*?\"<>|]", "_", row["name"] or job_id)[:40] + "-工程量统计.xlsx"
    return send_file(out, as_attachment=True, download_name=fn,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/qty/jobs")
def api_qty_jobs():
    uid = g.get("uid") or ""
    with db() as conn:
        if not uid or _is_admin(uid):
            rows = conn.execute("SELECT id,uid,name,created_at,files FROM qty_job ORDER BY id DESC LIMIT 100").fetchall()
        else:
            rows = conn.execute("SELECT id,uid,name,created_at,files FROM qty_job WHERE uid=? ORDER BY id DESC LIMIT 100", (uid,)).fetchall()
    return jsonify(ok=True, items=[dict(r) for r in rows])


@app.post("/api/qty/job/<job_id>/delete")
def api_qty_delete(job_id):
    row = _job(job_id)
    if not _may_touch(row):
        return jsonify(ok=False, error="这条任务不是你的"), 403
    with db() as conn:
        conn.execute("DELETE FROM qty_job WHERE id=?", (job_id,))
    shutil.rmtree(os.path.join(QTY_DIR, job_id), ignore_errors=True)
    return jsonify(ok=True)


# ---------------------------------------------------------------- 管理页
@app.get("/api/admin/users")
def api_admin_users():
    return jsonify(ok=True, projs=[{"code": c, "name": n} for c, n in perm.PROJS.items()],
                   items=perm.list_users())


@app.get("/api/admin/pending")
def api_admin_pending():
    """待开通：来过但没有任何账号绑着他的飞书 id。"""
    return jsonify(ok=True, items=perm.list_visits())


@app.post("/api/admin/user")
def api_admin_user():
    """加人 / 改授权 / 改口令 / 停用 / 审批 / 绑解飞书 id / 取入口链接。

    action 必须显式给，不做「猜意图」。
    """
    b = request.get_json(silent=True) or {}
    act = (b.get("action") or "").strip()
    try:
        if act == "add":
            uid = perm.add_user(b.get("name") or "", b.get("pw") or "",
                                role=b.get("role") or "user", perms=b.get("perms") or [],
                                oid=b.get("oid") or None)
            return jsonify(ok=True, uid=uid)
        if act == "update":
            uid = (b.get("uid") or "").strip()
            if not uid:
                return jsonify(ok=False, error="缺 uid"), 400
            # 自锁防护：改角色 / 停用会把「最后一个能管人的人」弄没，之后谁也进不来 /admin，
            # 只能去改 SQLite 才能救。三种情况直接拒：
            new_role = b.get("role")
            new_active = b.get("active")
            # active 前端可能传 0 / false / "0"，用 is False 判断会漏掉整数 0（实测漏过）
            will_disable = new_active is not None and not bool(new_active)
            tgt = perm.get_user(uid=uid)
            if not tgt:
                return jsonify(ok=False, error="账号不存在"), 404
            lost_admin = (tgt.get("role") == "admin" and tgt.get("active")
                          and ((new_role in ("admin", "user") and new_role != "admin")
                               or will_disable))
            if lost_admin:
                if uid == (g.get("uid") or ""):
                    return jsonify(ok=False, error="不能把自己降权或停用——会把自己锁在门外"), 400
                if _active_admin_count() <= 1:
                    return jsonify(ok=False, error="这是最后一个管理员，先给别的人管理员权限再改"), 400
            perm.set_user(uid, pw=(b.get("pw") or None), role=new_role,
                          active=new_active, perms=b.get("perms"))
            return jsonify(ok=True)
        if act == "delete":
            uid = (b.get("uid") or "").strip()
            if not uid:
                return jsonify(ok=False, error="缺 uid"), 400
            if uid == (g.get("uid") or ""):
                return jsonify(ok=False, error="不能删自己"), 400
            tgt = perm.get_user(uid=uid)
            if tgt and tgt.get("role") == "admin" and tgt.get("active") and _active_admin_count() <= 1:
                return jsonify(ok=False, error="这是最后一个管理员，不能删"), 400
            with perm.db() as conn:
                conn.execute("DELETE FROM qc_user WHERE id=?", (uid,))
                conn.execute("DELETE FROM qc_perm WHERE uid=?", (uid,))
            return jsonify(ok=True)
        if act == "approve":
            # 待开通名单上的一个人 → 建号（或把 id 绑到已有的同名账号上）
            oid = (b.get("oid") or "").strip()
            if not oid:
                return jsonify(ok=False, error="缺 oid"), 400
            name = (b.get("name") or "").strip()
            perms = b.get("perms") or []
            exist = perm.get_user(name) if name else None
            if exist:
                perm.bind_oid(exist["id"], oid)
                if perms:
                    perm.set_user(exist["id"], perms=perms)
                uid, created = exist["id"], False
            else:
                if not name:
                    return jsonify(ok=False, error="缺姓名"), 400
                uid = perm.add_user(name, "", role=b.get("role") or "user",
                                    perms=perms, oid=oid)
                created = True
            perm.drop_visit(oid)
            return jsonify(ok=True, uid=uid, created=created)
        if act == "bind":
            uid = (b.get("uid") or "").strip()
            if not uid:
                return jsonify(ok=False, error="缺 uid"), 400
            perm.bind_oid(uid, b.get("oid") or "")
            return jsonify(ok=True)
        if act == "unbind":
            uid = (b.get("uid") or "").strip()
            if not uid:
                return jsonify(ok=False, error="缺 uid"), 400
            perm.bind_oid(uid, "")
            return jsonify(ok=True)
        if act == "link":
            # 给 PC 端（没有飞书宿主）用的入口链接：点一次换 cookie，管 30 天
            uid = (b.get("uid") or "").strip()
            proj = (b.get("proj") or "qty").strip()
            days = int(b.get("days") or 30)
            if not uid:
                return jsonify(ok=False, error="缺 uid"), 400
            if not perm.can(uid, proj):
                return jsonify(ok=False, error="这个人没有「%s」权限，先勾上"
                               % perm.PROJS.get(proj, proj)), 400
            path = _page_of(proj)
            url = "%s%s?k=%s" % (request.host_url.rstrip("/"), path,
                                 perm.long_ticket(uid, proj, days))
            return jsonify(ok=True, url=url, days=days)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    return jsonify(ok=False, error="不认识的 action：" + act), 400


@app.post("/api/admin/import_people")
def api_admin_import():
    """把 data/people.json 里的人批量建成「绑好飞书 id」的账号。

    这份名单的 key 就是 open_id（来源：任务表人员字段），所以建完就能用，
    不需要每个人先来登一次 —— 这是整套免密里最省事的一步。
    """
    book = load_people()
    made, skipped = [], []
    for oid, nm in sorted(book.items(), key=lambda kv: kv[1]):
        if not nm or perm.get_by_oid(oid):
            skipped.append(nm or oid)
            continue
        u = perm.get_user(nm)
        try:
            if u:                                   # 同名的已经手工建过 → 补绑 id
                perm.bind_oid(u["id"], oid)
                skipped.append(nm)
            else:
                perm.add_user(nm, "", perms=["audit"], oid=oid)
                made.append(nm)
        except ValueError:
            skipped.append(nm)
    return jsonify(ok=True, created=made, skipped=skipped,
                   note="已按飞书 id 建号并只开审核；工程量按需勾选")


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


def _write_judgement_panel(conn, rec, rid, run_id, action, note, actor, actor_id):
    """飞书面板那条路（通过 / 退回）也落一条判断事实。

    为什么必须双写：采集只能寄生在「人本来就要做的事」上 —— 他在飞书里点退回，
    其实已经表过态了。这条路没有三选一，只有一句手写理由，所以 dispute_reason
    留空、note 必填（qc_judgement 的 CHECK 允许这种组合）。

    机器判不了的时候「同意」没有意义，直接不写 —— 和 /review 的规矩保持一致。
    """
    ai = str(rec.get("ai") or "").strip()
    if ai not in ("通过", "不合格"):
        return
    stance = "agree" if action == "确认" else "dispute"
    verdict = ("pass" if ai == "通过" else "fail") if stance == "agree" else \
              ("fail" if ai == "通过" else "pass")
    uid = (g.get("uid") or "").strip() or "master"
    prev = conn.execute(
        "SELECT j.* FROM qc_judgement j WHERE j.src='records' AND j.src_key=? AND j.check_item='' "
        "AND j.uid=? AND NOT EXISTS (SELECT 1 FROM qc_judgement x WHERE x.supersedes_id=j.id) "
        "ORDER BY j.id DESC LIMIT 1", (rid, uid)).fetchone()
    if prev and prev["stance"] == stance and (prev["note"] or "") == (note or ""):
        return
    ev = {"ai_verdict": ai, "ai_reason": (rec.get("reason") or "")[:600], "via": "panel"}
    conn.execute(
        "INSERT INTO qc_judgement(uid,oid,actor,src,record_id,src_key,run_id,check_item,"
        "check_cat,rule_ver,verdict,stance,note,ai_verdict,material_hash,evidence_json,"
        "supersedes_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, actor_id or None, actor, "records", rid, rid, run_id, "",
         rec.get("mode"), RULE_VER, verdict, stance, note, ai,
         material_hash(rec), json.dumps(ev, ensure_ascii=False),
         prev["id"] if prev else None, now()))


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
        if action in ("确认", "打回"):
            _write_judgement_panel(conn, rec, rid, run_id, action, stored, actor, actor_id)
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


# ── 跟随心跳遥测 ──────────────────────────────────────────────
# 为什么要有它：面板在飞书 iframe 里「点了左表却不变」，从服务端完全看不见，
# 只能靠人描述顶栏文字，来回猜。改成让页面每几轮把心跳推上来，服务端留存，
# 事后直接读「它到底读到了什么」—— 是没事件、读到旧值，还是被弹窗冻住了。
_PULSE = deque(maxlen=400)


@app.post("/api/pulse")
def api_pulse_post():
    d = request.get_json(force=True, silent=True) or {}
    try:
        n = int(d.get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    _PULSE.append({
        "at": datetime.now().strftime("%H:%M:%S"),
        "n": n,
        "res": str(d.get("res") or "")[:16],
        "sel": str(d.get("sel") or "")[:40],
        "ms": d.get("ms"),
        "same": d.get("same"),
        "frozen": str(d.get("frozen") or "")[:30],
        "evt": d.get("evt"),
        "noevt": str(d.get("noEvt") or "")[:70],
        "sdk": str(d.get("sdk") or "")[:24],
        "locked": str(d.get("locked") or "")[:40],
        "err": str(d.get("err") or "")[:100],
        "ua": (request.headers.get("User-Agent") or "")[:50],
    })
    return jsonify(ok=True, count=len(_PULSE))


@app.get("/api/pulse")
def api_pulse_get():
    limit = min(int(request.args.get("limit") or 40), 400)
    return jsonify(ok=True, count=len(_PULSE), items=list(_PULSE)[-limit:])


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
    ok = 1 if body.get("ok") else 0
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


# ══════════════════════════════════════════════════════════════════════
# 核对台（/review）
#
# 两类材料**并列**，绝不混算：
#   src=records —— 飞书里的真实报告，只有整条 AI 结论，没有检查项明细（90 条）
#   src=compare —— 比对产出的检查项明细，但全是测试项目，没有飞书记录号（17 个项目）
# 已实测二者没有共同键（报告编号 XDL… vs 项目名 绿驹…，零重叠），
# 所以页面必须把「有明细」和「只有结论」分开显示，不能装成一份数据。
# ══════════════════════════════════════════════════════════════════════

PROJ_DB = (os.environ.get("QC_PROJECTS_DB")
           or r"C:\Users\lenovo\Desktop\综合测绘报告\projects.db")

CAT_CN = {
    "redline_corner": "红线角点", "redline_area": "红线面积", "redline": "红线",
    "setback": "退界", "building_corner": "建筑角点", "basement_corner": "地下室角点",
    "building_area": "建筑面积", "floor_height": "楼层净高", "height": "建筑高度",
    "outline": "建筑轮廓", "indicator": "指标", "building": "分幢",
    "building_sheets": "分幢表", "opinion": "文字意见",
}

STANCES = ("agree", "dispute", "hold")
DISPUTE_REASONS = ("图不对", "机器看错", "规矩不对")
HOLD_REASONS = ("材料不清", "缺规划依据", "无对应规则", "需现场核实")

# 文件类型 → 受它影响的检查类别。两侧文件字节相同，这一类就是在拿一份文件跟自己比。
# masterplan 兜底所有几何类，因为总平面图是它们共同的上游。
FT2CAT = {
    "redline": {"redline", "redline_corner", "redline_area", "setback"},
    "building": {"building", "building_corner", "building_area", "building_sheets",
                 "outline", "floor_height", "height"},
    "basement": {"basement_corner"},
    "masterplan": {"building", "building_corner", "building_area", "building_sheets",
                   "outline", "floor_height", "height", "setback", "indicator"},
}


def _projdb():
    """只读打开比对库。

    必须 mode=ro —— 那是另一条产线的真源，服务台只许看。
    库不在（换机器、路径变了）就抛，由调用方转成 unavailable，
    绝不返回空列表冒充「没有材料」。
    """
    uri = "file:" + PROJ_DB.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _mt(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


_CMP_CACHE = {"mt": -1, "mats": None}


def _cmp_materials():
    """每个项目只取**最新一轮**（历史上多轮是同一批数据反复算，不是新观测）。"""
    if _CMP_CACHE["mats"] is not None and _CMP_CACHE["mt"] == _mt(PROJ_DB):
        return _CMP_CACHE["mats"]
    mats = []
    try:
        conn = _projdb()
    except Exception as e:
        raise RuntimeError(f"比对库打不开：{e}")
    try:
        rows = conn.execute("""
            WITH last AS (
              SELECT project_id, MAX(round_num) rnd
              FROM compare_results GROUP BY project_id
            )
            SELECT p.id pid, p.name pname, COALESCE(p.client,'') client,
                   l.rnd rnd, COUNT(c.item) n,
                   SUM(CASE WHEN json_extract(c.detail,'$.pass') IS NULL THEN 1 ELSE 0 END) unk,
                   SUM(CASE WHEN json_extract(c.detail,'$.pass') = 1 THEN 1 ELSE 0 END) ok,
                   SUM(CASE WHEN json_extract(c.detail,'$.pass') = 0 THEN 1 ELSE 0 END) bad
            FROM projects p
            LEFT JOIN last l ON l.project_id = p.id
            LEFT JOIN compare_results c
                   ON c.project_id = p.id AND c.round_num = l.rnd
            GROUP BY p.id, p.name, p.client, l.rnd
            ORDER BY n DESC, p.id
        """).fetchall()
        for r in rows:
            n = r["n"] or 0
            mats.append({
                "src": "compare",
                "key": f"cm:{r['pid']}:{r['rnd'] or 0}",
                "title": r["pname"] or f"项目 {r['pid']}",
                "sub": r["client"] or "",
                "round": r["rnd"] or 0,
                "total": n, "pass": r["ok"] or 0, "fail": r["bad"] or 0,
                "hold": r["unk"] or 0,
                "note": "" if n else "这项材料一次比对都没跑出结果",
            })
    finally:
        conn.close()
    # 同名 + 同项数 = 同一批数据的重复跑。实库里真的有：p1/p3 都叫「绿驹MVP验证」
    # 各 157 项，p16/p17 都叫「资料2一键验收」各 30 项，「未命名项目」有 7 个。
    # 界面渲染出来一模一样，人在左侧点来点去会以为「点了没反应」——
    # 所以把同批关系算出来交给前端显式说明，而不是让人自己猜。
    batch, first_of = {}, {}
    for m in mats:
        gk = (m["title"], m["total"])
        batch[gk] = batch.get(gk, 0) + 1
    for m in mats:
        gk = (m["title"], m["total"])
        if gk in first_of:
            m["dup_of"] = first_of[gk]
        else:
            m["dup_of"] = ""
            first_of[gk] = m["key"]
        m["batch"] = batch[gk]
    _CMP_CACHE.update(mt=_mt(PROJ_DB), mats=mats)
    return mats


def _rec_materials():
    if not _records:
        load_records()
    out = []
    for r in _records:
        ai = str(r.get("ai") or "").strip() or "判不了"
        out.append({
            "src": "records",
            "key": r["record_id"],
            "title": r.get("tn") or r["record_id"],
            "sub": (r.get("name") or "") + ((" · " + r["sub"]) if r.get("sub") else ""),
            "round": 0,
            "total": 1,
            "pass": 1 if ai == "通过" else 0,
            "fail": 1 if ai == "不合格" else 0,
            "hold": 0 if ai in ("通过", "不合格") else 1,
            "note": "这份材料没有检查项明细，只能整条表态",
        })
    return out


def _marks_for(src, keys):
    """一次取回一批材料已有的表态，key -> {check_item: row}。

    「当前生效」= 没有别人用 supersedes_id 指向它。写成 supersedes_id IS NULL
    是个陷阱：那样只认第一条，人改过主意之后读回来还是旧结论。
    """
    if not keys:
        return {}
    q = ",".join("?" * len(keys))
    out = {}
    with db() as conn:
        rows = conn.execute(
            "SELECT j.* FROM qc_judgement j WHERE j.src=? AND j.src_key IN (" + q + ") "
            "AND NOT EXISTS (SELECT 1 FROM qc_judgement x WHERE x.supersedes_id = j.id) "
            "ORDER BY j.id", [src] + list(keys)).fetchall()
    for r in rows:
        d = out.setdefault(r["src_key"], {})
        d[r["check_item"]] = dict(r)          # ORDER BY id，后面的覆盖前面的 = 最新
    return out


@app.get("/api/review/materials")
def api_review_materials():
    """两类材料分开返回；比对库打不开时 compare_error 会写明原因。"""
    compare, err = [], ""
    try:
        compare = _cmp_materials()
    except Exception as e:
        err = str(e)
    records = _rec_materials()

    mk = _marks_for("compare", [m["key"] for m in compare])
    mr = _marks_for("records", [m["key"] for m in records])
    for m in compare:
        done = mk.get(m["key"], {})
        m["done"] = len(done)
        m["dispute"] = sum(1 for v in done.values() if v["stance"] == "dispute")
        m["human_hold"] = sum(1 for v in done.values() if v["stance"] == "hold")
    for m in records:
        done = mr.get(m["key"], {})
        m["done"] = len(done)
        m["dispute"] = sum(1 for v in done.values() if v["stance"] == "dispute")
        m["human_hold"] = sum(1 for v in done.values() if v["stance"] == "hold")
    return jsonify(ok=True, compare=compare, records=records,
                   compare_error=err, proj_db=PROJ_DB)


@app.get("/api/review/items/<src>/<path:key>")
def api_review_items(src, key):
    if src not in ("records", "compare"):
        return jsonify(ok=False, error="src 只能是 records / compare"), 400

    if src == "records":
        if not _records:
            load_records()
        rec = _by_rid.get(key)
        if not rec:
            return jsonify(ok=False, error="没有这份材料"), 404
        ai = str(rec.get("ai") or "").strip()
        items = [{
            "item": "",                      # '' = 整条记录
            "cat": rec.get("mode") or "",
            "cat_cn": "整条结论",
            "building": "",
            "label": "整条记录 · " + (rec.get("tn") or rec["record_id"]),
            "measured": None, "planned": None, "diff": None, "tolerance": None,
            "verdict": ("pass" if ai == "通过" else "fail" if ai == "不合格" else "hold"),
            "remark": rec.get("reason") or "",
        }]
        meta = {
            "src": src, "key": key, "title": rec.get("tn") or key,
            "sub": rec.get("name") or "", "round": 0,
            "material_hash": material_hash(rec),
            "rule_ver": RULE_VER,
            "has_detail": False,
            "img_url": "/img/" + os.path.basename(rec["img"]) if rec.get("img") else None,
        }
    else:
        try:
            conn = _projdb()
        except Exception as e:
            return jsonify(ok=False, error=f"比对库打不开：{e}"), 503
        try:
            parts = key.split(":")
            if len(parts) != 3 or parts[0] != "cm":
                return jsonify(ok=False, error="材料编号格式不对"), 400
            pid, rnd = int(parts[1]), int(parts[2])
            rows = conn.execute("""
                SELECT category, building_id, item, measured, planned, diff, tolerance,
                       json_extract(detail,'$.pass') pv, detail
                FROM compare_results WHERE project_id=? AND round_num=?
                ORDER BY category, building_id, item
            """, (pid, rnd)).fetchall()
            pj = conn.execute("SELECT name, client FROM projects WHERE id=?", (pid,)).fetchone()
            vers = conn.execute(
                "SELECT side,file_type,stored_path,file_size FROM file_versions "
                "WHERE project_id=? AND is_current=1", (pid,)).fetchall()
        finally:
            conn.close()

        items = []
        # 同源文件：两侧字节相同，这一类等于拿一份文件跟自己比
        same = _same_source_types(pid)
        same_cats = set()
        for ft in same:
            same_cats |= FT2CAT.get(ft, {ft})

        # 单轮内同一条目重复：实测 p1 每个地下室角点被记了 4 遍（全库 108 行这种）。
        # 按行显示的话，人会对着同一个点表态四次 —— 那就是又一批假样本，必须合并。
        # 合并规则：同 (类别, 楼栋, 检查项) 只留一行，结论取最差的（不过 > 判不了 > 通过）。
        rank = {"pass": 0, "hold": 1, "fail": 2}
        raw_n = len(rows)
        uniq, seen = [], {}
        for r in rows:
            pv = r["pv"]
            v = "pass" if pv == 1 else "fail" if pv == 0 else "hold"
            k = (r["category"], r["building_id"] or "", r["item"])
            if k in seen:
                u = seen[k]
                u["dup"] += 1
                if (r["measured"], r["planned"], r["diff"]) != (u["measured"], u["planned"], u["diff"]):
                    u["dup_diff"] = True
                if rank[v] > rank[u["verdict"]]:
                    u["verdict"] = v
                continue
            u = {"item": r["item"], "cat": r["category"],
                 "cat_cn": CAT_CN.get(r["category"], r["category"]),
                 "building": r["building_id"] or "", "label": r["item"],
                 "measured": r["measured"], "planned": r["planned"],
                 "diff": r["diff"], "tolerance": r["tolerance"],
                 "verdict": v, "remark": _remark(r["detail"]),
                 "dup": 1, "dup_diff": False}
            seen[k] = u
            uniq.append(u)

        # 整类零差：≥10 行且差全为 0。两个独立来源之间不可能整类零差，
        # 这是「不同文件但照抄折线」的唯一信号（p15–p17）。只报警、不改数。
        cnt = {}
        for u in uniq:
            c = cnt.setdefault(u["cat"], [0, 0])
            c[0] += 1
            if u["diff"] == 0:
                c[1] += 1
        zero = {c for c, (n, d0) in cnt.items() if n >= 10 and d0 == n}

        for u in uniq:
            u["same_source"] = u["cat"] in same_cats
            u["zero_suspect"] = u["cat"] in zero
            items.append(u)
        raw = "|".join(sorted(f"{v['side']}:{v['file_type']}:{v['stored_path']}:{v['file_size']}"
                              for v in vers))
        meta = {
            "src": src, "key": key,
            "title": (pj["name"] if pj else f"项目 {pid}"),
            "sub": (pj["client"] if pj else "") or "",
            "round": rnd,
            "material_hash": hashlib.sha1(f"{pid}@{rnd}#{raw}".encode("utf-8")).hexdigest()[:16],
            "rule_ver": RULE_VER,
            "has_detail": True,
            "img_url": None,
            "same_source_types": sorted(same),
            "zero_types": sorted(zero),
            "raw_total": raw_n,
            "dup_total": raw_n - len(uniq),
        }

    marks = _marks_for(src, [key]).get(key, {})
    for it in items:
        it["mark"] = marks.get(it["item"])
    return jsonify(ok=True, meta=meta, items=items,
                   reasons={"dispute": list(DISPUTE_REASONS), "hold": list(HOLD_REASONS)})


def _remark(detail):
    try:
        d = json.loads(detail or "{}")
        return d.get("remark") or ""
    except Exception:
        return ""


_SHA_CACHE = {}


def _file_sha(path):
    """按 (路径,大小,mtime) 缓存，不重复读盘；文件一变缓存自然失效。"""
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    k = (path, st.st_size, int(st.st_mtime))
    if k in _SHA_CACHE:
        return _SHA_CACHE[k]
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        v = h.hexdigest()
    except OSError:
        v = "missing"
    _SHA_CACHE[k] = v
    return v


def _same_source_types(pid):
    """两侧同名文件字节完全相同 → 这一类的比对是拿一份文件跟自己比，结论无意义。

    实测：p1 的红线与建筑两侧 sha 完全一致，所以 157 行零差。页面必须标出来，
    否则人会在那一屏上「同意」，把假数据变成假证据。
    """
    try:
        conn = _projdb()
    except Exception:
        return set()
    try:
        rows = conn.execute(
            "SELECT side,file_type,stored_path FROM file_versions "
            "WHERE project_id=? AND is_current=1", (pid,)).fetchall()
    finally:
        conn.close()
    by = {}
    for r in rows:
        by.setdefault(r["file_type"], {})[r["side"]] = _file_sha(r["stored_path"])
    out = set()
    for ft, d in by.items():
        s, p = d.get("survey"), d.get("plan")
        if s and p and s == p and s != "missing":
            out.add(ft)
    return out


def _ai_verdict_for(src, key, item):
    """服务端自己回查 AI 结论，不信前端传上来的 —— 否则人可以自报「AI 说不过」。"""
    if src == "records":
        if not _records:
            load_records()
        rec = _by_rid.get(key)
        if not rec:
            return None
        ai = str(rec.get("ai") or "").strip()
        return ai if ai in ("通过", "不合格") else "判不了"
    try:
        conn = _projdb()
    except Exception:
        return None
    try:
        parts = key.split(":")
        pid, rnd = int(parts[1]), int(parts[2])
        r = conn.execute(
            "SELECT json_extract(detail,'$.pass') pv, detail FROM compare_results "
            "WHERE project_id=? AND round_num=? AND item=? LIMIT 1", (pid, rnd, item)).fetchone()
        if not r:
            return None
        pv = r["pv"]
        return "通过" if pv == 1 else "不合格" if pv == 0 else "判不了"
    finally:
        conn.close()


@app.post("/api/review/mark")
def api_review_mark():
    """一条表态 = 一行事实。只追加；改主意靠 supersedes_id 指旧行。

    机器的话一个字都不改 —— 这里不碰 qc_result，也不碰飞书字段。
    """
    body = request.get_json(force=True, silent=True) or {}
    src = (body.get("src") or "").strip()
    key = (body.get("key") or "").strip()
    item = body.get("item")
    item = "" if item is None else str(item)
    stance = (body.get("stance") or "").strip()
    d_reason = (body.get("dispute_reason") or "").strip() or None
    h_reason = (body.get("hold_reason") or "").strip() or None
    note = (body.get("note") or "").strip() or None
    my_verdict = (body.get("my_verdict") or "").strip()   # 仅 AI=判不了 且人有意见时需要

    if src not in ("records", "compare") or not key:
        return jsonify(ok=False, error="src / key 不合法"), 400
    if stance not in STANCES:
        return jsonify(ok=False, error="stance 只能是 agree / dispute / hold"), 400
    if stance == "dispute" and d_reason not in DISPUTE_REASONS:
        return jsonify(ok=False, error="有意见必须选一个原因"), 400
    if stance == "hold" and h_reason not in HOLD_REASONS:
        return jsonify(ok=False, error="判不了必须选一个原因"), 400

    ai = _ai_verdict_for(src, key, item)
    if ai is None:
        return jsonify(ok=False, error="这条判断在源库里找不到了，先刷新再说"), 404

    if stance == "hold":
        verdict = "hold"
    elif stance == "agree":
        verdict = "hold" if ai == "判不了" else ("pass" if ai == "通过" else "fail")
        if ai == "判不了":
            return jsonify(ok=False, error="AI 自己也没判出来，这条只能选「判不了」或有意见"), 400
    else:  # dispute
        if ai == "判不了":
            if my_verdict not in ("pass", "fail"):
                return jsonify(ok=False, error="AI 没判出来，请先选你的结论（通过 / 不合格）"), 400
            verdict = my_verdict
        else:
            verdict = "fail" if ai == "通过" else "pass"

    uid = (g.get("uid") or "").strip() or "master"
    oid = ""
    u = perm.get_user(uid=uid) if uid != "master" else None
    actor = (u or {}).get("name") or uid
    if not oid:
        oid = (u or {}).get("oid") or None

    ev = {"ai_verdict": ai, "stance": stance,
          "ai_reason": (body.get("ai_reason") or "")[:600],
          "shown": body.get("shown") or {}}
    mh = (body.get("material_hash") or "").strip()
    if not mh:
        mh = hashlib.sha1(f"{src}|{key}".encode("utf-8")).hexdigest()[:16]

    with db() as conn:
        prev = conn.execute(
            "SELECT j.* FROM qc_judgement j WHERE j.src=? AND j.src_key=? AND j.check_item=? AND j.uid=? "
            "AND NOT EXISTS (SELECT 1 FROM qc_judgement x WHERE x.supersedes_id = j.id) "
            "ORDER BY j.id DESC LIMIT 1",
            (src, key, item, uid)).fetchone()
        # 连点两下不产生两行：同结论同原因、60 秒内视为同一次
        if prev and prev["stance"] == stance and (prev["dispute_reason"] or "") == (d_reason or "") \
                and (prev["hold_reason"] or "") == (h_reason or "") and (prev["note"] or "") == (note or ""):
            return jsonify(ok=True, id=prev["id"], dedup=True,
                           stance=stance, verdict=verdict, ai_verdict=ai)
        cur = conn.execute(
            "INSERT INTO qc_judgement(uid,oid,actor,src,record_id,src_key,run_id,check_item,"
            "check_cat,building,rule_code,rule_ver,verdict,stance,dispute_reason,hold_reason,"
            "note,ai_verdict,material_hash,evidence_json,elapsed_ms,device,supersedes_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, oid, actor, src,
             key if src == "records" else "",
             key, body.get("run_id"),
             item, body.get("check_cat"), body.get("building"),
             body.get("rule_code"), body.get("rule_ver") or RULE_VER,
             verdict, stance, d_reason, h_reason, note, ai,
             mh, json.dumps(ev, ensure_ascii=False),
             body.get("elapsed_ms"), (request.headers.get("User-Agent") or "")[:200],
             prev["id"] if prev else None, now()))
        jid = cur.lastrowid
    return jsonify(ok=True, id=jid, stance=stance, verdict=verdict,
                   ai_verdict=ai, supersedes=(prev["id"] if prev else None))


@app.get("/api/review/stats")
def api_review_stats():
    with db() as conn:
        tot = conn.execute("SELECT COUNT(*) c FROM qc_judgement").fetchone()["c"]
        by = conn.execute(
            "SELECT j.src, j.stance, COUNT(*) c FROM qc_judgement j "
            "WHERE NOT EXISTS (SELECT 1 FROM qc_judgement x WHERE x.supersedes_id = j.id) "
            "GROUP BY 1,2").fetchall()
        users = conn.execute(
            "SELECT uid, actor, COUNT(*) c FROM qc_judgement GROUP BY 1,2 ORDER BY c DESC"
        ).fetchall()
        med = conn.execute(
            "SELECT AVG(elapsed_ms) a FROM qc_judgement WHERE elapsed_ms IS NOT NULL").fetchone()["a"]
    return jsonify(ok=True, total=tot,
                   by=[{"src": r["src"], "stance": r["stance"], "n": r["c"]} for r in by],
                   users=[{"uid": r["uid"], "actor": r["actor"], "n": r["c"]} for r in users],
                   avg_elapsed_ms=round(med or 0))


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
