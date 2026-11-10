# -*- coding: utf-8 -*-
"""专项验证：人工的三个出口 —— 通过 / 不合格退回 / 附言，外加改判拦截。

跑在假飞书宿主上（/），面板在 iframe 里，和真飞书同一个交互路径。
"""
import os
import sys

import requests
from playwright.sync_api import sync_playwright

BASE = os.environ.get("QC_BASE") or "http://127.0.0.1:8776"
TOKEN = os.environ.get("QC_TOKEN") or "qc-74f3a91c2b6e"
HDR = {"X-QC-Token": TOKEN}
RID = "reczz28LZrntXm9r"          # 这条被临时改成 ai=不合格，用来测改判

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}  ({detail})" if detail else f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  ({detail})" if detail else f"  FAIL  {name}")


def main():
    with sync_playwright() as sp:
        b = sp.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/host?k=" + TOKEN, wait_until="domcontentloaded")
        pg.wait_for_timeout(4500)
        fl = pg.frame(url=lambda u: "/panel" in u)
        if not fl:
            print("面板没加载出来"); return 1
        pg.wait_for_timeout(1200)

        print("\n[1] AI 判不合格时，点「通过」必须弹改判理由")
        check("样本 AI 判定为不合格", "不合格" in fl.inner_text("#aiRes"),
              fl.inner_text("#aiRes")[:30])
        fl.evaluate("document.getElementById('pass').click()")
        pg.wait_for_timeout(400)
        check("弹出改判窗", fl.is_visible("#maskPass"))
        fl.evaluate("document.getElementById('passOk').click()")
        pg.wait_for_timeout(400)
        check("空理由被拦下", fl.is_visible("#maskPass"))
        fl.fill("#passNote", "差异已核实为版本更新，补件已随成果提交")
        pg.wait_for_timeout(200)
        check("预览出飞书成品句", "人工改判" in fl.inner_text("#psPrev"),
              fl.inner_text("#psPrev")[:46])
        fl.evaluate("document.getElementById('passOk').click()")
        pg.wait_for_timeout(2200)
        check("签署行显示通过", fl.is_visible("#sign") and "通过" in fl.inner_text("#sign"),
              fl.inner_text("#sign")[:44])
        r = requests.get(f"{BASE}/api/record/{RID}", headers=HDR, timeout=8).json()["record"]
        check("conclusion 落为「通过」", r.get("conclusion") == "通过", str(r.get("conclusion")))
        check("AI 原判仍是不合格", r.get("ai") == "不合格", str(r.get("ai")))

        print("\n[2] 已决策后补附言：结论不许动")
        fl.evaluate("document.getElementById('remark').click()")
        pg.wait_for_timeout(400)
        check("弹出附言窗", fl.is_visible("#maskNote"))
        fl.fill("#noteText", "建议下次补交控制点成果表")
        pg.wait_for_timeout(200)
        check("预览提示是追加", "追加" in fl.inner_text("#ntPrev"), fl.inner_text("#ntPrev")[:40])
        fl.evaluate("document.getElementById('noteOk').click()")
        pg.wait_for_timeout(2200)
        check("结论仍是通过", "通过" in fl.inner_text("#sign") and "附言" not in fl.inner_text("#sign"),
              fl.inner_text("#sign")[:44])
        r = requests.get(f"{BASE}/api/record/{RID}", headers=HDR, timeout=8).json()["record"]
        check("后端结论未变", r.get("conclusion") == "通过", str(r.get("conclusion")))
        acts = [h["action"] for h in r.get("history", [])]
        check("留痕含附言", "附言" in acts, str(acts))

        print("\n[3] 撤销 → 判不合格退回（带规则号）")
        fl.evaluate("document.getElementById('undo').click()")
        pg.wait_for_timeout(2000)
        check("撤销回到待确认", "待确认" in fl.inner_text("#hdSt") or not fl.is_visible("#sign"),
              fl.inner_text("#hdSt")[:20])
        fl.evaluate("document.getElementById('reject').click()")
        pg.wait_for_timeout(400)
        check("弹出退回窗", fl.is_visible("#maskReject"))
        n = fl.evaluate("document.querySelectorAll('#rejChips .chipr').length")
        check("问题类型 chips 已渲染", n >= 6, f"{n} 个")
        fl.evaluate("document.querySelector('#rejChips .chipr').click()")
        fl.fill("#rejectNote", "清单 12 点，报告正文写 9 点，差 3 点")
        pg.wait_for_timeout(250)
        prev = fl.inner_text("#rejPrev")
        check("预览带规则号", "不合格（P-013）" in prev, prev[:52])
        fl.evaluate("document.getElementById('rejectOk').click()")
        pg.wait_for_timeout(2200)
        sign = fl.inner_text("#sign")
        check("签署行显示打回·不合格", "打回" in sign and "不合格" in sign, sign[:50])
        r = requests.get(f"{BASE}/api/record/{RID}", headers=HDR, timeout=8).json()["record"]
        check("conclusion 落为「不合格」", r.get("conclusion") == "不合格", str(r.get("conclusion")))
        check("备注含规则号前缀", "[P-013]" in (r.get("decided_note") or ""), r.get("decided_note"))

        print("\n[4] 退回必填说明")
        fl.evaluate("document.getElementById('undo').click()")
        pg.wait_for_timeout(1800)
        fl.evaluate("document.getElementById('reject').click()")
        pg.wait_for_timeout(350)
        fl.fill("#rejectNote", "")
        fl.evaluate("document.getElementById('rejectOk').click()")
        pg.wait_for_timeout(600)
        check("空说明不放行", fl.is_visible("#maskReject"))

        print("\n[5] 时间线记录")
        fl.evaluate("document.getElementById('rejectCancel').click()")
        pg.wait_for_timeout(200)
        fl.evaluate("document.querySelector('#tabs button[data-t=\"log\"]').click()")
        pg.wait_for_timeout(400)
        log = fl.inner_text("#logList")
        check("时间线含改判记录", "人工通过" in log, "")
        check("时间线含附言", "附言（结论未改）" in log, "")

        print("\n[6] 运行期错误")
        check("无 JS 异常", not errs, str(errs[:2]))

        # 截图留档
        fl.evaluate("document.querySelector('#tabs button[data-t=\"det\"]').click()")
        pg.wait_for_timeout(300)
        fl.evaluate("document.getElementById('reject').click()")
        pg.wait_for_timeout(400)
        pg.screenshot(path="shot_ui_reject.png")
        fl.evaluate("document.getElementById('rejectCancel').click()")
        fl.evaluate("document.getElementById('remark').click()")
        pg.wait_for_timeout(400)
        pg.screenshot(path="shot_ui_note.png")
        b.close()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
