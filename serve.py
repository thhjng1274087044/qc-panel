"""生产启动入口。

与 app.py 的区别：用 waitress 而不是 Flask 自带开发服务器，
可多线程处理并发、支持监听 0.0.0.0、不会打印开发警告。

    PORT=8080 QC_TOKEN=xxxxxxxx python serve.py
"""

import os
import sys

try:
    from waitress import serve
except ImportError:
    sys.exit("缺少 waitress，请先执行：pip install -r requirements.txt")

import app as qc


def main():
    qc.load_records()
    qc.init_db()

    port = int(os.environ.get("PORT") or os.environ.get("QC_PORT") or "8776")
    host = os.environ.get("QC_HOST") or "0.0.0.0"
    token = qc.QC_TOKEN

    print("\n  质检插件服务")
    print(f"  监听        {host}:{port}")
    print(f"  样本        {len(qc._records)} 条 · 判据版本 {qc.RULE_VER}")
    print(f"  图片目录    {qc.IMG_DIR}")
    print(f"  数据库      {qc.DB}")
    if token:
        print(f"  面板地址    http(s)://<你的域名>/panel?k={token}")
    else:
        print("  ⚠ 未设置 QC_TOKEN，任何人都能访问数据接口，仅限本机调试")
    print()

    serve(qc.app, host=host, port=port, threads=8, channel_timeout=120)


if __name__ == "__main__":
    main()
