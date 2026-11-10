# -*- coding: utf-8 -*-
"""ngrok 警告页在 iframe 内「点 Visit Site 能否进去」——决定这套能不能给飞书用。

只回答一个问题：如果人工在 iframe 里点过那个按钮，面板能不能加载出来，
以及加载出来后 cookie 是否留在飞书那条链路上（换个页面还认不认）。
"""
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
TOKEN = "qc-74f3a91c2b6e"

HOST = """<!doctype html><meta charset="utf-8">
<body style="margin:0;font:14px system-ui">
<div style="height:56px;background:#1f2329;color:#fff;line-height:56px;padding:0 14px">假飞书宿主</div>
<iframe id="f" src="%s/panel?k=%s" style="width:420px;height:700px;border:1px solid #ccc"></iframe>
</body>""" % (BASE, TOKEN)


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(viewport={"width": 1200, "height": 820})
        pg = ctx.new_page()
        pg.set_content(HOST)
        pg.wait_for_timeout(6000)
        fl = pg.frames[1]
        print("iframe 首屏:", fl.inner_text("body")[:110].replace("\n", " "))

        # 警告页上的可点元素：新版是 button，旧版是 a
        clicked = False
        for sel in ("button:has-text('Visit Site')", "a:has-text('Visit Site')",
                    "text=Visit Site"):
            try:
                loc = fl.locator(sel)
                if loc.count():
                    loc.first.click()
                    clicked = True
                    print("点了:", sel)
                    break
            except Exception as e:
                print("  点不动", sel, str(e)[:60])
        if not clicked:
            print("找不到 Visit Site 按钮，页面上有这些可点文本：")
            print("  ", [s.strip() for s in fl.inner_text("body").split("\n") if s.strip()][:12])
            b.close()
            return

        pg.wait_for_timeout(6000)
        frames = pg.frames
        print("点完后 iframe 数:", len(frames))
        for i, f in enumerate(frames[1:], 1):
            try:
                t = f.inner_text("body")[:140].replace("\n", " ")
                print(f"   frame{i}: {t!r}")
            except Exception as e:
                print(f"   frame{i}: 读不到 {str(e)[:40]}")
        pg.screenshot(path="probe_ngrok_after_click.png")

        # 换一个页面，看 cookie 有没有留在同一浏览器里
        pg2 = ctx.new_page()
        pg2.set_content(HOST.replace("/panel?k=", "/qty?k="))
        pg2.wait_for_timeout(6000)
        if len(pg2.frames) > 1:
            t2 = pg2.frames[1].inner_text("body")[:120].replace("\n", " ")
            print("第二个 iframe（新页面）:", repr(t2))
            print("-> 不再出警告页:", "about to visit" not in t2.lower())
        b.close()


if __name__ == "__main__":
    main()
