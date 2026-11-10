# -*- coding: utf-8 -*-
"""把 88 条全量跑批的真实产物聚成面板数据 data/records.json。

数据源（均为已完成的真实产物，不造数）：
  E:\\质检skill\\证据截图_20261105_全量88\\上传内容_全量.csv   序号/任务编号/record_id/徽章/模板/对比图/拟写入
                                   _上传结果.csv            状态/file_token/说明
  E:\\质检skill\\_pick\\stream_结论.csv                    任务名称/项目子项（能补则补）
图片不拷贝，由 Flask 直接从原目录读，省磁盘。
"""
import csv
import json
import os

BATCH = r"E:\质检skill\证据截图_20261105_全量88"
STREAM = r"E:\质检skill\_pick\stream_结论.csv"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "records.json")

TONE = {
    "√ 一致": ("green", "一致"),
    "× 不一致（登记不改判）": ("red", "不一致"),
    "× 不一致": ("red", "不一致"),
    "并列核对": ("gray", "并列核对"),
}


def rows(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    content = rows(os.path.join(BATCH, "上传内容_全量.csv"))
    result = {r["record_id"]: r for r in rows(os.path.join(BATCH, "_上传结果.csv")) if r.get("record_id")}
    meta = {}
    for r in rows(STREAM):
        if r.get("任务编号"):
            meta[r["任务编号"]] = r

    out = []
    for c in content:
        rid = (c.get("record_id") or "").strip()
        tn = (c.get("任务编号") or "").strip()
        if not rid or not tn:
            continue
        tone, badge = TONE.get((c.get("徽章") or "").strip(), ("gray", "并列核对"))
        m = meta.get(tn, {})
        u = result.get(rid, {})
        out.append({
            "record_id": rid,
            "tn": tn,
            "name": (m.get("任务名称") or "").strip(),
            "sub": (m.get("项目子项") or "").strip(),
            "badge": badge,
            "tone": tone,
            "mode": (c.get("模板") or "").strip(),
            "img": (c.get("对比图") or "").strip(),
            "reason": (c.get("拟写入_审核意见说明") or "").strip(),
            "ai": "通过",
            "stage": "部门审核",
            "file_token": (u.get("file_token") or "").strip(),
            "upload": (u.get("状态") or "").strip(),
            "index": (c.get("序号") or "").strip(),
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    from collections import Counter
    print("写出", len(out), "条 ->", OUT)
    print("徽章:", Counter(r["badge"] for r in out).most_common())
    print("模板:", Counter(r["mode"] for r in out).most_common())
    print("有图:", sum(1 for r in out if r["img"]), "有名称:", sum(1 for r in out if r["name"]))


if __name__ == "__main__":
    main()
