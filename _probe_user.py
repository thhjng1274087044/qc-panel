"""探针：在真实 iframe 环境里调用 SDK 的用户身份接口，看它发出什么请求键、返回什么。

结论会写在 shot_probe_user.png 里，同时把宿主侧日志原文打出来。
"""

from __future__ import annotations

import json
import os

from playwright.sync_api import sync_playwright

BASE = os.environ.get("QC_BASE", "http://127.0.0.1:8776")
TOKEN = os.environ.get("QC_TOKEN", "")

PROBE = r"""
async () => {
  const out = { exports: null, bridgeKeys: null, calls: {}, fatal: null };
  const race = (p, ms) => Promise.race([
    Promise.resolve(p).then(v => ({ ok: true, v })),
    new Promise(r => setTimeout(() => r({ ok: false, v: 'TIMEOUT' }), ms)),
  ]).catch(e => ({ ok: false, v: 'ERR ' + (e && (e.message || e)) }));

  try {
    const m = await import('/vendor/sdk/bitable_app.mjs');
    out.exports = Object.keys(m);
    const br = m.bridge;
    if (!br) { out.fatal = 'bridge 未导出'; return out; }
    out.bridgeKeys = Object.getOwnPropertyNames(Object.getPrototypeOf(br))
      .filter(k => k !== 'constructor');
    for (const fn of ['getUserId', 'getBaseUserId', 'getTenantKey', 'getInstanceId', 'getEnv']) {
      if (typeof br[fn] !== 'function') { out.calls[fn] = 'METHOD_MISSING'; continue; }
      out.calls[fn] = await race(br[fn].call(br), 5000);
    }
  } catch (e) {
    out.fatal = String(e);
  }
  return out;
}
"""


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        pg = browser.new_page(viewport={"width": 1440, "height": 980})
        pg.goto(f"{BASE}/?k={TOKEN}", wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)

        target = None
        for fr in pg.frames[1:]:
            if "/panel" in (fr.url or ""):
                target = fr
                break
        if target is None:
            print("找不到面板 iframe，现有 frames:")
            for fr in pg.frames:
                print("   ", fr.url)
            browser.close()
            return

        print("面板 iframe:", target.url)
        res = target.evaluate(PROBE)
        print("=== 导出模块 ===")
        print(res.get("exports"))
        print("=== bridge 方法 ===")
        print(res.get("bridgeKeys"))
        print("=== 调用结果 ===")
        for k, v in (res.get("calls") or {}).items():
            print(f"  {k:14} -> {json.dumps(v, ensure_ascii=False)}")
        if res.get("fatal"):
            print("!!! fatal:", res["fatal"])

        pg.wait_for_timeout(1200)
        body = pg.inner_text("body")
        print("=== 宿主日志里「未实现 / invoke」相关行 ===")
        for line in body.splitlines():
            if "未实现" in line or "invoke" in line.lower():
                print("   ", line.strip())

        pg.screenshot(path="shot_probe_user.png", full_page=False)
        print("截图: shot_probe_user.png")
        browser.close()


if __name__ == "__main__":
    main()
