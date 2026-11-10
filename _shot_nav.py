# -*- coding: utf-8 -*-
"""「返回首页」视觉自检：真浏览器（msedge）截图 + DOM 断言。

为什么必须截图：链接在不在 HTML 里（curl 就能验）和「点得到、不挤压、不换行」是两件事。
面板最窄会被塞进飞书侧栏，340px 下顶栏挤爆过一次，所以三档宽度都看。

用法：python _shot_nav.py     产物：shot_nav_*.png
"""
import os

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8776"


def _master_token():
    """主口令不写死在脚本里（会跟着仓库走）；优先环境变量，其次 .env.local。"""
    v = os.environ.get("QC_TOKEN")
    if v:
        return v
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
    if os.path.isfile(p):
        for ln in open(p, encoding="utf-8"):
            if ln.startswith("QC_TOKEN="):
                return ln.split("=", 1)[1].strip()
    return ""


TOKEN = _master_token()

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    # 主口令通道不种 cookie（只放行），要拿会话得走口令登录。
    # 口令优先用环境变量，没有就读部署时生成的那份 —— 不硬编码在脚本里。
    sess = requests.Session()
    pw = os.environ.get("QC_ADMIN_PW") or ""
    if not pw:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "perm_admin.txt")
        if os.path.isfile(p):
            for ln in open(p, encoding="utf-8"):
                if "口令" in ln:
                    pw = ln.split("口令", 1)[1].strip()
                    break
    r = sess.post(BASE + "/api/login", json={"name": "admin", "password": pw}, timeout=30)
    ck = sess.cookies.get("qc_sess")
    if not ck:
        print("拿不到会话 cookie：", r.status_code, r.text[:120])
        return 1

    with sync_playwright() as sp:
        b = sp.chromium.launch(channel="msedge")
        ctx = b.new_context(viewport={"width": 1240, "height": 860})
        ctx.add_cookies([{"name": "qc_sess", "value": ck, "domain": "127.0.0.1", "path": "/"}])
        errs = []

        def shot(name, url, w, h, sel=""):
            pg = ctx.new_page()
            pg.set_viewport_size({"width": w, "height": h})
            pg.on("pageerror", lambda e: errs.append(f"{name}: {e}"[:160]))
            pg.goto(url, wait_until="domcontentloaded")
            pg.wait_for_timeout(2200)
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"shot_nav_{name}.png")
            pg.screenshot(path=path)
            info = {"path": path}
            if sel:
                info = pg.evaluate("""(sel) => {
                    const a = document.querySelector(sel);
                    const hd = document.querySelector('.hd');
                    return {
                      href: a ? a.getAttribute('href') : null,
                      text: a ? a.innerText.trim() : null,
                      visible: a ? (a.getBoundingClientRect().width > 0 &&
                                    a.getBoundingClientRect().height > 0) : false,
                      hasHome: !!document.querySelector('[data-home]'),
                      cards: document.querySelectorAll('.cards .card').length,
                      hdH: hd ? hd.offsetHeight : 0,
                      overflow: document.documentElement.scrollWidth - window.innerWidth
                    };
                }""", sel)
                info["path"] = path
            pg.close()
            return info

        print("\n[1] 首页（各项目）")
        e = shot("entry", BASE + "/", 1240, 860, ".cards .card")
        check("首页列出项目卡片", e.get("cards", 0) >= 2, f"{e.get('cards')} 张")
        check("首页卡片可见", bool(e.get("visible")), str(e.get("text")))
        check("首页不画返回按钮（它自己就是首页）", not e.get("hasHome"))

        print("\n[2] 工程量页")
        q = shot("qty", BASE + "/qty", 1240, 560, "[data-home]")
        check("返回首页可见", bool(q.get("visible")), str(q.get("text")))
        check("指向 /", q.get("href") == "/", str(q.get("href")))
        check("顶栏没被撑高", q.get("hdH", 0) <= 52, str(q.get("hdH")))
        check("没有横向溢出", q.get("overflow", 0) <= 0, str(q.get("overflow")))

        print("\n[3] 文件压缩页")
        sh = shot("shrink", BASE + "/shrink", 1240, 620, "[data-home]")
        check("返回首页可见", bool(sh.get("visible")), str(sh.get("text")))
        check("指向 /", sh.get("href") == "/", str(sh.get("href")))
        check("顶栏没被撑高", sh.get("hdH", 0) <= 52, str(sh.get("hdH")))
        check("没有横向溢出", sh.get("overflow", 0) <= 0, str(sh.get("overflow")))

        print("\n[4] 管理页")
        a = shot("admin", BASE + "/admin", 1240, 700, "[data-home]")
        check("返回首页可见", bool(a.get("visible")), str(a.get("text")))
        check("指向 /", a.get("href") == "/", str(a.get("href")))
        check("顶栏没被撑高", a.get("hdH", 0) <= 52, str(a.get("hdH")))

        print("\n[4] 审核面板（飞书侧栏：420 与 340 两档）")
        for w, tag in ((420, "panel420"), (340, "panel340")):
            p = shot(tag, BASE + "/panel?k=" + TOKEN, w, 760, "[data-home]")
            check(f"{w}px 返回首页可见", bool(p.get("visible")), str(p.get("text")))
            check(f"{w}px 顶栏不换行（≤40）", p.get("hdH", 0) <= 40, str(p.get("hdH")))
            check(f"{w}px 没有横向溢出", p.get("overflow", 0) <= 0, str(p.get("overflow")))
            check(f"{w}px 链接带票据", "?k=" in (p.get("href") or ""), str(p.get("href"))[:40])

        if errs:
            print("\n  页面 JS 报错：")
            for e_ in errs:
                print("   ", e_)
        check("没有 JS 报错", not errs, str(len(errs)))

        print("\n[5] 假飞书宿主里真点一次（必须留在 iframe 里，不许顶掉飞书界面）")
        pg = ctx.new_page()
        pg.set_viewport_size({"width": 1200, "height": 820})
        pg.goto(BASE + "/host?k=" + TOKEN, wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        before = pg.url
        fl = pg.frame(url=lambda u: "/panel" in u)
        if not fl:
            check("面板在 iframe 里加载出来了", False)
        else:
            check("面板在 iframe 里加载出来了", True)
            fl.click("[data-home]")
            for _ in range(40):                 # 等 iframe 自己换页
                if "/panel" not in fl.url:
                    break
                pg.wait_for_timeout(500)
            pg.wait_for_timeout(1200)
            # 这条最要紧：target=_top 会把顶层窗口顶掉，飞书客户端当成外部链接另开全屏
            check("顶层没被顶掉（仍停在宿主页）", pg.url == before, pg.url[:52])
            check("iframe 内换成了首页", "/panel" not in fl.url, fl.url[:52])
            n = fl.locator(".cards .card").count()
            check("iframe 内的首页列出项目", n >= 2, f"{n} 张卡")

            print("\n[6] 接着在 iframe 里点进工程量、再返回来（整条链不出框架）")
            if n:
                fl.click("a.card[href*='/qty']")
                for _ in range(40):
                    if "/qty" in fl.url:
                        break
                    pg.wait_for_timeout(500)
                check("iframe 内进了工程量页", "/qty" in fl.url, fl.url[:56])
                check("顶层仍停在宿主页", pg.url == before, pg.url[:40])
                pg.wait_for_timeout(1500)
                check("工程量页也有返回首页", fl.locator("[data-home]").count() == 1)
                fl.click("[data-home]")
                for _ in range(40):
                    if "/qty" not in fl.url:
                        break
                    pg.wait_for_timeout(500)
                check("点了返回又回到 iframe 内首页",
                      "/qty" not in fl.url and "/panel" not in fl.url, fl.url[:56])
        pg.close()

        print("\n[7] 首页在飞书侧栏宽度（420）下也别挤爆")
        e2 = shot("entry420", BASE + "/?k=" + TOKEN, 420, 760, ".cards .card")
        check("420px 首页卡片可见", bool(e2.get("visible")), str(e2.get("text", ""))[:20])
        check("420px 首页无横向溢出", e2.get("overflow", 0) <= 0, str(e2.get("overflow")))

        b.close()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
