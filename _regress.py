# -*- coding: utf-8 -*-
"""插件面板回归：假宿主 + 本地 SDK + 后端三表，一次跑完九项。"""
import os
import sqlite3
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8776"
TOKEN = os.environ.get("QC_TOKEN") or ""
ok = fail = 0


def reset_db():
    """回归前清掉人工动作，让每次从「待确认」起跑（只动本地测试库）。"""
    c = sqlite3.connect(r"E:\质检skill\_plugin_server\data\qc.db")
    c.execute("DELETE FROM qc_action")
    c.execute("UPDATE qc_result SET conclusion=ai_conclusion")
    c.commit()
    c.close()


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    reset_db()
    with sync_playwright() as sp:
        b = sp.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/?k=" + TOKEN, wait_until="domcontentloaded")
        pg.wait_for_timeout(4200)
        fl = pg.frame(url=lambda u: "/panel" in u)

        print("\n[1] 首屏绑定")
        bind = fl.inner_text("#bindRid").strip()
        tn = fl.inner_text("#tn").strip()
        check("绑定条显示 recordId", "rec" in bind, bind)
        check("任务号已渲染", len(tn) > 5, tn)
        check("结论理由已渲染", len(fl.inner_text("#reason")) > 10)
        check("证据图已加载",
              fl.evaluate("()=>document.getElementById('shotImg').naturalWidth>0"))

        print("\n[2] 跟随左表切换")
        rids = pg.eval_on_selector_all("#grid tbody tr[data-rid]", "e=>e.map(x=>x.dataset.rid)")
        for idx in (3, 6, 2):
            pg.click(f"#grid tbody tr:nth-child({idx})")
            pg.wait_for_timeout(2000)
            want = rids[idx - 1]
            got_tn = fl.inner_text("#tn").strip()
            check(f"点第{idx}行 → {want[:10]}…",
                  want in fl.inner_text("#bindRid"), got_tn)

        print("\n[3] 漂移拦截")
        before = fl.inner_text("#tn").strip()
        pg.click("#btnDrift")
        fl.evaluate("document.getElementById('pass').click()")
        pg.wait_for_timeout(700)
        check("弹出拦截窗", fl.is_visible("#maskGuard"))
        gtxt = fl.inner_text("#maskGuard").replace("\n", " ")
        check("拦截窗列出两条记录", before in gtxt and "rec" in gtxt, gtxt[:90])
        fl.evaluate("document.getElementById('gOk').click()")
        pg.wait_for_timeout(2000)
        check("关闭后跟上左表", fl.inner_text("#tn").strip() != before,
              fl.inner_text("#tn").strip())

        print("\n[4] 正常确认")
        t0 = fl.inner_text("#tn").strip()
        rid0 = [x for x in rids if True]
        fl.evaluate("document.getElementById('pass').click()")
        pg.wait_for_timeout(2600)
        check("签署行出现", fl.is_visible("#sign"), fl.inner_text("#sign")[:50])
        check("状态变已确认", "已确认" in fl.inner_text("#hdSt"))

        print("\n[5] 撤销")
        fl.evaluate("document.getElementById('undo').click()")
        pg.wait_for_timeout(2200)
        check("撤销后回到待确认", "已确认" not in fl.inner_text("#hdSt"),
              fl.inner_text("#hdSt").strip())

        print("\n[6] 打回带备注")
        fl.evaluate("document.getElementById('reject').click()")
        pg.wait_for_timeout(300)
        check("打回弹窗出现", fl.is_visible("#maskReject"))
        fl.fill("#rejectNote", "合同时限不符，退回补件")
        fl.evaluate("document.getElementById('rejectOk').click()")
        pg.wait_for_timeout(2400)
        check("打回生效", fl.is_visible("#sign") and "打回" in fl.inner_text("#sign"),
              fl.inner_text("#sign")[:60])

        print("\n[7] 四 Tab 内容与溢出")
        for t in ("det", "rule", "exe", "log"):
            fl.evaluate(f"document.querySelector('#tabs button[data-t=\"{t}\"]').click()")
            pg.wait_for_timeout(180)
            m = fl.evaluate("""()=>{const b=document.getElementById('body');
                return {sh:b.scrollHeight,ch:b.clientHeight,sw:b.scrollWidth,cw:b.clientWidth}}""")
            check(f"Tab {t} 无横向溢出", m["sw"] <= m["cw"] + 2, f"{m['sw']}/{m['cw']}")
            check(f"Tab {t} 有内容", m["sh"] > 60, f"h={m['sh']}")

        print("\n[8] 400px 侧栏宽度")
        pg.set_viewport_size({"width": 820, "height": 900})
        pg.wait_for_timeout(600)
        fl2 = pg.frame(url=lambda u: "/panel" in u)
        m = fl2.evaluate("""()=>{const d=document.documentElement;
            return {sw:d.scrollWidth,cw:d.clientWidth,
                    ov:[...document.querySelectorAll('body *')].filter(e=>e.scrollWidth>e.clientWidth+3&&getComputedStyle(e).overflowX==='visible').map(e=>e.className).slice(0,4)}}""")
        check("整页无横向溢出", m["sw"] <= m["cw"] + 2, f"{m['sw']}/{m['cw']} {m['ov']}")
        pg.screenshot(path="shot_wide.png")
        pg.set_viewport_size({"width": 470, "height": 900})
        pg.wait_for_timeout(600)
        pg.screenshot(path="shot_narrow.png")
        fl3 = pg.frame(url=lambda u: "/panel" in u)
        m2 = fl3.evaluate("()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2")
        check("窄屏无横向溢出", m2)

        print("\n[9] 运行期错误")
        check("无 JS 异常", not errs, str(errs[:2]))

        print(f"\n结果：{ok} 通过 / {fail} 失败")
        b.close()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
