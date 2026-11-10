# -*- coding: utf-8 -*-
"""核对台视觉自检：真浏览器（msedge）三档宽度截图 + DOM 断言。

为什么必须截图：接口返回对（curl 能验）和「页面真的把四数、三键、告警显示出来」是两件事。
上次自己生成的 HTML 只做语法检查，结果图形只占画布一角 —— 渲染层缺陷只能看。

用法：python _shot_review.py      产物：shots/review_*.png
"""
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("QC_SHOT_BASE") or "http://127.0.0.1:8777"
OUT = os.path.join(HERE, "shots")
os.makedirs(OUT, exist_ok=True)


def ticket():
    p = "/tmp/review_ticket.txt"
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    sys.path.insert(0, HERE)
    import perm
    return perm.new_ticket("u_42133c", "audit")


def main():
    tk = ticket()
    url = f"{BASE}/review?k={tk}"
    widths = [(1280, 900, "wide"), (820, 900, "mid"), (400, 880, "narrow")]
    fails = []
    with sync_playwright() as p:
        br = p.chromium.launch(channel="msedge", headless=True)
        for w, h, tag in widths:
            pg = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" else None)
            pg.goto(url, wait_until="networkidle")
            try:
                pg.wait_for_selector(".m", timeout=8000)
                pg.wait_for_selector(".row", timeout=8000)
            except Exception as e:
                fails.append(f"{tag}: 列表没渲染出来 {e}")
            pg.wait_for_timeout(600)
            png = os.path.join(OUT, f"review_{tag}.png")
            pg.screenshot(path=png, full_page=False)

            # DOM 断言：四数 + 三键 + 汇总不撒谎
            info = pg.evaluate("""() => {
              const g = s => (document.querySelector(s)||{}).textContent||'';
              return {
                total: g('#sum b.n'), p: g('#sP'), f: g('#sF'), h: g('#sH'), d: g('#sD'),
                mats: document.querySelectorAll('.m').length,
                rows: document.querySelectorAll('.row').length,
                warn: document.querySelectorAll('.wbox,.strip').length,
                btnDisabled: ['#bAgree','#bDispute','#bHold'].map(s=>document.querySelector(s).disabled),
                sumText: (document.querySelector('#sum')||{}).innerText||''
              };
            }""")
            print(f"[{tag}] {w}x{h} 材料={info['mats']} 行={info['rows']} 告警={info['warn']} "
                  f"总={info['total']} 过={info['p']} 不过={info['f']} 判不了={info['h']} 已表态={info['d']}")
            if errs:
                fails.append(f"{tag}: JS 报错 {errs[:3]}")
            if info["rows"] == 0:
                fails.append(f"{tag}: 没有检查项行")
            if "判不了" not in info["sumText"]:
                fails.append(f"{tag}: 汇总里没有「判不了」这一项")
            if all(info["btnDisabled"]):
                fails.append(f"{tag}: 三个键都不可点（应当选了默认项就可用）")
            pg.close()
        br.close()

    if fails:
        print("\n未通过：")
        for f in fails:
            print("  -", f)
        return 1
    print("\n全部通过，截图在", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
