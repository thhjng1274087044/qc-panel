# -*- coding: utf-8 -*-
"""成果水印模块自测 —— 权限门、页面内容、可达性探测的真实行为。

为什么这个自测值得写（都是会静默出错的地方）：
  1) 任务码写错不会报错，只会让人点进去 403，或者干脆不该进的人进得去；
  2) 可达性探测如果走了代理，内网地址会被丢给 127.0.0.1:7011，直连必然失败，
     人看到「远端连不上」，而实际上水印服务是好的 —— 这条必须实测；
  3) https 判断只认 request.is_secure 时，挂在 ngrok 后面恒为 False，
     页面就会去嵌 http:// 的内网页，被浏览器按混合内容拦成白框，也不报错；
  4) 远端真挂了必须 distinguishable 地报出来（reachable=false + err），
     不能和「没权限」「配置读不到」混成一句话。

用法：python _selftest_watermark.py      （不碰生产库：QC_DATA 指向临时目录）
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
APP_PORT = 8799
WM_PORT = 8798
BASE = f"http://127.0.0.1:{APP_PORT}"
MASTER = "selftest-watermark-master"
OID_A = "ou_selftestwatermark_aaaaaaaaaaa1111"
OID_B = "ou_selftestwatermark_bbbbbbbbbbb2222"
FAKE_VER = "9.9.9"

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


def req(path, method="GET", body=None, cookie="", headers=None, follow=False):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if cookie:
        hdrs["Cookie"] = cookie
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=hdrs)
    # 直连：本机常年带 HTTP_PROXY=127.0.0.1:7011，不绕开连 127.0.0.1 都会失败
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     *([] if follow else [NoRedirect]))
    try:
        with op.open(r, timeout=60) as resp:
            return (resp.status, resp.read().decode("utf-8", "replace"),
                    resp.headers.get("Set-Cookie", ""), resp.headers.get("Location", ""))
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode("utf-8", "replace"),
                e.headers.get("Set-Cookie", ""), e.headers.get("Location", ""))


def wait_port(port, tries=50):
    for _ in range(tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


# ---------------------------------------------------------------- 假水印服务
class FakeWM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/") == "/openapi.json":
            body = json.dumps({"info": {"title": "假的", "version": FAKE_VER},
                               "paths": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html>fake watermark</html>")


def start_fake():
    srv = ThreadingHTTPServer(("127.0.0.1", WM_PORT), FakeWM)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv


def main():
    for p in (APP_PORT, WM_PORT):
        if not port_free(p):
            print(f"端口 {p} 被占用，先关掉再跑")
            return 1

    tmp = tempfile.mkdtemp(prefix="qcwmark_")
    with open(os.path.join(tmp, "records.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    os.environ["QC_DATA"] = tmp
    import perm                                        # noqa: E402
    perm.init()
    a = perm.add_user("水印甲", "", "user", ["watermark"], OID_A)
    b = perm.add_user("水印乙", "", "user", ["qty"], OID_B)
    ta = perm.new_ticket(a, "watermark")
    tb = perm.new_ticket(b, "qty")

    wm = start_fake()
    env = dict(os.environ, QC_DATA=tmp, PORT=str(APP_PORT), QC_TOKEN=MASTER,
               QC_IMG_DIR=tmp, QC_WM_URL=f"http://127.0.0.1:{WM_PORT}",
               QC_WM_TIMEOUT="2",
               # 故意塞一个坏代理：探测若不绕代理，连 127.0.0.1 也会失败
               HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
               http_proxy="http://127.0.0.1:1", https_proxy="http://127.0.0.1:1",
               NO_PROXY="")
    proc = subprocess.Popen([PY, "-u", "serve.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_port(APP_PORT):
            print("服务没起来，放弃")
            return 1

        print("\n[1] 门")
        s, _, _, loc = req("/watermark")
        check("没凭据跳登录", s == 302 and "/login" in loc, f"{s} {loc}")
        s, _, _, loc = req("/watermark?k=" + ta)
        check("有 watermark 权限 → 直接跳原程序，不出中转页",
              s == 302 and loc.rstrip("/") == f"http://127.0.0.1:{WM_PORT}", f"{s} {loc}")
        s, h, _, _ = req("/watermark?stay=1&k=" + ta)
        check("?stay=1 才停在本服务（排障用）", s == 200, str(s))
        s, h2, _, _ = req("/watermark?k=" + tb)
        check("只有工程量权限的人打不开", s == 403, str(s))
        check("拒绝页给返回首页", "返回首页" in h2)
        s, _, _, _ = req("/api/watermark/conf")
        check("没凭据的探测接口 401", s == 401, str(s))
        s, _, _, _ = req("/api/watermark/conf?k=" + tb)
        check("越权探测接口 403", s == 403, str(s))

        print("\n[2] 页面内容")
        check("有说明卡容器", 'id="card"' in h)
        check("引了返回首页脚本", "/static/nav.js" in h)
        check("顶栏有返回首页", "data-home" in h)
        check("接 /api/me 显示当前人", "/api/me" in h)
        check("走统一探测接口", "/api/watermark/conf" in h)
        check("有直开按钮", "打开原程序" in h)
        check("写明不复制、不中转", "不可外传的一环" in h)
        check("页面里没有硬编码远端 IP",
              "192.168" not in h, "远端地址只能来自服务端")

        print("\n[3] 可达性探测（远端正常）")
        s, d, _, _ = req("/api/watermark/conf?k=" + ta)
        c = json.loads(d)
        check("接口 200", s == 200, str(s))
        check("reachable=true", c.get("reachable") is True, str(c.get("reachable")))
        check("读到版本号", c.get("version") == FAKE_VER, str(c.get("version")))
        check("err 为空", c.get("err") == "", str(c.get("err")))
        check("给了地址", str(c.get("url", "")).endswith(f":{WM_PORT}/"), str(c.get("url")))
        check("探测耗时合理", isinstance(c.get("ms"), int) and 0 <= c["ms"] < 2000, str(c.get("ms")))
        check("代理没被误用（坏代理下仍连通）", c.get("reachable") is True)

        print("\n[4] 可达性探测（远端停掉）")
        wm.shutdown()
        wm.server_close()
        time.sleep(0.3)
        s, h4, _, loc4 = req("/watermark?k=" + ta)
        check("远端停掉时不跳转、停下来说明", s == 200 and "没有响应" in h4, f"{s} {loc4}")
        s, d, _, _ = req("/api/watermark/conf?k=" + ta)
        c = json.loads(d)
        check("接口仍 200（不是 500）", s == 200, str(s))
        check("reachable=false", c.get("reachable") is False, str(c.get("reachable")))
        check("说了错在哪（err 非空）", bool(c.get("err")), str(c.get("err")))
        check("版本号留空而不是编一个", c.get("version") == "", str(c.get("version")))

        print("\n[5] 远端恢复后能自己好（不是把 false 粘住）")
        wm = start_fake()
        time.sleep(0.3)
        s, d, _, _ = req("/api/watermark/conf?k=" + ta)
        c = json.loads(d)
        check("恢复后 reachable=true", c.get("reachable") is True, str(c.get("reachable")))

        print("\n[6] 用顶层跳转绕开混合内容（不再嵌 iframe）")
        check("页面里没有 iframe", "<iframe" not in h, "https 页嵌 http 必白框")
        s, _, _, loc = req("/watermark?k=" + ta)
        check("可达时 302 到远端原程序",
              s == 302 and loc.rstrip("/") == f"http://127.0.0.1:{WM_PORT}", f"{s} {loc}")
        s, home, _, _ = req("/?k=" + ta)
        check("首页水印卡用新标签打开（首页留着）",
              "m.code === 'watermark'" in home and "_blank" in home, str(s))

        print("\n[7] 首页卡片指对地方")
        s, d, _, _ = req("/api/me?k=" + ta)
        me = json.loads(d)
        codes = [m["code"] for m in me["modules"]]
        check("只列他有的模块", codes == ["watermark"], str(codes))
        href = me["modules"][0]["href"]
        check("链接指向 /watermark", href.startswith("/watermark?k="), href[:30])
        st, _, _, lo = req(href)
        check("链接自带票据能直接打开（302 到原程序即算通）",
              st == 302 and lo.startswith(f"http://127.0.0.1:{WM_PORT}"), f"{st} {lo}")
        s, d, _, _ = req("/api/me?k=" + MASTER)
        mm = json.loads(d)
        hrefs = {m["code"]: m["href"] for m in mm["modules"]}
        check("主口令也给水印卡", "watermark" in hrefs, str(list(hrefs)))
        check("主口令各卡路径互不一样",
              len({x.split("?")[0] for x in hrefs.values()}) == len(hrefs), str(hrefs)[:90])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        try:
            wm.shutdown()
            wm.server_close()
        except Exception:
            pass

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
