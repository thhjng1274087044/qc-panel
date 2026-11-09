"""验证「当前登录人」这条线：SDK 取值 → 查姓名 → 顶栏显示 → 落库留痕。

不依赖真飞书：假宿主按 Bridge_getUserId 返回固定 open_id（张示例），
该 id 在 data/people.json 里，能验证「id → 姓名」这一跳。
"""

from __future__ import annotations

import json
import os
import sqlite3

from playwright.sync_api import sync_playwright

BASE = os.environ.get("QC_BASE", "http://127.0.0.1:8776")
TOKEN = os.environ.get("QC_TOKEN", "")
DB = "data/qc.db"

results: list[tuple[bool, str, str]] = []


def ck(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))


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
        ck(panel is not None, "面板 iframe 已加载", panel.url if panel else "未找到")
        if panel is None:
            return

        # 1. 顶栏应出现登录人 chip
        who = panel.evaluate("""() => {
            const el = document.querySelector('#hdWho');
            if(!el) return null;
            const r = el.getBoundingClientRect();
            return { text: el.textContent.trim(), title: el.title,
                     shown: getComputedStyle(el).display !== 'none',
                     w: Math.round(r.width), h: Math.round(r.height) };
        }""")
        print("顶栏 chip:", json.dumps(who, ensure_ascii=False))
        ck(bool(who and who["shown"]), "顶栏显示登录人", (who or {}).get("text", ""))
        ck(bool(who and who["text"] == "张示例"), "ID 已换成姓名", (who or {}).get("text", ""))
        ck(bool(who and "ou_00000000000000000000000000000001" in who["title"]), "悬停可见完整 id")
        ck(bool(who and who["w"] <= 90), "chip 宽度受限不挤占顶栏", f"{who['w']}px")

        # 2. 顶栏不横向溢出（400px 场景）
        ov = panel.evaluate("""() => ({
            sw: document.documentElement.scrollWidth,
            cw: document.documentElement.clientWidth
        })""")
        ck(ov["sw"] <= ov["cw"] + 1, "400px 下无横向溢出", f"{ov['sw']} ≤ {ov['cw']}")

        # 3. 不暴露密码级别的信息：title 里不带 token
        ck(bool(who and TOKEN not in who["title"]), "chip 不含访问口令")

        # 截一张 400px 面板
        box = pg.query_selector("#plug").bounding_box()
        if box:
            pg.screenshot(path="shot_who.png", clip=box, full_page=False)

        # 4. 提交一条附言，验证 actor 落库
        rid = panel.evaluate("() => (window.__qc && window.__qc.S && window.__qc.S.locked) || null")
        if not rid:
            rid = panel.evaluate("() => document.querySelector('#bindRid b')?.textContent || null")
        print("当前锁定:", rid)
        ck(bool(rid), "已有绑定记录", str(rid))

        panel.click("#remark")
        panel.wait_for_selector("#maskNote.on", timeout=3000)
        panel.fill("#noteText", "登录人留痕自测")
        panel.click("#noteOk")
        pg.wait_for_timeout(2500)

        with sqlite3.connect(DB) as c:
            row = c.execute(
                "SELECT actor, note, payload FROM qc_action ORDER BY id DESC LIMIT 1"
            ).fetchone()
        print("最近动作:", row[:2] if row else None)
        ck(bool(row), "动作已落库")
        if row:
            actor, note, payload = row
            ck(actor == "张示例", "actor 存为姓名", actor)
            ck("ou_00000000000000000000000000000001" in (payload or ""), "payload 保留完整 actor_id")
            ck(note == "登录人留痕自测", "附言内容正确", note)

        # 5. 留痕页应显示这个 actor
        panel.click("#tabs button[data-t='log']")
        pg.wait_for_timeout(600)
        log_txt = panel.inner_text("#logList")
        ck("张示例" in log_txt, "留痕列表显示姓名", log_txt.splitlines()[-1][:40] if log_txt else "")

        browser.close()

    ok = sum(1 for r in results if r[0])
    print("\n" + "=" * 46)
    for good, name, detail in results:
        print(("  ✓ " if good else "  ✗ ") + name + (f"   [{detail}]" if detail else ""))
    print("=" * 46)
    print(f"{ok} 通过 / {len(results) - ok} 失败")


if __name__ == "__main__":
    main()
