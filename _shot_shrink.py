# -*- coding: utf-8 -*-
"""文件压缩模块：真浏览器实测（msedge），自起自停一个隔离实例。

为什么不能只 curl：这个模块**全部逻辑在浏览器里**（pdf.js 渲染 + pdf-lib 重拼），
服务端只发页面和库。curl 只能证明文件发得出去，压不压得动只有真浏览器知道。

为什么自己起实例：生产那个进程不归测试管，沙箱会回收工具拉起的后台进程，
跑到一半服务没了会得到假失败。这里自己 Popen、自己 hold、结束自己关。

用法：python _shot_shrink.py [样本pdf]
产物：shot_shrink_done.png / shot_shrink_420.png / shrink_out.pdf
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "shot-shrink-master"
DEFAULT_SAMPLE = r"E:\质检skill\_dwg_recover\WLJC26018426-GC001\温岭市担屿涂中水回填项目河道标高测量.pdf"
TARGET_MB = 4.0

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def wait_port(port, tries=40):
    for _ in range(tries):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


def main():
    sample = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SAMPLE
    if not os.path.isfile(sample):
        print("样本 PDF 不见了，换一个再跑：", sample)
        return 1
    before = os.path.getsize(sample) / 1048576
    print(f"样本：{os.path.basename(sample)}  {before:.2f} MB\n")

    tmp = tempfile.mkdtemp(prefix="qcshotshrink_")
    with open(os.path.join(tmp, "records.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    env = dict(os.environ, QC_DATA=tmp, PORT=str(PORT), QC_TOKEN=TOKEN,
               QC_IMG_DIR=r"E:\质检skill\证据截图_20261105_全量88")
    proc = subprocess.Popen([PY, "-u", "serve.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        if not wait_port(PORT):
            print("服务没起来")
            return 1
        sess = requests.Session()
        sess.get(BASE + "/?k=" + TOKEN, allow_redirects=False, timeout=20)

        with sync_playwright() as sp:
            b = sp.chromium.launch(channel="msedge")
            ctx = b.new_context(viewport={"width": 1240, "height": 960}, accept_downloads=True)
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))

            print("[1] 页面与库")
            pg.goto(BASE + "/shrink?k=" + TOKEN, wait_until="domcontentloaded")
            pg.wait_for_timeout(1500)
            check("标题是文件压缩", "文件压缩" in pg.title(), pg.title())
            check("pdf.js 加载成功", pg.evaluate("() => typeof pdfjsLib !== 'undefined'"))
            check("pdf-lib 加载成功", pg.evaluate("() => typeof PDFLib !== 'undefined'"))
            check("jszip 加载成功", pg.evaluate("() => typeof JSZip !== 'undefined'"))
            check("返回首页可用", pg.locator("[data-home]").count() == 1)
            chip = pg.locator(".chip.on").inner_text().strip()
            check("默认目标 4MB", chip == "4 MB", chip)

            print("\n[2] 真压一份 PDF")
            pg.set_input_files("#fileInput", sample)
            pg.wait_for_timeout(600)
            check("文件进了列表", pg.locator(".fileitem").count() == 1,
                  pg.locator(".fname").inner_text()[:30] if pg.locator(".fname").count() else "无")
            tag = pg.locator(".tag.warn").first.inner_text() if pg.locator(".tag.warn").count() else ""
            if tag:
                print("       判定：", tag)

            pg.click("#btnStart")
            try:
                pg.wait_for_selector(".tag.ok", timeout=300000)
            except Exception:
                pass
            pg.wait_for_timeout(900)
            n_ok = pg.locator(".tag.ok").count()
            check("压缩完成", n_ok == 1, f"{n_ok} 条完成")

            text = pg.locator(".tag.ok").inner_text() if n_ok else ""
            m = re.search(r"([\d.]+)\s*MB", text)
            size = float(m.group(1)) if m else -1
            check(f"压到 {TARGET_MB}MB 以下", 0 < size < TARGET_MB, text.replace("\n", " "))
            check("比原来小", 0 < size < before, f"{before:.2f} → {size:.2f} MB")
            check("有下载链接", pg.locator("a.dl").count() == 1)
            href = pg.locator("a.dl").get_attribute("href") or ""
            check("下载是 blob 直出（没走后端）", href.startswith("blob:"), href[:18])

            print("\n[3] 下载真能落盘")
            target = os.path.join(HERE, "shrink_out.pdf")
            with pg.expect_download(timeout=60000) as dl:
                pg.click("a.dl")
            d = dl.value
            d.save_as(target)
            got = os.path.getsize(target) / 1048576
            check("下载文件名带 _压缩", "压缩" in d.suggested_filename, d.suggested_filename)
            check("落盘体积与页面一致", abs(got - size) < 0.06, f"{got:.2f} MB")

            pg.screenshot(path=os.path.join(HERE, "shot_shrink_done.png"))

            print("\n[4] 飞书侧栏宽度（420）")
            pg2 = ctx.new_page()
            pg2.set_viewport_size({"width": 420, "height": 880})
            pg2.goto(BASE + "/shrink?k=" + TOKEN, wait_until="domcontentloaded")
            pg2.wait_for_timeout(1500)
            ov = pg2.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            check("420px 没有横向溢出", ov <= 0, str(ov))
            pg2.screenshot(path=os.path.join(HERE, "shot_shrink_420.png"))
            pg2.close()

            if errs:
                for e in errs:
                    print("       页面报错：", e)
            check("没有 JS 报错", not errs, str(len(errs)))
            b.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print(f"\n结果：{ok} 通过 / {fail} 失败")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
