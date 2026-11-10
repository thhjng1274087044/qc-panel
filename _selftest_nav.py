# -*- coding: utf-8 -*-
"""「返回首页」自测：起隔离实例（临时数据目录 + 独立端口），走真实 HTTP。

验的是「每个项目页都能回首页、且回得去」这条线：
  首页 / 本身不画返回按钮（它自己就是首页），其余三页都得有；
  四页都要引 nav.js —— 带票据、跳顶层这两件事都写在那一个文件里，漏引就等于没修；
  nav.js 免鉴权（否则 iframe 里连脚本都拿不到）；
  票据能打开首页（否则从面板点返回会被踢去 /login）；
  同一张审核票据打不开工程量（所以首页上那张卡必须跳顶层，不能留在 iframe 里）；
  被拒页面的兜底文案是「返回首页」，不是旧的「回入口」。

用法：python _selftest_nav.py     （不碰生产库：QC_DATA 指向临时目录）
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
PORT = 8796
BASE = f"http://127.0.0.1:{PORT}"
MASTER = "selftest-nav-master"
OID_A = "ou_selftestnav_aaaaaaaaaaaaaaaaaa1111"
OID_M = "ou_selftestnav_mmmmmmmmmmmmmmmmmm2222"

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
    """返回 (状态码, 正文, Set-Cookie, Location)。3xx 不自动跟随。"""
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
            return (resp.status, resp.read().decode("utf-8", "replace"),
                    resp.headers.get("Set-Cookie", ""), resp.headers.get("Location", ""))
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode("utf-8", "replace"),
                e.headers.get("Set-Cookie", ""), e.headers.get("Location", ""))


def wait_port(port, tries=40):
    for _ in range(tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="qcnav_")
    with open(os.path.join(tmp, "people.json"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    with open(os.path.join(tmp, "records.json"), "w", encoding="utf-8") as f:
        json.dump([], f)

    os.environ["QC_DATA"] = tmp
    import perm                                        # noqa: E402  （必须在设完 QC_DATA 之后导入）
    perm.init()
    u = perm.add_user("导航甲", "", "user", ["audit", "qty"], OID_A)
    uid = u["id"] if isinstance(u, dict) else u
    ticket = perm.new_ticket(uid, "audit")
    mgr = perm.add_user("导航管", "", "admin", [], OID_M)

    env = dict(os.environ, QC_DATA=tmp, PORT=str(PORT), QC_TOKEN=MASTER,
               QC_IMG_DIR=r"E:\质检skill\证据截图_20261105_全量88")
    proc = subprocess.Popen([PY, "-u", "serve.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_port(PORT):
            print("服务没起来，放弃")
            return 1

        print("\n[1] nav.js 本身")
        s, js, _, _ = req("/static/nav.js")
        check("免鉴权能取到", s == 200, str(s))
        check("会带票据 k", "get('k')" in js or '"k"' in js or "'k'" in js)
        # 踩过的坑：加 target=_top 会把飞书界面整个顶掉，客户端另开全屏 webview。
        # 只认「真的设了 target」—— 注释里写着 target=_top 是在解释为什么不能这么干。
        check("不设 target（不顶掉顶层窗口）",
              "setAttribute('target'" not in js and ".target" not in js)
        check("首页地址是 /", "'/'" in js or '"/"' in js)

        print("\n[2] 六个页面的返回入口")
        # /watermark 现在点进来就 302 去原程序（远端可达时），
        # 要验它的页面结构得带 ?stay=1，否则拿到的是跳转不是页面。
        for path, need_btn in (("/", False), ("/qty", True), ("/shrink", True),
                               ("/watermark?stay=1", True), ("/admin", True), ("/panel", True)):
            sep = "&" if "?" in path else "?"
            s, h, _, _ = req(f"{path}{sep}k={MASTER}")
            check(f"{path} 能开", s == 200, str(s))
            if need_btn:
                check(f"{path} 引了 nav.js", "/static/nav.js" in h)
                check(f"{path} 有 data-home", "data-home" in h)
            else:
                check(f"{path} 首页不画返回按钮", "data-home" not in h)
                check(f"{path} 首页不引 nav.js（省一次隧道请求）", "/static/nav.js" not in h)

        # 水印的真入口：能连上就直接 302 去原程序，不再给一层中转页
        s, _, _, loc = req(f"/watermark?k={MASTER}")
        check("/watermark 直达原程序（302，不出中转页）",
              s == 302 and loc.startswith("http://"), f"{s} {loc}")

        print("\n[3] 面板里点返回首页 = 拿票据开首页")
        s, h, _, _ = req(f"/?k={ticket}")
        check("票据能打开首页", s == 200, str(s))
        s, d, _, _ = req(f"/api/me?k={ticket}")
        try:
            me = json.loads(d)
        except Exception:
            me = {}
        check("首页带票据能取到 /api/me", s == 200 and me.get("ok") is True, str(s))
        mods = me.get("modules") or []
        codes = [m["code"] for m in mods]
        check("只列他有权限的模块", codes == ["audit", "qty"], str(codes))
        # 首页那几张卡能不能在 iframe 里点动，全看链接自带不自带票据 ——
        # 只写 /qty 的话，iframe 里没 cookie，点进去就是 302 登录页
        for code in ("audit", "qty"):
            href = next((m["href"] for m in mods if m["code"] == code), "")
            check(f"首页的「{code}」链接自带票据", "k=" in href, href[:36])
            s2, h2, _, _ = req(href)
            check(f"拿「{code}」这条链接能直接进", s2 == 200, str(s2))
        s, d, _, _ = req("/api/me?k=" + perm.new_ticket(mgr, "admin"))
        try:
            adm = json.loads(d)
        except Exception:
            adm = {}
        mhref = next((m["href"] for m in (adm.get("modules") or []) if m["code"] == "admin"), "")
        check("管理员的「账号」链接自带票据", "k=" in mhref, mhref[:36])
        check("拿这条链接能直接进 /admin", req(mhref)[0] == 200)

        print("\n[4] 同一张票据不能挪去别处用")
        s, h, _, _ = req(f"/qty?k={ticket}")
        check("审核票据打不开工程量（所以卡片要跳顶层）", s == 403, str(s))
        check("拒绝页写「返回首页」", "返回首页" in h)
        check("拒绝页不再写旧文案", "回入口" not in h)

        print("\n[5] 没凭据时")
        s, _, _, loc = req("/qty")
        check("工程量跳登录", s == 302 and "/login" in loc, f"{s} {loc}")
        s, _, _, _ = req("/panel")
        check("面板外壳仍能开（内部认人）", s == 200, str(s))

        print("\n[6] 主口令通道")
        s, d, _, _ = req(f"/api/me?k={MASTER}")
        try:
            me = json.loads(d)
        except Exception:
            me = {}
        check("主口令拿得到全部模块", me.get("master") is True and len(me.get("modules") or []) >= 3,
              str(len(me.get("modules") or [])))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
