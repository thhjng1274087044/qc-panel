# -*- coding: utf-8 -*-
"""核对台交互自测（真浏览器点真按钮，写的是**临时库**）。

为什么必须点：接口自测证明「能存」，不能证明「点了之后页面对」。
上一轮就栽在这 —— 接口全绿、页面一片 401 和白屏。

流程：起一个 QC_DATA=临时目录 的服务 → 点同意 → 点有意见选原因 → 刷新看重不重
用法：python _e2e_review_ui.py      产物：shots/review_sheet.png
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8778
BASE = f"http://127.0.0.1:{PORT}"
OUT = os.path.join(HERE, "shots")
os.makedirs(OUT, exist_ok=True)

PASS = 0
FAIL = 0


def ck(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {msg}")
    else:
        FAIL += 1
        print(f"  FAIL {msg}")


def wait_health(timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="qc_ui_test_")
    shutil.copy(os.path.join(HERE, "data", "records.json"), os.path.join(tmp, "records.json"))
    env = dict(os.environ, QC_DATA=tmp, QC_PORT=str(PORT))

    # 先让 perm 在临时库里建管理员，再签一张票
    boot = subprocess.run(
        [PY, "-c",
         "import sys;sys.path.insert(0,'.');import app,perm;app.init_db();"
         "u=perm.list_users()[0]['id'];print(u,perm.new_ticket(u,'audit'))"],
        cwd=HERE, env=env, capture_output=True, text=True)
    tail = [ln for ln in boot.stdout.strip().splitlines() if ln.strip()]
    if boot.returncode != 0 or not tail:
        print(boot.stdout, boot.stderr)
        return 1
    uid, tk = tail[-1].split()
    print(f"  测试用户 {uid}")

    srv = subprocess.Popen([PY, "app.py"], cwd=HERE, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_health():
            print("  服务没起来")
            return 1
        url = f"{BASE}/review?k={tk}"
        with sync_playwright() as p:
            br = p.chromium.launch(channel="msedge", headless=True)
            pg = br.new_page(viewport={"width": 1280, "height": 900})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(url, wait_until="networkidle")
            pg.wait_for_selector(".row", timeout=10000)

            def stat():
                return pg.evaluate("""() => ({
                  done: (document.querySelector('#sD')||{}).textContent,
                  disp: (document.querySelector('#sX')||{}).textContent,
                  agree: document.querySelectorAll('.mk.y').length,
                  dispute: document.querySelectorAll('.mk.x').length,
                })""")

            # ---- 1. 点「同意」 ----
            pg.click(".row")
            pg.click("#bAgree")
            pg.wait_for_selector(".mk.y", timeout=6000)
            s = stat()
            ck(s["agree"] >= 1 and s["done"] == "1", f"点同意：行内出现「已同意」，已表态 {s['done']}")
            ck(pg.evaluate("() => document.querySelector('#hint').textContent.length > 0"),
               "表态后底部提示有反馈")

            # ---- 2. 点「有意见」→ 必须选原因（必须挑一条未表态的，否则等于改主意） ----
            pg.evaluate("""() => {
              const r=[...document.querySelectorAll('.row')].find(x=>x.querySelector('.mk.q'));
              if(r) r.click();
            }""")
            pg.click("#bDispute")
            pg.wait_for_selector("#sheet:not([hidden])", timeout=4000)
            chips = pg.eval_on_selector_all("#shC .chip", "els => els.map(e => e.textContent)")
            ck(chips == ["图不对", "机器看错", "规矩不对"], f"有意见三选一：{chips}")
            pg.screenshot(path=os.path.join(OUT, "review_sheet.png"))
            pg.click("#shC .chip:nth-child(2)")   # 机器看错
            pg.wait_for_selector(".mk.x", timeout=6000)
            s = stat()
            ck(s["dispute"] >= 1 and s["disp"] == "1", f"选完原因立刻提交，有意见 {s['disp']}")

            # ---- 3. 刷新还要在（存进库了，不是只在内存） ----
            pg.goto(url, wait_until="networkidle")
            pg.wait_for_selector(".row", timeout=10000)
            pg.wait_for_timeout(400)
            s = stat()
            ck(s["done"] == "2", f"刷新后仍显示已表态 {s['done']} 条")
            ck(s["agree"] >= 1 and s["dispute"] >= 1, "同意与有意见都还在")

            # ---- 4. 详情面板显示表态 ----
            pg.evaluate("""() => {
              const r=[...document.querySelectorAll('.row')].find(x=>x.querySelector('.mk.x'));
              if(r) r.click();
            }""")
            pg.wait_for_timeout(400)
            det = pg.evaluate("() => (document.querySelector('#det')||{}).innerText||''")
            ck("机器看错" in det and "没改" in det, "详情里写清「有意见 · 机器看错」，并说明机器结论没改")

            # ---- 5. 整条记录也能表态 ----
            pg.evaluate("""() => {
              const ms=[...document.querySelectorAll('.m')];
              const last=ms[ms.length-1]; if(last) last.click();
            }""")
            pg.wait_for_timeout(900)
            ck(pg.evaluate("() => document.querySelectorAll('.row').length") == 1,
               "飞书记录：只有一条「整条」表态单元")
            pg.click(".row")
            pg.click("#bHold")
            pg.wait_for_selector("#sheet:not([hidden])", timeout=4000)
            pg.click("#shC .chip:nth-child(1)")
            pg.wait_for_timeout(800)
            ck(pg.evaluate("() => document.querySelectorAll('.mk.h').length") >= 1,
               "整条记录可以标「判不了」")

            if errs:
                ck(False, f"JS 报错：{errs[:3]}")
            br.close()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=6)
        except Exception:
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
