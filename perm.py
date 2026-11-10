# -*- coding: utf-8 -*-
"""人与项目：谁能进哪个模块。

三个东西在此定死，别的文件不许另造一套：

1. **账号**：`qc_user` 一表一人。**认人靠飞书 open_id**（`oid` 列），不是靠口令：
   内业人员在飞书里打开面板，宿主把 `bridge.getUserId()` 给他，服务端拿这个 id 查授权，零输入。
   口令是可选项（`pw` 空串 = 免密账号），只给管理员留一个应急入口。
   角色只有两种：`admin`（能进管理页、能改所有人的授权）与 `user`（只能进被授权的项目）。
2. **授权**：`qc_perm` 一项目一人一行。项目号是内部短码（`audit` / `qty`），
   界面上显示中文名。没这一行 = 看不见这个项目，入口页就不出那张卡。
3. **身份传递**：两条通道，不混用。
   - 顶层浏览器（入口页、工程量页）：`qc_sess` cookie，HMAC 签名，12 小时。
     拿票据打开页面时会顺手把票据换成 cookie，所以「点一次管理员发的链接」就够，之后直接开首页。
   - 飞书 iframe（审核面板）：cookie 在第三方上下文里会被浏览器拦，
     所以走短票据 `?k=<uid>.<proj>.<exp>.<sig>` —— panel.html 现在就把 `?k=` 原样转发到每个请求，
     票据复用它，前端一行都不用改。主口令（QC_TOKEN）仍然有效，留作管理通道。

**这套认人的边界要说准确**：open_id 由前端上报，服务端没验签，改请求就能冒名。
它的作用是把「谁能看哪个模块」落到人头上、并且可追溯，**不是不可否认**。
要更硬，得拿飞书应用凭证把代码换成 `user_access_token` 再反查——那是下一步的事，别当成已经做到。

为什么票据不用 cookie：飞书桌面端是 Chromium，第三方 cookie 默认拦；面板要能用，只能靠 URL 带凭据。
票据里带 proj，所以「有审核票据」不等于「能进工程量页」。
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# 测试用 QC_DATA 指向临时目录，一次把库、密钥、管理员口令文件全隔开
DATA = os.environ.get("QC_DATA") or os.path.join(HERE, "data")
DB = os.environ.get("QC_DB") or os.path.join(DATA, "qc.db")
SECRET_FILE = os.path.join(DATA, "secret.key")
ADMIN_PW_FILE = os.path.join(DATA, "perm_admin.txt")   # 首次初始化的管理员口令（明文，仅本机，交付后删）

COOKIE = "qc_sess"
SESSION_TTL = 12 * 3600
TICKET_TTL = 12 * 3600

# 项目短码 -> 显示名。加项目只改这里，入口页与管理页都读它。
PROJS = {
    "audit": "测绘报告审核",
    "qty": "工程量统计",
    "shrink": "文件压缩",
    "watermark": "成果水印",
}
PROJ_DESC = {
    "audit": "逐项核对：机器结论 + 实测/规划对照，人点同意或有意见",
    "qty": "dwg 按图层统计工程量，与清单对撞",
    "shrink": "pdf / 图片压到 4MB 以下，格式不变、文件不上传",
    "watermark": "doc/docx/pdf 打溯源水印并强制加密，出件即记台账（独立服务）",
}

DDL = """
CREATE TABLE IF NOT EXISTS qc_user (
  id         TEXT PRIMARY KEY,        -- 内部 id：u_ + 6 位，不随姓名变
  name       TEXT NOT NULL UNIQUE,    -- 显示名（中文姓名），同时是人工建的登录名
  role       TEXT NOT NULL DEFAULT 'user',   -- admin / user
  pw         TEXT NOT NULL DEFAULT '',       -- salt$hash；空串 = 不设口令，只认飞书身份
  oid        TEXT,                    -- 飞书 open_id（bridge.getUserId()），认人就靠它
  active     INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS qc_perm (
  uid  TEXT NOT NULL,
  proj TEXT NOT NULL,
  PRIMARY KEY (uid, proj)
);
-- 没开通的人打开面板时会留下一行：管理员照这份名单一键开通，不用手工抄 open_id
CREATE TABLE IF NOT EXISTS qc_visit (
  oid      TEXT PRIMARY KEY,
  name     TEXT,
  first_at TEXT NOT NULL,
  last_at  TEXT NOT NULL,
  hits     INTEGER NOT NULL DEFAULT 1
);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


_READY = False


def db():
    global _READY
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    if not _READY:                 # 第一次连上就把表备好，任何入口都不会撞「no such table」
        conn.executescript(DDL)
        _migrate(conn)
        _READY = True
    return conn


def _migrate(conn):
    """老库补列。CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，必须显式 ALTER。

    不补这一列的话，`SELECT *` 出来没有 oid，认人整条线静默失效 —— 而且不报错，
    只会表现为「所有人都进不去」，最难查。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(qc_user)")}
    if "oid" not in cols:
        conn.execute("ALTER TABLE qc_user ADD COLUMN oid TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_oid "
                 "ON qc_user(oid) WHERE oid IS NOT NULL")


# ---------------------------------------------------------------- 密钥 / 口令
def secret():
    """签名密钥。文件不存在就生成一次，之后所有会话与票据都靠它。"""
    if os.path.isfile(SECRET_FILE):
        with open(SECRET_FILE, "rb") as f:
            s = f.read().strip()
        if s:
            return s
    os.makedirs(DATA, exist_ok=True)
    s = secrets.token_bytes(32)
    with open(SECRET_FILE, "wb") as f:
        f.write(s)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass
    return s


def hash_pw(plain, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.sha256((salt + (plain or "")).encode("utf-8")).hexdigest()


def verify_pw(plain, stored):
    try:
        salt, digest = (stored or "").split("$", 1)
    except ValueError:
        return False
    got = hashlib.sha256((salt + (plain or "")).encode("utf-8")).hexdigest()
    return hmac.compare_digest(got, digest)


# ---------------------------------------------------------------- 用户 / 授权
def init():
    with db() as conn:
        conn.executescript(DDL)
    return ensure_admin()


def ensure_admin():
    """没有管理员就建一个，口令写 data/perm_admin.txt（只本机可见）。

    为什么不硬编码默认口令：这服务挂在公网隧道上，默认口令等于没锁。
    """
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM qc_user WHERE role='admin'").fetchone()
        if row["c"]:
            return None
        pw = secrets.token_urlsafe(9)
        uid = "u_" + secrets.token_hex(3)
        conn.execute(
            "INSERT INTO qc_user(id,name,role,pw,active,created_at) VALUES (?,?,?,?,1,?)",
            (uid, "admin", "admin", hash_pw(pw), now()),
        )
        for proj in PROJS:
            conn.execute("INSERT OR IGNORE INTO qc_perm(uid,proj) VALUES (?,?)", (uid, proj))
    os.makedirs(DATA, exist_ok=True)
    with open(ADMIN_PW_FILE, "w", encoding="utf-8") as f:
        f.write(f"登录名 admin\n口令 {pw}\n生成于 {now()}\n")
    print(f"[perm] 已创建管理员 admin，口令见 {ADMIN_PW_FILE}")
    return pw


def get_user(name=None, uid=None):
    sql = "SELECT * FROM qc_user WHERE " + ("id=?" if uid else "name=?")
    with db() as conn:
        r = conn.execute(sql, (uid or name,)).fetchone()
    return dict(r) if r else None


def list_users():
    with db() as conn:
        rows = conn.execute("SELECT * FROM qc_user ORDER BY role DESC, name").fetchall()
        perms = {}
        for p in conn.execute("SELECT uid, proj FROM qc_perm").fetchall():
            perms.setdefault(p["uid"], []).append(p["proj"])
    out = []
    for r in rows:
        u = dict(r)
        u["has_pw"] = bool(u.get("pw"))
        u.pop("pw", None)
        u["perms"] = sorted(perms.get(u["id"], []))
        out.append(u)
    return out


def add_user(name, pw="", role="user", perms=(), oid=None):
    """建账号。口令可以不给（pw=""）—— 那这个账号只能靠飞书身份进，是现在的常态。

    两者都不给就拒：既不绑 open_id 又没口令，等于建了个谁也进不去的空壳。
    """
    name = (name or "").strip()
    oid = (oid or "").strip() or None
    if not name:
        raise ValueError("姓名不能为空")
    if not oid and not (pw or ""):
        raise ValueError("要么绑飞书 id，要么给口令，两者都没有就没人能进")
    if pw and len(pw) < 6:
        raise ValueError("口令至少 6 位")
    if get_user(name):
        raise ValueError("同名账号已存在")
    if oid:
        _assert_oid_free(oid)
    uid = "u_" + secrets.token_hex(3)
    with db() as conn:
        conn.execute(
            "INSERT INTO qc_user(id,name,role,pw,oid,active,created_at) VALUES (?,?,?,?,?,1,?)",
            (uid, name, "admin" if role == "admin" else "user",
             hash_pw(pw) if pw else "", oid, now()),
        )
        for p in perms:
            if p in PROJS:
                conn.execute("INSERT OR IGNORE INTO qc_perm(uid,proj) VALUES (?,?)", (uid, p))
    return uid


def _assert_oid_free(oid, uid=None):
    with db() as conn:
        row = conn.execute("SELECT id,name FROM qc_user WHERE oid=?", (oid,)).fetchone()
    if row and row["id"] != uid:
        raise ValueError("这个飞书 id 已经绑给「%s」了" % row["name"])


def get_by_oid(oid):
    """认人的唯一入口。返回的 dict 不含口令。"""
    oid = (oid or "").strip()
    if not oid:
        return None
    with db() as conn:
        r = conn.execute("SELECT * FROM qc_user WHERE oid=?", (oid,)).fetchone()
    if not r:
        return None
    u = dict(r)
    u.pop("pw", None)
    return u


def bind_oid(uid, oid):
    """绑 / 解绑飞书 id。oid 传空串 = 解绑（那个人立刻进不去，留痕还在）。"""
    oid = (oid or "").strip() or None
    with db() as conn:
        if not conn.execute("SELECT 1 FROM qc_user WHERE id=?", (uid,)).fetchone():
            raise ValueError("账号不存在")
    if oid:
        _assert_oid_free(oid, uid)
    with db() as conn:
        conn.execute("UPDATE qc_user SET oid=? WHERE id=?", (oid, uid))
    return True


def record_visit(oid, name=""):
    """没开通的人来过的痕迹。管理员照这份名单一键开通，省掉「你的 open_id 是多少」的来回。"""
    oid = (oid or "").strip()
    if not oid:
        return
    name = (name or "").strip()[:40]
    with db() as conn:
        row = conn.execute("SELECT oid FROM qc_visit WHERE oid=?", (oid,)).fetchone()
        if row:
            conn.execute(
                "UPDATE qc_visit SET last_at=?, hits=hits+1, "
                "name=CASE WHEN ?<>'' THEN ? ELSE name END WHERE oid=?",
                (now(), name, name, oid))
        else:
            conn.execute("INSERT INTO qc_visit(oid,name,first_at,last_at,hits) VALUES (?,?,?,?,1)",
                         (oid, name, now(), now()))
        # 这是公开接口（认人之前就得能调），防一手刷：名单只留最近的 300 条
        conn.execute("DELETE FROM qc_visit WHERE oid NOT IN "
                     "(SELECT oid FROM qc_visit ORDER BY last_at DESC LIMIT 300)")


def list_visits():
    """待开通名单：来过、但当前没有任何账号绑着这个 id。已绑定的不再打扰。"""
    with db() as conn:
        rows = conn.execute("SELECT * FROM qc_visit ORDER BY last_at DESC").fetchall()
        bound = {r["oid"] for r in conn.execute("SELECT oid FROM qc_user WHERE oid IS NOT NULL")}
    return [dict(r) for r in rows if r["oid"] not in bound]


def drop_visit(oid):
    with db() as conn:
        conn.execute("DELETE FROM qc_visit WHERE oid=?", ((oid or "").strip(),))


def set_user(uid, *, pw=None, role=None, active=None, perms=None):
    """改口令 / 角色 / 启停 / 授权。perms 传列表时是**整体替换**，不是追加。"""
    with db() as conn:
        if not conn.execute("SELECT 1 FROM qc_user WHERE id=?", (uid,)).fetchone():
            raise ValueError("账号不存在")
        if pw:
            if len(pw) < 6:
                raise ValueError("口令至少 6 位")
            conn.execute("UPDATE qc_user SET pw=? WHERE id=?", (hash_pw(pw), uid))
        if role in ("admin", "user"):
            conn.execute("UPDATE qc_user SET role=? WHERE id=?", (role, uid))
        if active is not None:
            conn.execute("UPDATE qc_user SET active=? WHERE id=?", (1 if active else 0, uid))
        if perms is not None:
            conn.execute("DELETE FROM qc_perm WHERE uid=?", (uid,))
            for p in perms:
                if p in PROJS:
                    conn.execute("INSERT OR IGNORE INTO qc_perm(uid,proj) VALUES (?,?)", (uid, p))
    return True


def perms_of(uid):
    with db() as conn:
        rows = conn.execute("SELECT proj FROM qc_perm WHERE uid=?", (uid,)).fetchall()
    return {r["proj"] for r in rows}


def can(uid, proj):
    u = get_user(uid=uid)
    if not u or not u.get("active"):
        return False
    if u["role"] == "admin":
        return True                      # 管理员不按项目逐条授权，不然改配置自己先被锁外面
    return proj in perms_of(uid)


def auth_user(name, pw):
    """口令对 + 账号启用才放行；顺带记 last_login。返回用户 dict 或 None。

    免密账号（pw 存空串）在这里一律拒绝 —— 否则空口令就能登进来，等于开了后门。
    """
    u = get_user(name)
    if not u or not u.get("active") or not (u.get("pw") or ""):
        return None
    if not verify_pw(pw, u["pw"]):
        return None
    with db() as conn:
        conn.execute("UPDATE qc_user SET last_login=? WHERE id=?", (now(), u["id"]))
    u.pop("pw", None)
    return u


# ---------------------------------------------------------------- 会话 / 票据
def _sign(raw):
    return hmac.new(secret(), raw.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _pack(obj):
    raw = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
    return raw + "." + _sign(raw)


def _unpack(text, ttl):
    try:
        raw, sig = (text or "").rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(raw), sig):
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        obj = json.loads(base64.urlsafe_b64decode(raw + pad))
    except Exception:
        return None
    if int(obj.get("exp") or 0) < int(time.time()):
        return None
    return obj


def new_session(uid):
    return _pack({"uid": uid, "exp": int(time.time()) + SESSION_TTL, "n": secrets.token_hex(4)})


def read_session(tok):
    obj = _unpack(tok, SESSION_TTL)
    return (obj or {}).get("uid")


def new_ticket(uid, proj):
    """面板票据：塞在 ?k= 里，panel.html 不需要改。"""
    return _pack({"uid": uid, "p": proj, "exp": int(time.time()) + TICKET_TTL,
                  "n": secrets.token_hex(4)})


def read_ticket(tok):
    obj = _unpack(tok, TICKET_TTL)
    if not obj or (obj.get("p") not in PROJS and obj.get("p") != "admin"):
        return None
    return obj


def long_ticket(uid, proj, days=30):
    """发给个人 PC 的入口链接（没有飞书宿主，认不出人）。

    用一次就把票据换成 cookie，所以「发一次链接」够用一个月；
    链接可转发，等于凭据 —— 谁转发谁负责，别往群里发。
    proj 允许 `admin`：管理员自己也要能不开口令进管理页。
    """
    if proj not in PROJS and proj != "admin":
        raise ValueError("项目号不对")
    return _pack({"uid": uid, "p": proj, "exp": int(time.time()) + int(days) * 86400,
                  "n": secrets.token_hex(4)})


def cookie_header(tok, secure=False):
    parts = [f"{COOKIE}={tok}", "Path=/", f"Max-Age={SESSION_TTL}", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie():
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


if __name__ == "__main__":       # python perm.py  → 初始化 + 打印管理员口令位置
    import sys
    pw = init()
    if pw:
        print("管理员口令：", pw)
    else:
        print("管理员已存在，口令见", ADMIN_PW_FILE)
    args = sys.argv[1:]
    if args[:1] == ["bind"] and len(args) >= 4:
        bind_oid(get_user(args[1])["id"], args[2])
        print("已绑定", args[1], "→", args[2])
    if args[:1] == ["visits"]:
        for v in list_visits():
            print(f"  {v['oid']}  {v['name'] or '（无姓名）'}  最近 {v['last_at']}  来过 {v['hits']} 次")
    for u in list_users():
        print(f"  {u['name']:<8} {u['role']:<6} 项目={','.join(u['perms']) or '无'}"
              f" 飞书id={u.get('oid') or '未绑'}")
