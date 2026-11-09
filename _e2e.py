# -*- coding: utf-8 -*-
"""端到端验证：假飞书宿主 + 本地 SDK + 面板。
重点验证：点左表某行 → 右侧真的跟着换（不是靠模拟数据，靠 getSelection 轮询/事件）。
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8776"


def main():
    with sync_playwright() as sp:
        b = sp.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs, logs = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("console", lambda m: logs.append(m.type + ": " + m.text[:160]))

        pg.goto(BASE + "/", wait_until="domcontentloaded")
        pg.wait_for_timeout(4500)          # 等握手 + 首屏

        fl = pg.frame(url=lambda u: "/panel" in u)
        print("iframe 找到:", bool(fl))

        # 1. 面板顶栏 / 绑定条
        print("顶栏状态:", fl.inner_text("#hdSt").strip())
        print("绑定条  :", fl.inner_text("#bind").replace("\n", " ").strip()[:70])
        print("任务号  :", fl.inner_text("#tn").strip())
        print("徽章    :", fl.inner_text("#badgeTxt").strip())
        print("结论摘要:", fl.inner_text("#reason")[:60].replace("\n", " "))

        # 2. 左表第 3 行 vs 右侧
        left = pg.eval_on_selector_all("#grid tbody tr[data-rid]", "els=>els.slice(0,5).map(e=>e.dataset.rid+' | '+e.children[2].innerText)")
        print("左表前5行:", [x.split(" | ")[0] for x in left])

        pg.click("#grid tbody tr:nth-child(3)")
        pg.wait_for_timeout(2200)
        rid3 = left[2].split(" | ")[0]
        print("\n点第3行 ->", rid3)
        print("  绑定条:", fl.inner_text("#bind").replace("\n", " ").strip()[:70])
        print("  任务号:", fl.inner_text("#tn").strip())
        print("  一致?", rid3 in fl.inner_text("#bindRid"))

        # 3. 再切一行
        pg.click("#grid tbody tr:nth-child(5)")
        pg.wait_for_timeout(2200)
        rid5 = left[4].split(" | ")[0]
        print("\n点第5行 ->", rid5)
        print("  任务号:", fl.inner_text("#tn").strip())
        print("  一致?", rid5 in fl.inner_text("#bindRid"))

        # 4. 证据图是否真的加载
        imgok = fl.evaluate("()=>{const i=document.getElementById('shotImg');return {src:i.src.split('/').pop(),w:i.naturalWidth,h:i.naturalHeight}}")
        print("\n证据图:", imgok)

        # 5. 核对浮层
        fl.click("#auditBtn")
        pg.wait_for_timeout(400)
        print("核对浮层:", fl.inner_text("#auditRows").replace("\n", " / ")[:200])
        fl.click("#auditClose")
        pg.wait_for_timeout(200)

        # 6. 确认通过 -> 后端落库
        fl.click("#pass")
        pg.wait_for_timeout(2600)
        print("\n确认后状态:", fl.inner_text("#hdSt").strip(), "|", fl.inner_text("#sign").strip()[:60])

        # 7. 四 Tab 溢出检查
        for t in ["det", "rule", "exe", "log"]:
            fl.click(f'#tabs button[data-t="{t}"]')
            pg.wait_for_timeout(150)
            ov = fl.evaluate("""()=>{const b=document.getElementById('body');
                return {sh:b.scrollHeight, ch:b.clientHeight, over:b.scrollWidth>b.clientWidth+2}}""")
            print(f"  Tab {t}: h={ov['sh']} client={ov['ch']} 横溢={ov['over']}")

        pg.screenshot(path="shot_host.png")

        # 8. 窄屏 400px 左右
        pg.set_viewport_size({"width": 900, "height": 860})
        pg.wait_for_timeout(500)
        fl2 = pg.frame(url=lambda u: "/panel" in u)
        ovw = fl2.evaluate("()=>[...document.querySelectorAll('body *')].filter(e=>e.scrollWidth>e.clientWidth+3 && getComputedStyle(e).overflowX==='visible').map(e=>e.className||e.tagName).slice(0,6)")
        print("\n窄屏横向溢出元素:", ovw)
        pg.screenshot(path="shot_host_narrow.png")

        print("\n页面错误:", errs[:3] if errs else "无")
        bad = [l for l in logs if l.startswith("error")]
        print("console error:", bad[:3] if bad else "无")
        b.close()


if __name__ == "__main__":
    sys.exit(main())
