# -*- coding: utf-8 -*-
"""实测：在 **HTTPS 页面** 点水印卡，浏览器是否允许顶层跳到内网 http。

为什么要专门测这一条：
  之前写成「https 页嵌 http iframe」，浏览器按混合内容直接拦，是一片白框。
  现在改成 302 顶层跳转。混合内容策略只管子资源，顶层导航应当放行 ——
  但这是整件事的关键假设，不能用「应该」代替实测。

跑法：python _wm_click_check.py             （默认走公网 https 地址）
      python _wm_click_check.py --local     （走本机 http，作对照）
"""
import sys
from playwright.sync_api import sync_playwright

K = "qc-74f3a91c2b6e"
PUB = "https://spinous-practicedly-richie.ngrok-free.dev"
LOCAL = "http://127.0.0.1:8776"
use_local = "--local" in sys.argv
BASE = LOCAL if use_local else PUB


def main():
    ok = fail = 0

    def chk(label, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {label}" + (f"  ({extra})" if extra else ""))
        else:
            fail += 1
            print(f"  FAIL  {label}  {extra}")

    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        ctx = b.new_context(
            extra_http_headers={"ngrok-skip-browser-warning": "true"},
            ignore_https_errors=True,
        )
        pg = ctx.new_page()
        pg.goto(f"{BASE}/?k={K}", wait_until="domcontentloaded", timeout=60000)
        print("入口页:", pg.url)
        print("协议  :", pg.evaluate("location.protocol"))
        chk("入口在 HTTPS 上（混合内容的先决条件）",
            pg.evaluate("location.protocol") == ("http:" if use_local else "https:"),
            pg.evaluate("location.protocol"))

        # 首页卡片是 /api/me 异步渲染的，等它出来再点（否则拿到空 NodeList）
        pg.wait_for_selector("a.card", timeout=30000)
        cards = pg.eval_on_selector_all("a.card", "els => els.map(e => e.textContent.trim().slice(0,20))")
        print("卡片:", cards)
        chk("首页有水印卡", any("水印" in c for c in cards), str(cards))

        # 点水印卡：应新开标签，且落到内网原程序
        target = pg.locator("a.card", has_text="成果水印").first
        chk("水印卡带 target=_blank", target.get_attribute("target") == "_blank",
            str(target.get_attribute("target")))

        with ctx.expect_page(timeout=30000) as newp:
            target.click()
        np = newp.value
        np.wait_for_url("http://192.168.0.101:8765/**", timeout=30000)
        print("新标签:", np.url)
        chk("新标签落到水印原程序", np.url.startswith("http://192.168.0.101:8765"), np.url)

        body = np.inner_text("body")[:80].replace("\n", " ")
        print("页面文字:", body)
        # 目标页自己会把内网地址印在页面上，所以判据看标题文案而不是「有没有 IP」
        chk("确实是水印程序，不是错误页", "管控工具" in body, body[:50])

        # 对照：直接开 /watermark 也应 302 过去（不再有中转页）
        pg2 = ctx.new_page()
        pg2.goto(f"{BASE}/watermark?k={K}", wait_until="domcontentloaded", timeout=60000)
        print("直接开 /watermark ->", pg2.url)
        chk("直接开 /watermark 也直达原程序",
            pg2.url.startswith("http://192.168.0.101:8765"), pg2.url)
        chk("中间没有停在本服务的中转页",
              (pg2.title() or "").strip() != "成果水印", repr(pg2.title()))

        b.close()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
