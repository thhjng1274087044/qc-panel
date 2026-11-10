# -*- coding: utf-8 -*-
"""截图证明：人工结论落在页面 + 本地库，刷新（新浏览器会话）后仍在。

只读操作，不改任何数据。
"""
import os

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8776"
TOKEN = os.environ.get("QC_TOKEN") or "qc-74f3a91c2b6e"
RID = "reczz28LZrntXm9r"
OUT = os.path.dirname(os.path.abspath(__file__))


def run():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        # 全新上下文 = 没有任何缓存/内存状态，等价于「关掉再打开」
        ctx = b.new_context(viewport={"width": 1360, "height": 900}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(f"{BASE}/?k={TOKEN}", wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        # 选中那条已判的记录
        row = pg.query_selector(f'tr[data-rid="{RID}"]')
        if not row:
            print("未找到目标行")
            b.close()
            return
        row.click()
        pg.wait_for_timeout(2200)
        pg.screenshot(path=os.path.join(OUT, "shot_persist_full.png"))

        # 面板区域
        frame = None
        for f in pg.frames:
            if "panel" in (f.url or ""):
                frame = f
                break
        if frame:
            sign = frame.query_selector("#sign")
            print("签署行文本:", sign.inner_text() if sign else "(未找到 #sign)")
            state = frame.query_selector("#state")
            if state:
                print("状态区文本:", state.inner_text()[:80])
        b.close()


if __name__ == "__main__":
    run()
