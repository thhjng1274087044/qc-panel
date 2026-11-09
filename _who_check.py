"""验证顶栏「登录人」的三态：命中真名 / 未收录（? 前缀+虚线）/ 取不到（隐藏）。

别人的账号没法借来登录，所以这里用注入 S.who 的方式把三种情况各渲染一遍，
证明「不在名单里的人」不会显示成假名字，也不会静默显示成普通 id。
"""

from __future__ import annotations

import json
import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("QC_BASE", "http://127.0.0.1:8776")
TOKEN = os.environ.get("QC_TOKEN", "")

results: list[tuple[bool, str, str]] = []


def ck(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))


def read_who(panel) -> dict:
    return panel.evaluate("""() => {
        const el = document.querySelector('#hdWho');
        if(!el) return null;
        const r = el.getBoundingClientRect();
        return {
            text: el.textContent.trim(),
            title: el.title,
            unk: el.classList.contains('unk'),
            shown: getComputedStyle(el).display !== 'none',
            w: Math.round(r.width),
            border: getComputedStyle(el).borderTopStyle,
        };
    }""")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        pg = browser.new_page(viewport={"width": 1440, "height": 980})
        pg.goto(f"{BASE}/?k={TOKEN}", wait_until="domcontentloaded")
        pg.wait_for_timeout(4500)

        panel = None
        for fr in pg.frames[1:]:
            if "/panel" in (fr.url or ""):
                panel = fr
                break
        ck(panel is not None, "面板 iframe 已加载")
        if panel is None:
            return

        box = pg.query_selector("#plug").bounding_box()

        # ---- 态 1：名单里有人（假宿主返回张示例的 open_id）
        a = read_who(panel)
        print("命中态:", json.dumps(a, ensure_ascii=False))
        ck(bool(a and a["text"] == "张示例"), "命中：显示真名", (a or {}).get("text", ""))
        ck(bool(a and not a["unk"]), "命中：不加虚线标记")
        ck(bool(a and a["border"] != "dashed"), "命中：边框实线", (a or {}).get("border", ""))
        ck(bool(a and a["w"] <= 90), "命中：不挤占顶栏", f"{a['w']}px")

        # ---- 态 2：id 不在名单里（换成别人、且确实查不到）
        panel.evaluate("""() => {
            const q = window.__qc;
            q.S.who = { id: 'ou_deadbeef00000000000000000000dead', name: '',
                        byBase: false, label: 'ou_…dead' };
            q.renderWho();
        }""")
        pg.wait_for_timeout(300)
        b = read_who(panel)
        print("未收录态:", json.dumps(b, ensure_ascii=False))
        ck(bool(b and b["text"] == "?ou_…dead"), "未收录：id 前加问号", (b or {}).get("text", ""))
        ck(bool(b and b["unk"]), "未收录：样式与真名可区分")
        ck(bool(b and b["border"] == "dashed"), "未收录：虚线边框", (b or {}).get("border", ""))
        ck(bool(b and "姓名未收录" in b["title"]), "未收录：悬停说明原因")
        ck(bool(b and a and b["text"] != a["text"]), "未收录：不会冒充别人")
        ck(bool(b and b["w"] <= 90), "未收录：不挤占顶栏", f"{b['w']}px")

        if box:
            pg.screenshot(path="shot_who_unknown.png", clip=box)

        # 逐条截顶栏，便于肉眼对照
        panel.evaluate("""() => {
            const q = window.__qc;
            q.S.who = { id: 'ou_00000000000000000000000000000001', name: '张示例',
                        byBase: false, label: '张示例' };
            q.renderWho();
        }""")
        pg.wait_for_timeout(250)
        hd = panel.query_selector("header") or panel.query_selector(".hd")
        if hd:
            hd.screenshot(path="shot_who_hd_ok.png")
        print("顶栏截图: shot_who_hd_ok.png")

        # ---- 态 3：完全取不到 id（SDK 没给）→ 隐藏，不能瞎显示
        panel.evaluate("""() => { const q = window.__qc; q.S.who = null; q.renderWho(); }""")
        pg.wait_for_timeout(250)
        c = read_who(panel)
        print("取不到态:", json.dumps(c, ensure_ascii=False))
        ck(bool(c and not c["shown"]), "取不到：整个 chip 隐藏")
        ck(bool(c and c["text"] == ""), "取不到：不留残字")

        browser.close()

    ok = sum(1 for r in results if r[0])
    print("\n" + "=" * 46)
    for good, name, detail in results:
        print(("  ✓ " if good else "  ✗ ") + name + (f"   [{detail}]" if detail else ""))
    print("=" * 46)
    print(f"{ok} 通过 / {len(results) - ok} 失败")


if __name__ == "__main__":
    main()
