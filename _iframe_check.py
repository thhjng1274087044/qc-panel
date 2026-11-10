# -*- coding: utf-8 -*-
"""在「飞书同款 400px iframe」里加载两条隧道的面板，看第一屏到底显示什么。

判定：iframe 里出现 "Visit Site" / "You are about to visit" = 警告页（不算可用）
      出现「绑定 / 核对 / 质检」= 真面板
"""
import os
import pathlib

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
PROXY_SERVER = os.environ.get("http_proxy") or "http://127.0.0.1:33631"

TARGETS = [
    ("cloudflared", "https://still-wives-salary-joins.trycloudflare.com/panel?k=qc-74f3a91c2b6e"),
]

HOST_TPL = """<!doctype html>
<html><body style="margin:0;background:#e9edf3">
<div style="width:400px;height:900px;background:#fff">
  <iframe src="%s" style="width:400px;height:900px;border:0;display:block"></iframe>
</div>
</body></html>"""

WARN = ("Visit Site", "You are about to visit", "ngrok-free", "abuse")


def main():
    host = HERE / "_iframe_host.html"
    results = []

    with sync_playwright() as sp:
        b = sp.chromium.launch(
            channel="msedge",
            proxy={"server": PROXY_SERVER, "bypass": "127.0.0.1,localhost"},
        )
        for name, url in TARGETS:
            host.write_text(HOST_TPL % url, encoding="utf-8")
            pg = b.new_page(viewport={"width": 400, "height": 900})
            try:
                pg.goto("file:///" + str(host).replace("\\", "/"), wait_until="domcontentloaded")
                pg.wait_for_timeout(16000)

                frame = None
                for f in pg.frames:
                    if f == pg.main_frame:
                        continue
                    if any(k in (f.url or "") for k in ("ngrok", "trycloudflare")):
                        frame = f
                        break

                text = ""
                if frame:
                    try:
                        text = (frame.inner_text("body") or "").strip()
                    except Exception as e:
                        text = f"<读不到 body: {e}>"

                preview = " ".join(text.split())[:260]
                is_warn = any(w in text for w in WARN)
                verdict = "警告页" if is_warn else ("真面板" if ("核对" in text or "绑定" in text) else "未知")

                pg.screenshot(path=str(HERE / f"shot_iframe_{name}.png"))
                results.append({"tunnel": name, "url": url, "frame_url": frame.url if frame else None,
                                "verdict": verdict, "preview": preview})
                print(f"\n[{name}] 判定 = {verdict}")
                print(f"  frame url : {frame.url if frame else '(无)'}")
                print(f"  首段文字  : {preview}")
            except Exception as e:
                print(f"\n[{name}] 异常: {e}")
                results.append({"tunnel": name, "url": url, "verdict": "异常", "preview": str(e)[:200]})
            finally:
                pg.close()
        b.close()

    print("\n截图：", ", ".join(f"shot_iframe_{t['tunnel']}.png" for t in results))


if __name__ == "__main__":
    main()
