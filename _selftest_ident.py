# -*- coding: utf-8 -*-
"""免密认人自测：起隔离实例（临时数据目录 + 独立端口），走真实 HTTP。

验的是「没有账号口令，按飞书 id 判权限」这条线的每一段：
  没凭据的面板外壳能开、接口仍 401；
  不认识的 id → 403 且进「待开通」；再来一次计数 +1；
  管理员一键开通 → 立刻能换到票据；票据只通被开通的那个模块；
  票据换 cookie（PC 点一次链接就记住）；空口令不能走口令通道；
  一个 id 不能绑两个人；同名账号补绑不新建；解绑后进不来；
  停用立刻生效；批量按名单建号后本人直接能用；主口令通道未回归。

用法：python _selftest_ident.py     （不碰生产库：QC_DATA 指向临时目录）
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8795
BASE = f"http://127.0.0.1:{PORT}"
MASTER = "selftest-master"
OID_A = "ou_selftest_aaaaaaaaaaaaaaaaaaaa1111"
OID_B = "ou_selftest_bbbbbbbbbbbbbbbbbbbb2222"
OID_C = "ou_selftest_cccccccccccccccccccc3333"
OID_D = "ou_selftest_dddddddddddddddddddd4444"
OID_E = "ou_selftest_eeeeeeeeeeeeeeeeeeee5555"

ok = fail = 0


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"  ({extra})" if extra else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}  {extra}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def req(path, method="GET", body=None, cookie="", follow=False):
    """返回 (状态码, 正文, Set-Cookie)。3xx 不自动跟随。"""
    data = None
    hdrs = {}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if cookie:
        hdrs["Cookie"] = cookie
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=hdrs)
    op = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with op.open(r, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers.get("Set-Cookie", "")


def jget(path, cookie="", body=None, method="GET"):
    s, b, ck = req(path, method=method, body=body, cookie=cookie)
    try:
        return s, json.loads(b), ck
    except Exception:
        return s, {"_raw": b[:200]}, ck


def wait_port(port, tries=40):
    for _ in range(tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="qcident_")
    # 名单：批量建号读的就是这个文件（生产里是 build_people.py 生成的 36 人快照）
    with open(os.path.join(tmp, "people.json"), "w", encoding="utf-8") as f:
        json.dump({OID_D: "名单甲", OID_E: "名单乙"}, f, ensure_ascii=False)
    with open(os.path.join(tmp, "records.json"), "w", encoding="utf-8") as f:
        json.dump([], f)

    os.environ["QC_DATA"] = tmp
    import perm                                        # noqa: E402  （必须在设完 QC_DATA 之后导入）
    perm.init()
    admin_pw = ""
    with open(os.path.join(tmp, "perm_admin.txt"), encoding="utf-8") as f:
        for ln in f:
            if "口令" in ln:
                admin_pw = ln.split("口令", 1)[1].strip()
                break

    env = dict(os.environ, QC_DATA=tmp, PORT=str(PORT), QC_TOKEN=MASTER,
               QC_IMG_DIR=r"E:\质检skill\证据截图_20261105_全量88")
    proc = subprocess.Popen([PY, "-u", "serve.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_port(PORT):
            print("服务没起来，放弃")
            return 1

        print("\n[1] 没有凭据的时候")
        s, _, _ = req("/panel")
        check("面板外壳能打开（内部自己认人）", s == 200, str(s))
        check("面板接口仍 401", req("/api/records")[0] == 401)
        check("入口页跳登录", req("/")[0] == 302)
        check("脏票据回落到干净面板", req("/panel?k=garbage.value")[0] == 302)

        print("\n[2] 不认识的飞书 id")
        s, d, _ = jget("/api/ident", body={"oid": OID_A}, method="POST")
        check("403 且标 pending", s == 403 and d.get("pending") is True, str(d.get("error"))[:30])
        check("回带自己的 id", d.get("oid") == OID_A)
        s, _, _ = req("/api/ident", "POST", {"oid": "short"})
        check("没给 id 是 400（不是 403）", s == 400, str(s))

        s, _, ck = req("/api/login", "POST", {"name": "admin", "password": admin_pw})
        cookie = ck.split(";")[0]
        check("管理员口令登录", bool(cookie))

        s, d, _ = jget("/api/admin/pending", cookie=cookie)
        ids = [x["oid"] for x in (d.get("items") or [])]
        check("待开通名单里有他", OID_A in ids, str(len(ids)))
        req("/api/ident", "POST", {"oid": OID_A})
        s, d, _ = jget("/api/admin/pending", cookie=cookie)
        hits = [x["hits"] for x in (d.get("items") or []) if x["oid"] == OID_A]
        check("再来一次次数累加", hits and hits[0] >= 2, str(hits))

        print("\n[3] 一键开通（只给审核）")
        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "approve", "oid": OID_A, "name": "测试甲", "perms": ["audit"]})
        check("开通成功且是新建", s == 200 and d.get("created") is True, str(d)[:50])
        s, d, _ = jget("/api/admin/pending", cookie=cookie)
        check("名单里消失", OID_A not in [x["oid"] for x in (d.get("items") or [])])

        s, d, _ = jget("/api/ident", body={"oid": OID_A}, method="POST")
        ticket = d.get("ticket") or ""
        mods = {m["code"]: m["ok"] for m in (d.get("modules") or [])}
        check("立刻能认出来", s == 200 and d.get("name") == "测试甲", str(s))
        check("拿到票据", bool(ticket), str(len(ticket)))
        check("只有审核", mods == {"audit": True}, str(mods))
        check("没开的模块不出现（免得被看到有哪些项目）",
              "qty" not in mods and "admin" not in mods, str(mods))

        print("\n[4] 票据的边界")
        check("票据进面板", req("/panel?k=" + ticket)[0] == 200)
        check("票据读样本接口", req("/api/records?k=" + ticket)[0] == 200)
        check("票据进不了工程量", req("/qty?k=" + ticket)[0] == 403)
        check("票据进不了管理页", req("/admin?k=" + ticket)[0] == 403)
        bad = ticket[:-2] + ("aa" if not ticket.endswith("aa") else "bb")
        check("篡改票据页面不给进", req("/panel?k=" + bad)[0] == 302)
        check("篡改票据接口 401", req("/api/records?k=" + bad)[0] == 401)

        print("\n[5] 票据换 cookie（PC 点一次链接）")
        s, _, ck = req("/panel?k=" + ticket)
        sess = ck.split(";")[0]
        check("页面 GET 落 cookie", "qc_sess" in ck, ck[:24])
        check("cookie 读接口", req("/api/records", cookie=sess)[0] == 200)
        check("cookie 进不了工程量", req("/qty", cookie=sess)[0] == 403)

        print("\n[6] 免密账号不能拿口令登")
        s, _, _ = req("/api/login", "POST", {"name": "测试甲", "password": ""})
        check("空口令 401", s == 401, str(s))
        s, _, _ = req("/api/login", "POST", {"name": "测试甲", "password": "123456"})
        check("随便一个口令也 401", s == 401, str(s))

        print("\n[7] 一个 id 只能绑一个人 / 同名补绑")
        req("/api/admin/user", "POST", {"action": "add", "name": "测试乙", "pw": "abc123",
                                        "perms": ["qty"]}, cookie=cookie)
        s, d, _ = jget("/api/admin/users", cookie=cookie)
        uid_a = [u["id"] for u in d["items"] if u["name"] == "测试甲"][0]
        uid_b = [u["id"] for u in d["items"] if u["name"] == "测试乙"][0]
        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "bind", "uid": uid_b, "oid": OID_A})
        check("同一个 id 绑第二个人被拒", s == 400, str(d.get("error"))[:30])

        req("/api/ident", "POST", {"oid": OID_B})
        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "approve", "oid": OID_B, "name": "测试乙", "perms": ["qty"]})
        check("同名账号补绑不新建", s == 200 and d.get("created") is False, str(d)[:40])
        s, d, _ = jget("/api/admin/users", cookie=cookie)
        row = [u for u in d["items"] if u["name"] == "测试乙"][0]
        check("id 已绑上", row.get("oid") == OID_B)
        check("补绑没把口令抹掉", row.get("has_pw") is True)

        print("\n[8] 解绑 / 停用 / 入口链接")
        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "unbind", "uid": uid_b})
        check("解绑成功", s == 200)
        s, d, _ = jget("/api/ident", body={"oid": OID_B}, method="POST")
        check("解绑后认不出（回到待开通）", s == 403 and d.get("pending") is True, str(s))

        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "link", "uid": uid_b, "proj": "qty"})
        check("没绑 id 也能发给口令人（有 qty 权限）", s == 200 and d.get("url", "").startswith("http"),
              str(d.get("url", ""))[:40])
        link = d.get("url", "").replace(BASE, "")
        s, _, ck = req(link)
        check("点链接即进工程量", s == 200 and "qc_sess" in ck, str(s))
        sess2 = ck.split(";")[0]
        check("之后开首页也能进", req("/", cookie=sess2)[0] == 200)

        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "link", "uid": uid_a, "proj": "qty"})
        check("没权限的人不给发工程量链接", s == 400, str(d.get("error"))[:24])

        s, d, _ = jget("/api/admin/user", cookie=cookie, method="POST",
                       body={"action": "update", "uid": uid_a, "active": 0})
        s, d, _ = jget("/api/ident", body={"oid": OID_A}, method="POST")
        check("停用后认不出", s == 403, str(d.get("error"))[:16])
        check("停用后旧会话立即失效", req("/api/records", cookie=sess)[0] == 403)
        req("/api/admin/user", "POST", {"action": "update", "uid": uid_a, "active": 1}, cookie=cookie)

        print("\n[9] 按名单批量建号")
        s, d, _ = jget("/api/admin/import_people", cookie=cookie, method="POST", body={})
        made = d.get("created") or []
        check("建了 2 个", s == 200 and len(made) == 2, str(made))
        s, d, _ = jget("/api/ident", body={"oid": OID_D}, method="POST")
        check("名单里的人直接能进（不用先来一次）", s == 200 and d.get("name") == "名单甲", str(s))
        mods = d.get("modules") or []
        check("默认只开审核", [m["code"] for m in mods] == ["audit"], str(mods))
        check("没权限的模块不出现在返回里（不是灰显）",
              all(m["code"] != "qty" for m in mods), str(mods))
        check("没权限就不签票据（防拿别人的入口卡）",
              all("k=" not in (m.get("href") or "") for m in mods if m["code"] != "audit"),
              str([m.get("href") for m in mods]))

        print("\n[10] 管理通道没被改坏")
        check("主口令进面板", req("/panel?k=" + MASTER)[0] == 200)
        check("主口令读管理接口", req("/api/admin/users?k=" + MASTER)[0] == 200)
        check("主口令仍能登录页", req("/login")[0] == 200)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    print(f"\n合计 {ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
