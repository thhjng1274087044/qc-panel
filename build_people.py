#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从飞书任务表的人员字段生成 id→姓名 映射。

为什么不用通讯录接口：
    应用（bot）的通讯录权限范围只覆盖「办公室」2 人，查工程测绘部 / 房产测绘部
    的人一律 41050 no user authority error。但任务表的人员字段值是 {id, name}
    成对返回的，读记录就能拿到姓名，不需要通讯录权限。

用法：
    python build_people.py                # 拉任务表，写 data/people.json
    python build_people.py --limit 2000   # 只扫前 2000 条（调试用）

产物：
    data/people.json         { "ou_xxx": "张三", ... }
    data/people.local.json   手工补录，不会被本脚本覆盖（可选，优先级更高）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR / "data" / "people.json"
LOCAL = BASE_DIR / "data" / "people.local.json"

# 本仓库不含任何真实 base / 表 ID，必须由环境变量传入
BASE_TOKEN = os.getenv("QC_BASE_TOKEN", "")
TABLE_ID = os.getenv("QC_TASK_TABLE_ID", "")
# 任务表上的人员类字段：项目经理 / 任务参与人员 / 质检上传人员 / 本部员工业务辅助判断 / 创建人
FIELDS = ["fldqLI158Q", "fldDghgSn6", "fldt2COH6G", "fldblPPJTB", "fldSakntfW"]
PAGE = 200


def find_cli() -> str | None:
    """定位 lark-cli 的 run.js。"""
    cand = os.getenv("LARK_CLI")
    if cand and Path(cand).exists():
        return cand
    npm = Path(os.getenv("APPDATA", "")) / "npm" / "node_modules"
    p = npm / "@larksuite" / "cli" / "scripts" / "run.js"
    if p.exists():
        return str(p)
    exe = shutil.which("lark-cli")
    return exe


def run_page(cli: str, offset: int, limit: int, retries: int = 3) -> dict:
    cmd = ["node", cli, "base", "+record-list",
           "--base-token", BASE_TOKEN, "--table-id", TABLE_ID]
    for f in FIELDS:
        cmd += ["--field-id", f]
    cmd += ["--offset", str(offset), "--limit", str(limit),
            "--format", "json", "--as", "bot"]

    last = ""
    for attempt in range(retries):
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        body = (r.stdout or "").strip()
        if body.startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError as e:
                last = f"JSON 解析失败：{e}"
        else:
            last = f"返回非 JSON（{len(body)} 字节，stderr={len(r.stderr)} 字节）"
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000, help="最多扫描多少条记录")
    args = ap.parse_args()

    if not BASE_TOKEN or not TABLE_ID:
        print("缺少 QC_BASE_TOKEN / QC_TASK_TABLE_ID 环境变量。\n"
              "本脚本需要指向一个真实的多维表格，仓库里不内置任何 base / 表 ID。\n"
              "例：QC_BASE_TOKEN=appxxxx QC_TASK_TABLE_ID=tblxxxx python build_people.py")
        return 2

    cli = find_cli()
    if not cli:
        print("找不到 lark-cli，请设置环境变量 LARK_CLI 指向 run.js")
        return 2

    pairs: dict[str, str] = {}
    scanned = 0
    offset = 0
    while offset < args.limit:
        try:
            d = run_page(cli, offset, PAGE)
        except Exception as e:
            print(f"[{offset}] 解析失败：{e}")
            break
        if not d.get("ok"):
            err = (d.get("error") or {}).get("message") or d.get("error")
            print(f"[{offset}] 接口失败：{err}")
            break
        data = d.get("data") or {}
        rows = data.get("data") or []
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            for cell in row:
                if isinstance(cell, list):
                    for p in cell:
                        if isinstance(p, dict) and p.get("id"):
                            name = (p.get("name") or "").strip()
                            if name:
                                pairs[p["id"]] = name
        print(f"  已扫 {scanned} 条，累计 {len(pairs)} 人", end="\r")
        if not data.get("has_more"):
            break
        offset += PAGE

    print(f"\n任务表人员字段：扫描 {scanned} 条，得到 {len(pairs)} 人")

    # 手工补录优先（例如审核人本身不参与具体任务，表里查不到）
    if LOCAL.exists():
        try:
            extra = json.loads(LOCAL.read_text(encoding="utf-8"))
            extra = {k: v for k, v in extra.items()
                     if isinstance(k, str) and isinstance(v, str) and v.strip()}
            if extra:
                pairs.update(extra)
                print(f"并入手工补录 people.local.json：{len(extra)} 人")
        except Exception as e:
            print(f"手工补录读取失败（忽略）：{e}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写出 {OUT.relative_to(BASE_DIR)}（{len(pairs)} 人）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
