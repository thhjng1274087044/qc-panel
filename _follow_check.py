"""验证「左侧点哪条，右侧面板就跟到哪条」。

在假宿主上点两行，比对面板标题（任务号）与绑定条是否跟着变。
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
        print("面板 iframe:", "已加载" if fr else "未加载")
        if not fr:
            b.close()
            return

        def state(tag: str) -> tuple[str, str]:
            tn = fr.eval_on_selector("#tn", "e => e.innerText").strip()
            bind = fr.eval_on_selector("#bindRid", "e => e.innerText").strip()
            head = fr.eval_on_selector("#hdSt span", "e => e.innerText").strip()
            print(f"  [{tag}] 任务号={tn!r} 绑定={bind!r} 顶栏={head!r}")
            return tn, bind

        rows = pg.eval_on_selector_all(
            "#grid tbody tr[data-rid]", "els => els.map(e => e.dataset.rid)")
        print("左表行数:", len(rows), rows[:4])
        first = state("初始")

        seen = [first]
        for i in (1, 2):
            if i >= len(rows):
                break
            pg.click(f'#grid tbody tr[data-rid="{rows[i]}"]')
            pg.wait_for_timeout(2800)
            seen.append(state(f"点第 {i+1} 行 {rows[i]}"))

        tns = [s[0] for s in seen]
        uniq = len(set(tns))
        print()
        print(f"结论：三次读到 {uniq} 种任务号 -> {'跟随正常' if uniq == len(tns) else '未跟随/重复'}")
        if uniq < len(tns):
            print("  实际序列:", tns)
        pg.screenshot(path="shot_follow_check.png", full_page=False)
        b.close()


if __name__ == "__main__":
    main()
