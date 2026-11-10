"""验证遮罩开合与跟随的联动。

旧实现把冻结状态缓存在 S.modal 里，只在 openModal/closeModal 时刷新；
#maskAudit 的开关绕过这两个函数，一旦失步就永久冻结且顶栏看不出异常。
本脚本验证：开着遮罩不跟随 → 关掉遮罩下一轮自动跟上（不需要点别处）。
"""

from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("QC_BASE", "http://127.0.0.1:8776")
K = os.environ.get("QC_TOKEN", "")
QS = f"?k={K}" if K else ""


def panel_frame(pg):
    for fr in pg.frames[1:]:
        if "/panel" in (fr.url or ""):
            return fr
    return None


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        pg.goto(f"{BASE}/host{QS}", wait_until="domcontentloaded")
        pg.wait_for_timeout(5000)
        fr = panel_frame(pg)
        if not fr:
            print("面板未加载")
            b.close()
            return

        def tn() -> str:
            return fr.eval_on_selector("#tn", "e => e.innerText").strip()

        def head() -> str:
            return fr.eval_on_selector("#hdSt span", "e => e.innerText").strip()

        rows = pg.eval_on_selector_all(
            "#grid tbody tr[data-rid]", "els => els.map(e => e.dataset.rid)")
        base_tn = tn()
        print(f"起始 {base_tn!r}  顶栏={head()!r}")

        # 1) 打开「核对」遮罩，再切左表 —— 应当不跟随
        fr.click("#auditBtn")
        pg.wait_for_timeout(1200)
        pg.click(f'#grid tbody tr[data-rid="{rows[1]}"]')
        pg.wait_for_timeout(2600)
        frozen_tn = tn()
        print(f"遮罩开时点第 2 行 -> {frozen_tn!r}  顶栏={head()!r}")

        # 2) 关掉遮罩，什么都不点 —— 下一轮应当自动跟上第 2 条
        fr.click("#auditClose")
        pg.wait_for_timeout(3000)
        after_tn = tn()
        print(f"遮罩关闭后（未再点击）-> {after_tn!r}  顶栏={head()!r}")

        print()
        c1 = frozen_tn == base_tn
        c2 = after_tn != base_tn
        print(f"  遮罩期间暂停跟随 : {'✓' if c1 else '✗ 仍在跟随'}")
        print(f"  关闭后自动恢复   : {'✓' if c2 else '✗ 卡住没跟上'}")
        print("  结论:", "通过" if (c1 and c2) else "未通过")
        b.close()


if __name__ == "__main__":
    main()
