# -*- coding: utf-8 -*-
"""权限与入口自测：起一个隔离实例（临时数据目录 + 独立端口），走真实 HTTP。

覆盖 17 项：
  未登录拦截（页面 302 / 接口 401）、登录页可访问、错口令 401、对口令发会话、
  /api/me 身份、管理员进 /admin、新建只开审核的人、越权页 403、越权接口 403、
  票据能进审核面板、票据进不了工程量、补授权后就通、停用后旧会话失效、
  主口令仍可直连面板、篡改票据被拒、过期票据被拒、上传统计跑通。

用法：python _selftest_perm.py     （不碰生产库：QC_DATA 指向临时目录）
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
MASTER = "selftest-master"
DXF = r"E:\XDLFC\管线标注-样本\滨海雨水_原始.dxf"
XLS = r"E:\质检skill\_qc_sample\XDL26109948结算清单.xls"

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


def req(path, method="GET", body=None, cookie="", headers=None, raw=None, follow=False):
    """返回 (状态码, 响应头, 正文, 新 cookie)。3xx 不自动跟随。"""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if raw is not None:
        data = raw
    if cookie:
        hdrs["Cookie"] = cookie
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=hdrs)
    op = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with op.open(r, timeout=180) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace"), resp.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace"), e.headers.get("Set-Cookie", "")


def wait_port(port, tries=40):
    for _ in range(tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def multipart(fields_files):
    """极简 multipart：fields_files = [(name, filename, bytes)]。
    不用 requests 是为了少一个依赖；这里只校验服务端能收全字节。"""
    boundary = "----qcselftest" + str(int(time.time()))
    out = b""
    for name, fn, content in fields_files:
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def main():
    tmp = tempfile.mkdtemp(prefix="qcperm_")
    os.environ["QC_DATA"] = tmp
    import perm                                        # noqa: E402  （必须在设完 QC_DATA 之后导入）
    perm.init()
    with open(os.path.join(tmp, "perm_admin.txt"), encoding="utf-8") as f:
        admin_pw = ""
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

        print("\n[1] 未登录")
        check("根路径跳登录", req("/")[0] == 302)
        check("工程量页跳登录", req("/qty")[0] == 302)
        check("管理页跳登录", req("/admin")[0] == 302)
        check("接口 401", req("/api/me")[0] == 401)
        check("登录页 200", req("/login")[0] == 200)

        print("\n[2] 登录")
        s, _, b, _ = req("/api/login", "POST", {"name": "admin", "password": "错的"})
        check("错口令 401", s == 401, b[:40])
        s, h, b, ck = req("/api/login", "POST", {"name": "admin", "password": admin_pw})
        cookie = ck.split(";")[0]
        check("对口令 200", s == 200 and "qc_sess" in ck, ck[:30])
        s, _, b, _ = req("/api/me", cookie=cookie)
        me = json.loads(b)
        check("/api/me 认出人", me.get("name") == "admin" and me.get("role") == "admin")
        check("管理员进 /admin", req("/admin", cookie=cookie)[0] == 200)
        check("管理员进 /qty", req("/qty", cookie=cookie)[0] == 200)

        print("\n[3] 只开审核的人")
        s, _, b, _ = req("/api/admin/user", "POST",
                         {"action": "add", "name": "测试员甲", "pw": "abc123", "perms": ["audit"]},
                         cookie=cookie)
        check("建号成功", s == 200 and json.loads(b).get("ok"), b[:60])
        _, _, _, ck2 = req("/api/login", "POST", {"name": "测试员甲", "password": "abc123"})
        ck2 = ck2.split(";")[0]
        check("本人登录", bool(ck2))
        check("本人进 /panel", req("/panel", cookie=ck2)[0] == 200)
        check("本人进 /qty 被拒 403", req("/qty", cookie=ck2)[0] == 403)
        check("本人进 /admin 被拒 403", req("/admin", cookie=ck2)[0] == 403)
        check("本人调工程量接口被拒", req("/api/qty/jobs", cookie=ck2)[0] == 403)

        print("\n[4] 票据（面板在 iframe 里靠这个）")
        _, _, b, _ = req("/api/me", cookie=ck2)
        me = json.loads(b)
        href = [m["href"] for m in me["modules"] if m["code"] == "audit"][0]
        ticket = href.split("k=", 1)[1]
        check("审核票据进面板", req("/panel?k=" + ticket)[0] == 200)
        check("审核票据读样本接口", req("/api/records?k=" + ticket)[0] == 200)
        check("审核票据进不了工程量", req("/qty?k=" + ticket)[0] == 403)
        bad = ticket[:-2] + ("aa" if not ticket.endswith("aa") else "bb")
        # 页面路径上「票据不可信」= 当没登录处理（302 回登录），接口才是 401；两者都不许 200
        check("篡改票据被拒", req("/panel?k=" + bad)[0] in (302, 401), str(req("/panel?k=" + bad)[0]))
        # 过期票据：用同一个 secret 签一个 exp 已过去的
        expired = perm._pack({"uid": perm.get_user("测试员甲")["id"], "p": "audit",
                              "exp": int(time.time()) - 60, "n": "x"})
        check("过期票据被拒", req("/panel?k=" + expired)[0] in (302, 401),
              str(req("/panel?k=" + expired)[0]))
        check("过期票据调接口 401", req("/api/records?k=" + expired)[0] == 401)
        check("篡改票据调接口 401", req("/api/records?k=" + bad)[0] == 401)

        print("\n[5] 补授权 / 停用 / 主口令")
        uid = perm.get_user("测试员甲")["id"]
        req("/api/admin/user", "POST", {"action": "update", "uid": uid, "perms": ["audit", "qty"]},
            cookie=cookie)
        check("补授权后进工程量", req("/qty", cookie=ck2)[0] == 200)
        req("/api/admin/user", "POST", {"action": "update", "uid": uid, "active": 0}, cookie=cookie)
        check("停用后旧会话失效", req("/qty", cookie=ck2)[0] in (401, 403))
        check("停用后登录失败", req("/api/login", "POST", {"name": "测试员甲", "password": "abc123"})[0] == 401)
        check("主口令仍可直连面板", req("/panel?k=" + MASTER)[0] == 200)
        check("主口令可看管理接口", req("/api/admin/users?k=" + MASTER)[0] == 200)

        print("\n[6] 上传统计（真文件、真解析）")
        with open(DXF, "rb") as f:
            dxf_bytes = f.read()
        with open(XLS, "rb") as f:
            xls_bytes = f.read()
        body, hdrs = multipart([("dwg", os.path.basename(DXF), dxf_bytes),
                                ("list", os.path.basename(XLS), xls_bytes)])
        s, _, b, _ = req("/api/qty/run", "POST", raw=body, cookie=cookie, headers=hdrs)
        r = json.loads(b) if b.startswith("{") else {}
        res = r.get("result") or {}
        check("上传统计成功", s == 200 and r.get("ok"), b[:80])
        check("图层数 15", (res.get("totals") or {}).get("layers") == 15,
              str((res.get("totals") or {}).get("layers")))
        check("线长 2878.588", abs(((res.get("totals") or {}).get("length_m") or 0) - 2878.588) < 0.01,
              str((res.get("totals") or {}).get("length_m")))
        check("清单行被解析", (res.get("totals") or {}).get("list_rows") == 1)
        check("工程量按图层前缀分组", any(g["name"].startswith("雨水直埋") for g in res.get("groups") or []))

        print("\n[7] 自锁防护（别把最后一个管理员弄没了）")
        # 实测踩过：用票据把自己的角色改成 user，接口照批，之后 /admin 直接 403，
        # 只能去改 SQLite 才能救回来。所以这两条必须拦住。
        auid = perm.get_user("admin")["id"]
        s, _, b, _ = req("/api/admin/user", "POST",
                         {"action": "update", "uid": auid, "role": "user"}, cookie=cookie)
        check("不能把自己降级", s == 400, b[:70])
        s, _, b, _ = req("/api/admin/user", "POST",
                         {"action": "update", "uid": auid, "active": 0}, cookie=cookie)
        check("不能停用自己", s == 400, b[:70])
        s, _, b, _ = req("/api/admin/user", "POST",
                         {"action": "delete", "uid": auid}, cookie=cookie)
        check("不能删自己", s == 400, b[:70])
        check("改完仍是管理员", req("/admin", cookie=cookie)[0] == 200)

        print(f"\n合计 {ok} 通过 / {fail} 失败")
        return 0 if fail == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
