# -*- coding: utf-8 -*-
"""管理页「编辑」按钮实测：真浏览器点开弹窗，加权限 / 去权限 / 改角色 / 停用。

不碰真实 36 人 —— 全程只在临时账号 `_edittest` 上操作，跑完删掉。
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("QC_BASE", "http://127.0.0.1:8776")
K = os.environ.get("QC_TOKEN", "")
NAME = "_edittest"

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ok_n = 0
bad = []


def chk(label, cond, detail=""):
    global ok_n
    if cond:
        ok_n += 1
        print(f"  PASS  {label}")
    else:
        bad.append(label)
        print(f"  FAIL  {label}   {detail}")


def api(path, body=None, method=None):
    url = f"{BASE}{path}" + (("&" if "?" in path else "?") + "k=" + urllib.parse.quote(K))
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b"{}")
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}"}


def find_user():
    d = api("/api/admin/users")
    for u in d.get("items") or []:
        if u["name"] == NAME:
            return u
    return None


def cleanup():
    u = find_user()
    if u:
        api("/api/admin/user", {"action": "delete", "uid": u["id"]})
    print("  （临时账号已清理）" if u else "  （无临时账号残留）")


def main():
    if not K:
        raise SystemExit("缺 QC_TOKEN")

    # 起始状态：先删残留，再建一个只开 audit 的普通账号
    cleanup()
    r = api("/api/admin/user", {"action": "add", "name": NAME, "pw": "abc123",
                                "role": "user", "perms": ["audit"]})
    assert r.get("ok"), r
    uid = r["uid"]
    print("临时账号:", uid)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)

        # ---- 窄视口（飞书侧栏 420px）：操作列是否常驻可见
        ctx = b.new_context(viewport={"width": 420, "height": 760})
        pg = ctx.new_page()
        pg.goto(f"{BASE}/admin?k={K}", wait_until="networkidle", timeout=60000)
        pg.wait_for_selector("#tb tr", timeout=30000)

        row = pg.locator("#tb tr", has_text=NAME).first
        chk("列表里出现临时账号", row.count() == 1)

        btn = row.locator('button[data-act="edit"]')
        chk("操作列有「编辑」按钮", btn.count() == 1)

        box = btn.bounding_box()
        vw = pg.viewport_size["width"]
        chk("窄屏下编辑按钮无需横滚即可见",
            box is not None and 0 <= box["x"] and box["x"] + box["width"] <= vw + 1,
            f"x={box and box['x']:.0f} 宽={box and box['width']:.0f} 视口={vw}")
        m = pg.evaluate("""() => {
          const w = document.querySelector('#tb').closest('.tw');
          return {scroll: w.scrollWidth, client: w.clientWidth};
        }""")
        chk("表格确实比容器宽（说明 sticky 有被考验到）", m["scroll"] > m["client"], str(m))

        # ---- 点开编辑：加一个模块
        btn.click()
        pg.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        chk("弹窗打开", pg.is_visible("#edSave"))
        chk("标题带姓名", NAME in pg.inner_text("#edTitle"), pg.inner_text("#edTitle"))

        # audit 已勾、qty 未勾
        chk("初始勾选与库一致（audit 勾、qty 不勾）",
            pg.is_checked('input[data-ep="audit"]') and not pg.is_checked('input[data-ep="qty"]'))

        ops_txt = pg.inner_text("#edOps")
        chk("弹窗内链接按已保存权限给（只出审核链接）",
            "审核链接" in ops_txt and "工程量链接" not in ops_txt, ops_txt.replace("\n", " "))
        chk("普通账号弹窗内有删除", "删除账号" in ops_txt, ops_txt.replace("\n", " "))

        pg.check('input[data-ep="qty"]')
        pg.click("#edSave")
        pg.wait_for_selector("#edMask.hide", state="attached", timeout=15000)
        u = find_user()
        chk("加权限生效：audit+qty", sorted(u["perms"]) == ["audit", "qty"], str(u["perms"]))

        # ---- 再编辑：去掉一个
        pg.locator("#tb tr", has_text=NAME).first.locator('button[data-act="edit"]').click()
        pg.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        pg.uncheck('input[data-ep="audit"]')
        pg.click("#edSave")
        pg.wait_for_selector("#edMask.hide", state="attached", timeout=15000)
        u = find_user()
        chk("去权限生效：只剩 qty", u["perms"] == ["qty"], str(u["perms"]))

        # ---- 改角色为管理员：勾选框应禁用
        pg.locator("#tb tr", has_text=NAME).first.locator('button[data-act="edit"]').click()
        pg.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        pg.select_option("#edRole", "admin")
        chk("管理员时勾选框禁用", pg.is_disabled('input[data-ep="audit"]'))
        chk("管理员时给了解释", "不生效" in pg.inner_text("#edPermHint"))
        chk("管理员弹窗里没有删除（防误删最后一个管理员）",
            "删除账号" not in pg.inner_text("#edOps"), pg.inner_text("#edOps").replace("\n", " "))
        pg.click("#edSave")
        pg.wait_for_selector("#edMask.hide", state="attached", timeout=15000)
        u = find_user()
        chk("角色已改为管理员", u["role"] == "admin", u["role"])

        # ---- 改回普通并停用
        pg.locator("#tb tr", has_text=NAME).first.locator('button[data-act="edit"]').click()
        pg.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        pg.select_option("#edRole", "user")
        pg.uncheck("#edActive")
        pg.click("#edSave")
        pg.wait_for_selector("#edMask.hide", state="attached", timeout=15000)
        u = find_user()
        chk("停用生效", u["active"] == 0, str(u["active"]))
        chk("停用后仍是普通角色", u["role"] == "user", u["role"])

        # ---- 取消按钮不留痕
        pg.locator("#tb tr", has_text=NAME).first.locator('button[data-act="edit"]').click()
        pg.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        pg.check('input[data-ep="shrink"]')
        pg.click("#edCancel")
        pg.wait_for_selector("#edMask.hide", state="attached", timeout=10000)
        u = find_user()
        chk("取消不写入（shrink 未被加）", "shrink" not in u["perms"], str(u["perms"]))

        # ---- Esc 关闭
        pg.locator("#tb tr", has_text=NAME).first.locator('button[data-act="edit"]').click()
        pg.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        pg.keyboard.press("Escape")
        chk("Esc 能关弹窗", pg.locator("#edMask.hide").count() == 1)

        pg.screenshot(path="shot_admin_edit_list.png")

        # ---- 宽屏截图：弹窗打开态
        pg2 = ctx.new_page()
        pg2.set_viewport_size({"width": 1100, "height": 720})
        pg2.goto(f"{BASE}/admin?k={K}", wait_until="networkidle", timeout=60000)
        pg2.wait_for_selector("#tb tr", timeout=30000)
        pg2.locator("#tb tr", has_text=NAME).first.locator('button[data-act="edit"]').click()
        pg2.wait_for_selector("#edMask:not(.hide)", timeout=10000)
        pg2.screenshot(path="shot_admin_edit_dialog.png")

        # ---- 自锁：管理员编辑自己不能降权（用接口验，前端只负责显示错误）
        me = api("/api/me")
        if not me.get("master") and me.get("uid"):
            r = api("/api/admin/user", {"action": "update", "uid": me["uid"], "role": "user"})
            chk("自己降权被后端拒绝", not r.get("ok"), str(r))
        else:
            print("  （主口令通道，跳过自锁用例）")

        b.close()

    cleanup()
    print(f"\n{ok_n} 通过 / {len(bad)} 失败")
    if bad:
        for x in bad:
            print("   失败:", x)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
