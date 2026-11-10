"""模拟飞书 iframe：把请求里的 Cookie 全部剥掉，看各页面还能不能认到人。

为什么这么测：飞书把面板放在 iframe 里，浏览器拦掉第三方 cookie，
所以「有 cookie 才认人」这条路径在飞书里是不成立的。桌面浏览器直接开页面必然通过，
这个脚本才是真正的判据。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perm
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8776"
UID = "u_42133c"                      # 邵宇航（真实账号，已绑定飞书 id）
ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {extra}")


def strip_cookie(route):
    h = {k: v for k, v in route.request.headers.items() if k.lower() != "cookie"}
    route.continue_(headers=h)


def main():
    t_admin = perm.new_ticket(UID, "admin")
    t_audit = perm.new_ticket(UID, "audit")
    t_qty = perm.new_ticket(UID, "qty")
    t_shrink = perm.new_ticket(UID, "shrink")

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1240, "height": 900})
        ctx.route("**/*", strip_cookie)          # 关键：所有请求都不带 cookie

        # ---- 管理页 ----
        print("\n[1] 管理页 /admin（无 cookie，只靠 ?k=）")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/admin?k={t_admin}", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(3000)
        body = pg.inner_text("body")
        check("没有「未登录或登录已过期」", "未登录或登录已过期" not in body)
        rows = pg.locator("table tbody tr").count()
        check("用户表格有行", rows >= 2, f"{rows} 行")
        who = pg.inner_text("#who") if pg.locator("#who").count() else ""
        check("右上角认出了姓名", "邵宇航" in who, who)
        check("没有 JS 报错", not errs, str(errs[:2]))
        pg.screenshot(path="shot_iframe_admin.png")
        pg.close()

        # ---- 工程量页 ----
        print("\n[2] 工程量页 /qty（无 cookie，只靠 ?k=）")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/qty?k={t_qty}", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(2500)
        body = pg.inner_text("body")
        check("没有「未登录或登录已过期」", "未登录或登录已过期" not in body)
        check("没有跳去登录页", "/login" not in pg.url, pg.url[:60])
        check("页面主体在", pg.locator(".wrap, main, .hd").count() > 0)
        check("没有 JS 报错", not errs, str(errs[:2]))
        pg.screenshot(path="shot_iframe_qty.png")
        pg.close()

        # ---- 压缩页 ----
        print("\n[3] 压缩页 /shrink（无 cookie，只靠 ?k=）")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/shrink?k={t_shrink}", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(2500)
        body = pg.inner_text("body")
        check("没有「未登录或登录已过期」", "未登录或登录已过期" not in body)
        check("没有跳去登录页", "/login" not in pg.url, pg.url[:60])
        check("没有 JS 报错", not errs, str(errs[:2]))
        pg.screenshot(path="shot_iframe_shrink.png")
        pg.close()

        # ---- 首页 ----
        print("\n[4] 首页 /（无 cookie，只靠 ?k=）")
        pg = ctx.new_page()
        pg.goto(f"{BASE}/?k={t_audit}", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(2500)
        n = pg.locator(".cards .card").count()
        check("首页列出卡片", n >= 2, f"{n} 张")
        check("没有跳去登录页", "/login" not in pg.url, pg.url[:60])
        pg.close()

        # ---- 审核面板 ----
        print("\n[5] 审核面板 /panel（无 cookie，只靠 ?k=）")
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/panel?k={t_audit}", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(4000)
        body = pg.inner_text("body")
        check("没有「未登录或登录已过期」", "未登录或登录已过期" not in body)
        check("没有停在上传口令页", "这个页面不用登录" not in body)
        check("没有 JS 报错", not errs, str(errs[:2]))
        pg.screenshot(path="shot_iframe_panel.png")
        pg.close()

        # ---- 兜底：iframe 里裸开 /admin（没票据）应该回面板认人，而不是停在一个错误页 ----
        print("\n[6] iframe 里裸开 /admin（无票据）→ 应该回面板认人")
        pg = ctx.new_page()
        pg.set_content('<iframe id="f" src="%s/admin" style="width:420px;height:800px"></iframe>' % BASE)
        pg.wait_for_timeout(3500)
        framed = [f.url for f in pg.frames if f != pg.main_frame]
        hit = [u for u in framed if "/panel" in u]
        check("被送回 /panel 认人", bool(hit), str([u[-40:] for u in framed]))
        check("没有停在登录页", not any("/login" in u for u in framed), str([u[-30:] for u in framed]))
        pg.close()

        # ---- 静态检查：顶层跳转的下载链接必须自己带票据 ----
        print("\n[7] 不走 fetch 的下载链接")
        here = os.path.dirname(os.path.abspath(__file__))
        qty = open(os.path.join(here, "templates", "qty.html"), encoding="utf-8").read()
        check("xlsx 导出走 qcWithK", "qcWithK('/api/qty/job/'" in qty or 'qcWithK("/api/qty/job/' in qty)

        ctx.close()
        b.close()

    print(f"\n合计 {ok} 通过 / {fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
