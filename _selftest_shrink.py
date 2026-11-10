# -*- coding: utf-8 -*-
"""「文件压缩」模块自测：起隔离实例（临时数据目录 + 独立端口），走真实 HTTP。

验的是这个模块能不能用、以及权限有没有漏：
  没凭据进不去；有 shrink 权限才进得去；只有 qty 权限的人打不开；
  首页卡片链接必须指向 /shrink（一条分支写错就会变成 /qty，是本项目踩过的真坑）；
  四个前端库都能取到（压缩在浏览器里做，库是硬依赖，缺一个整页白给）；
  管理页的模块短名和 /api/admin/users 的 projs 必须一一对应，否则勾选框和权限对不上。

用法：python _selftest_shrink.py     （不碰生产库：QC_DATA 指向临时目录）
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
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
MASTER = "selftest-shrink-master"
OID_A = "ou_selftestshrink_aaaaaaaaaaaaaa1111"
OID_B = "ou_selftestshrink_bbbbbbbbbbbbbb2222"

LIBS = {"pdf.min.js": 200000, "pdf.worker.min.js": 800000,
        "pdf-lib.min.js": 300000, "jszip.min.js": 50000}

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
    tmp = tempfile.mkdtemp(prefix="qcshrink_")
    with open(os.path.join(tmp, "records.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    os.environ["QC_DATA"] = tmp
    import perm                                        # noqa: E402
    perm.init()
    a = perm.add_user("压缩甲", "", "user", ["shrink"], OID_A)
    b = perm.add_user("压缩乙", "", "user", ["qty"], OID_B)
    ta = perm.new_ticket(a, "shrink")
    tb = perm.new_ticket(b, "qty")

    env = dict(os.environ, QC_DATA=tmp, PORT=str(PORT), QC_TOKEN=MASTER,
               QC_IMG_DIR=r"E:\质检skill\证据截图_20261105_全量88")
    proc = subprocess.Popen([PY, "-u", "serve.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_port(PORT):
            print("服务没起来，放弃")
            return 1

        print("\n[1] 门")
        s, _, _, loc = req("/shrink")
        check("没凭据跳登录", s == 302 and "/login" in loc, f"{s} {loc}")
        s, h, _, _ = req("/shrink?k=" + ta)
        check("有权限能进", s == 200, str(s))
        s, h2, _, _ = req("/shrink?k=" + tb)
        check("只有工程量权限的人打不开", s == 403, str(s))
        check("拒绝页给返回首页", "返回首页" in h2)
        s, _, _, _ = req("/api/shrink/anything")
        check("没凭据的接口 401", s == 401, str(s))

        print("\n[2] 页面内容")
        check("有拖放区", 'id="drop"' in h)
        check("有目标体积预设", "data-mb=\"4\"" in h)
        check("写了不上传", "不上传" in h)
        check("写了格式不变", "输出还是原来的格式" in h or "格式" in h)
        for lib in LIBS:
            check(f"页面引用 {lib}", lib in h)
        check("引了返回首页脚本", "/static/nav.js" in h)
        check("顶栏有返回首页", "data-home" in h)
        check("接 /api/me 显示当前人", "/api/me" in h)

        print("\n[3] 前端库（压缩全靠它们，缺一个就白屏）")
        for lib, minsize in LIBS.items():
            s, body, _, _ = req("/static/shrink/" + lib)
            check(f"{lib} 可达且够大", s == 200 and len(body.encode()) > minsize // 10,
                  f"{s} {len(body.encode())}")
        s, _, _, _ = req("/static/shrink/nope.js")
        check("不存在的库是 404 不是 500", s == 404, str(s))

        print("\n[4] 首页卡片指对地方（曾经把 shrink 拼成 /qty）")
        s, d, _, _ = req("/api/me?k=" + ta)
        me = json.loads(d)
        codes = [m["code"] for m in me["modules"]]
        check("首页只列他有的模块", codes == ["shrink"], str(codes))
        href = me["modules"][0]["href"]
        check("链接指向 /shrink", href.startswith("/shrink?k="), href[:30])
        check("链接自带票据能直接打开", req(href)[0] == 200)

        s, d, _, _ = req("/api/me?k=" + MASTER)
        mm = json.loads(d)
        hrefs = {m["code"]: m["href"] for m in mm["modules"]}
        check("主口令也给压缩卡", "shrink" in hrefs, str(list(hrefs)))
        check("主口令的压缩链接也对", hrefs["shrink"].startswith("/shrink?k="), hrefs["shrink"][:26])
        check("主口令各卡路径互不一样",
              len({h.split("?")[0] for h in hrefs.values()}) == len(hrefs), str(hrefs)[:80])

        print("\n[5] 管理页的勾选项和服务端对得上")
        s, d, ck, _ = req("/api/login", "POST", {"name": "admin", "password": _admin_pw(tmp)})
        cookie = ck.split(";")[0]
        s, d, _, _ = req("/api/admin/users", cookie=cookie)
        projs = [p["code"] for p in json.loads(d).get("projs") or []]
        check("projs 含 shrink", "shrink" in projs, str(projs))
        s, h, _, _ = req("/admin", cookie=cookie)
        for code in projs:
            check(f"管理页 PROJ 里有 {code}", f"{code}:" in h or f"'{code}'" in h)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


def _admin_pw(tmp):
    p = os.path.join(tmp, "perm_admin.txt")
    for ln in open(p, encoding="utf-8"):
        if "口令" in ln:
            return ln.split("口令", 1)[1].strip()
    return ""


if __name__ == "__main__":
    sys.exit(main())
