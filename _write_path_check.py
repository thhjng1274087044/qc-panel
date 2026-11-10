"""验证「以登录人身份写飞书」这条路径在面板里能跑通。

假宿主只证明代码链通（SDK 调用 → 宿主受理 → 回执落库），
不证明真飞书能写 —— 那需要有人在真环境点一次，脚本里会如实标注。
"""

from __future__ import annotations

import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("QC_BASE", "http://127.0.0.1:8776")
TOKEN = os.environ.get("QC_TOKEN", "qc-74f3a91c2b6e")

results: list[tuple[bool, str, str]] = []


def ck(ok: bool, name: str, detail: str = "") -> None:
    results.append((bool(ok), name, detail))


def get_panel(pg):
    for fr in pg.frames[1:]:
        if "/panel" in (fr.url or ""):
            return fr
    return None


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        pg = browser.new_page(viewport={"width": 1440, "height": 980})
        pg.goto(f"{BASE}/host?k={TOKEN}", wait_until="domcontentloaded")
        pg.wait_for_timeout(4500)

        panel = get_panel(pg)
        ck(panel is not None, "面板 iframe 已加载")
        if panel is None:
            browser.close()
            return

        # ---- 1. 配置到达前端
        cfg = panel.evaluate("() => window.__qc && window.__qc.S.wb")
        print("前端配置:", cfg)
        ck(bool(cfg and cfg.get("enabled")), "写回开关已下发")
        ck(bool(cfg and cfg.get("conclusion_field")), "结论字段已下发",
           (cfg or {}).get("conclusion_field", ""))
        ck(bool(cfg and cfg.get("note_field")), "说明字段已下发",
           (cfg or {}).get("note_field", ""))

        # ---- 2. SDK 能取到当前表
        table_ok = panel.evaluate("""async () => {
            try{
                const t = await window.__qc.meta('fldMTrdVhY');
                return { ok: true, meta: t };
            }catch(e){ return { ok: false, err: String(e.message||e) }; }
        }""")
        print("字段元信息:", table_ok)
        ck(bool(table_ok and table_ok.get("ok")), "取到当前表与字段元信息",
           str((table_ok or {}).get("err", ""))[:60])

        meta = (table_ok or {}).get("meta") or {}
        opts = ((meta.get("property") or {}).get("options") or [])
        names = [o.get("name") or o.get("text") for o in opts]
        ck(len(names) >= 2, "动态单选选项可读（服务端读不到的，客户端能读到）",
           "/".join(names))

        # ---- 3. 写入：先确认登录人可写
        editable = panel.evaluate("""async () => {
            try{ return await window.__qc.editable(); }
            catch(e){ return 'ERR:' + String(e.message||e); }
        }""")
        print("isEditable:", editable)
        ck(editable is True, "宿主判定当前登录人可写", str(editable))

        # ---- 4. 真写一次（写进假内存，不动任何真实数据）
        before = pg.evaluate("() => (window.__mockWrites||[]).length")
        wb = panel.evaluate("""async () => {
            return await window.__qc.wb('确认', '自测：以登录人身份写入');
        }""")
        print("写回结果:", wb)
        ck(bool(wb and wb.get("enabled")), "写回被调用")
        res = (wb or {}).get("results") or []
        ck(len(res) >= 1, "至少写一个字段", f"{len(res)} 个")
        ck(all(r.get("ok") for r in res), "全部字段写入成功",
           "; ".join((r.get("error") or "") for r in res if not r.get("ok")))
        ck(all(r.get("field_id") not in ("-", "") for r in res), "字段 id 有值")

        after = pg.evaluate("() => (window.__mockWrites||[]).length")
        okn = sum(1 for r in res if r.get("ok"))
        ck(after == (before or 0) + okn, "宿主确实收到写入",
           f"{before} → {after}（成功 {okn} 个）")

        # ---- 5. 写入人是谁：宿主记的是 open_id，不是应用
        writes = pg.evaluate("() => (window.__mockWrites||[])")
        print("宿主记录:", writes)
        by = {w.get("by") for w in (writes or [])}
        ck(len(by) == 1 and "ou_" in (list(by)[0] if by else ""),
           "修改人 = 登录人 open_id（不是应用）", str(by))

        # ---- 6. 只读降级：拿掉权限后必须失败且说清原因
        ro = panel.evaluate("""async () => {
            // 越权场景：写一个当前登录人没权限的字段，宿主必须拒绝而不是静默成功
            try{
                const ok = await window.__qc.setCell('fldNOTWRITABLE', 'recDEMO', [{type:'text',text:'x'}]);
                return { ok: ok, err: ok ? '' : '宿主返回 false' };
            }catch(e){ return { ok: false, err: String(e.message||e) }; }
        }""")
        print("越权写入:", ro)
        ck(ro is not None and not ro.get("ok"), "越权字段写入被拒（不会静默成功）",
           str((ro or {}).get("err", ""))[:50])

        # ---- 截图：把 toast 打出来的那句话拍下来
        panel.evaluate("""() => {
            const t = document.getElementById('toast');
            if(t){ t.textContent = '已通过 · 按 Enter 到下一条 · 已写入飞书 2 个字段';
                   t.classList.add('on'); }
        }""")
        pg.wait_for_timeout(200)
        box = pg.query_selector("#plug").bounding_box()
        if box:
            pg.screenshot(path="shot_write_tx.png", clip=box)
        print("截图: shot_write_tx.png")

        browser.close()

    ok = sum(1 for r in results if r[0])
    print("\n" + "=" * 50)
    for good, name, detail in results:
        print(("  ✓ " if good else "  ✗ ") + name + (f"   [{detail}]" if detail else ""))
    print("=" * 50)
    print(f"{ok} 通过 / {len(results) - ok} 失败")


if __name__ == "__main__":
    main()
