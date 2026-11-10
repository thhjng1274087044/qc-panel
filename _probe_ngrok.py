# -*- coding: utf-8 -*-
"""ngrok 免费版警告页在 iframe 里到底能不能用 —— 真浏览器取证。

结论只需要回答三件事：
1. 顶层直接打开 ngrok 地址，会不会出现 ERR_NGROK_6024 警告页；
2. 在 iframe 里打开（飞书就是这么嵌的），警告页是否同样出现；
3. 点过 "Visit Site" 之后，同浏览器再进 iframe 是否直接放行（cookie 有没有被第三方拦）。

用法：python _probe_ngrok.py https://xxx.ngrok-free.dev
"""
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
TOKEN = "qc-74f3a91c2b6e"

HOST = """<!doctype html><meta charset="utf-8">
<title>假飞书宿主</title>
<body style="margin:0;font:14px system-ui">
<div style="height:56px;background:#1f2329;color:#fff;line-height:56px;padding:0 14px">
  假飞书宿主（模拟侧栏 iframe）
</div>
<iframe src="%s/panel?k=%s" style="width:420px;height:700px;border:1px solid #ccc"></iframe>
</body>""" % (BASE, TOKEN)


def main():
    if not BASE:
        print("请给 ngrok 地址")
        return 1
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1200, "height": 820})

        print("[1] 顶层直接打开 /health")
        pg = ctx.new_page()
        pg.goto(BASE + "/health", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(1500)
        t = pg.title()
        body = pg.inner_text("body")[:160].replace("\n", " ")
        print(f"    title={t!r}")
        print(f"    body={body!r}")
        warn_top = "ngrok" in t.lower() or "ERR_NGROK" in pg.content()
        print(f"    -> 顶层是警告页: {warn_top}")
        pg.screenshot(path="probe_ngrok_top.png")
        pg.close()

        print("[2] iframe 里打开 /panel（同一浏览器，未点过 Visit Site）")
        pg2 = ctx.new_page()
        pg2.set_content(HOST)
        pg2.wait_for_timeout(6000)
        fl = pg2.frames[1] if len(pg2.frames) > 1 else None
        if fl is None:
            print("    iframe 没加载出来")
        else:
            txt = fl.inner_text("body")[:200].replace("\n", " ")
            print(f"    iframe 内容={txt!r}")
            warn_if = "ngrok" in txt.lower() or "Visit Site" in fl.content()
            print(f"    -> iframe 内是警告页: {warn_if}")
        pg2.screenshot(path="probe_ngrok_iframe.png")

        print("[3] 在顶层点 Visit Site（若存在）后，回 iframe 再看")
        try:
            pg3 = ctx.new_page()
            pg3.goto(BASE + "/health", wait_until="domcontentloaded", timeout=45000)
            pg3.wait_for_timeout(800)
            btn = pg3.get_by_role("button", name="Visit Site")
            if btn.count() == 0:
                btn = pg3.get_by_text("Visit Site")
            if btn.count():
                btn.first.click()
                pg3.wait_for_timeout(3000)
                print(f"    点过了，落点={pg3.url[:60]}")
            else:
                print("    顶层没有 Visit Site 按钮（可能已是 200 正常页）")
            pg3.close()
        except Exception as e:
            print("    点按钮失败:", str(e)[:80])

        pg4 = ctx.new_page()
        pg4.set_content(HOST)
        pg4.wait_for_timeout(6000)
        fl4 = pg4.frames[1] if len(pg4.frames) > 1 else None
        if fl4 is not None:
            txt4 = fl4.inner_text("body")[:160].replace("\n", " ")
            print(f"    iframe 内容={txt4!r}")
            ok_if = "测绘" in txt4 or "返回首页" in txt4 or "选择" in txt4
            print(f"    -> 点过之后 iframe 能进面板: {ok_if}")
        pg4.screenshot(path="probe_ngrok_iframe_after.png")

        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
