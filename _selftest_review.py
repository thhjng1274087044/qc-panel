# -*- coding: utf-8 -*-
"""核对台自测：只测接口与落库语义，不测样式（样式看 _shot_review.py 的截图）。

用**临时数据目录**跑，绝不写生产 qc.db —— 采集表一旦被测试数据污染，
以后所有比率都不可信，这正是这个项目一直在治的病。

用法：python _selftest_review.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="qc_review_test_")
os.environ["QC_DATA"] = TMP
# 先把样本清单拷进临时目录，否则服务起不来
src_records = os.path.join(HERE, "data", "records.json")
if os.path.isfile(src_records):
    shutil.copy(src_records, os.path.join(TMP, "records.json"))

sys.path.insert(0, HERE)
import app    # noqa: E402
import perm   # noqa: E402

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


def main():
    app.init_db()
    app.load_records()
    users = perm.list_users()
    ck(bool(users), f"有账号可用（{len(users)} 个）")
    uid = users[0]["id"]
    c = app.app.test_client()
    c.set_cookie(perm.COOKIE, perm.new_session(uid))

    # ---------- 材料 ----------
    r = c.get("/api/review/materials")
    ck(r.status_code == 200, "材料列表 200")
    d = r.get_json()
    ck(len(d["compare"]) >= 10, f"比对材料 {len(d['compare'])} 份")
    ck(len(d["records"]) == 90, f"飞书记录 {len(d['records'])} 条")
    ck(d["compare_error"] == "", "比对库能读到")

    # ---------- 检查项 + 去重 ----------
    r = c.get("/api/review/items/compare/cm:1:1")
    ck(r.status_code == 200, "检查项 200")
    d = r.get_json()
    m, items = d["meta"], d["items"]
    ck(m["dup_total"] > 0, f"单轮内重复被识别：原始 {m['raw_total']} 行 → {len(items)} 项（合并 {m['dup_total']} 行）")
    ck(len({(x['cat'], x['building'], x['item']) for x in items}) == len(items),
       "合并后 (类别,楼栋,检查项) 唯一")
    ck(bool(m["same_source_types"]), f"同源文件被标出：{m['same_source_types']}")
    ck(any(x["same_source"] for x in items), "同源类别下的条目带 same_source 标记")

    it_pass = next(x for x in items if x["verdict"] == "pass")
    it_hold = next((x for x in items if x["verdict"] == "hold"), None)

    def mark(payload):
        return c.post("/api/review/mark", json=payload)

    base = {"src": "compare", "key": "cm:1:1", "item": it_pass["item"],
            "check_cat": it_pass["cat"], "building": it_pass["building"],
            "material_hash": m["material_hash"], "elapsed_ms": 4200}

    # ---------- 同意 ----------
    r = mark(dict(base, stance="agree"))
    j = r.get_json()
    ck(r.status_code == 200 and j["ok"], "同意：写入成功")
    id1 = j["id"]
    ck(j["verdict"] == "pass", "同意 AI 通过 → verdict=pass")

    # ---------- 连点两下不产生两行 ----------
    r = mark(dict(base, stance="agree"))
    ck(r.get_json().get("dedup") is True, "同结论重复提交 → 幂等（不新增行）")

    # ---------- 有意见必须选原因 ----------
    r = mark(dict(base, stance="dispute"))
    ck(r.status_code == 400, "有意见不选原因 → 400")
    r = mark(dict(base, stance="dispute", dispute_reason="随便编的"))
    ck(r.status_code == 400, "原因不在三选一里 → 400")
    r = mark(dict(base, stance="dispute", dispute_reason="机器看错"))
    j = r.get_json()
    ck(r.status_code == 200 and j["verdict"] == "fail", "有意见 → verdict 翻成 fail")
    ck(j["supersedes"] == id1, "有意见 supersedes 指向上一条（只追加，不覆盖）")
    id2 = j["id"]

    # ---------- 判不了必须选原因 ----------
    r = mark(dict(base, stance="hold"))
    ck(r.status_code == 400, "判不了不选原因 → 400")
    r = mark(dict(base, stance="hold", hold_reason="缺规划依据"))
    j = r.get_json()
    ck(r.status_code == 200 and j["verdict"] == "hold", "判不了 → verdict=hold")

    # ---------- 机器判不了的条目 ----------
    if it_hold:
        hb = dict(base, item=it_hold["item"], check_cat=it_hold["cat"],
                  building=it_hold["building"])
        r = mark(dict(hb, stance="agree"))
        ck(r.status_code == 400, "AI 判不了时点「同意」→ 拒绝（同意一个空结论没有意义）")
        r = mark(dict(hb, stance="dispute"))
        ck(r.status_code == 400, "AI 判不了时点「有意见」但没给自己的结论 → 400")
        r = mark(dict(hb, stance="dispute", dispute_reason="机器看错", my_verdict="pass"))
        ck(r.status_code == 200 and r.get_json()["verdict"] == "pass",
           "AI 判不了 + 有意见 + 自己的结论 → 记 pass")

    # ---------- 状态回读 ----------
    d = c.get("/api/review/items/compare/cm:1:1").get_json()
    got = {x["item"]: x["mark"] for x in d["items"] if x["mark"]}
    ck(it_pass["item"] in got, "表态能在检查项上回读")
    ck(got[it_pass["item"]]["stance"] == "hold", "回读到的是最新一次（hold），不是第一条")

    # ---------- 假记录不接受 ----------
    r = mark(dict(base, src="records", key="不存在的记录", stance="agree"))
    ck(r.status_code == 404,
       f"源库里没有的条目 → 404，不写空判断（实际 {r.status_code}：{r.get_json().get('error')}）")

    # ---------- 视图 ----------
    import sqlite3
    conn = sqlite3.connect(os.path.join(TMP, "qc.db"))
    conn.row_factory = sqlite3.Row
    n = conn.execute("SELECT COUNT(*) c FROM qc_judgement").fetchone()["c"]
    nlive = conn.execute(
        "SELECT COUNT(*) c FROM qc_judgement j WHERE NOT EXISTS "
        "(SELECT 1 FROM qc_judgement x WHERE x.supersedes_id = j.id)").fetchone()["c"]
    ck(n >= 4 and nlive < n, f"只追加：共 {n} 行，其中当前生效 {nlive} 行")
    va = conn.execute("SELECT * FROM v_agreement").fetchall()
    ck(len(va) >= 1, "v_agreement 能出数")
    ev = conn.execute("SELECT evidence_json FROM qc_judgement ORDER BY id DESC LIMIT 1").fetchone()[0]
    ck("ai_verdict" in (ev or ""), "evidence_json 内联了当时的 AI 结论（不靠以后 join）")
    bad = conn.execute("SELECT COUNT(*) c FROM qc_judgement WHERE stance='dispute' "
                       "AND dispute_reason IS NULL").fetchone()["c"]
    ck(bad == 0, "没有「有意见但没原因」的脏行")
    conn.close()

    # ---------- 飞书记录（整条） ----------
    d = c.get("/api/review/items/records/recvoy4YEneiac").get_json()
    ck(d["meta"]["has_detail"] is False, "飞书记录标记为「没有检查项明细」")
    ck(len(d["items"]) == 1 and d["items"][0]["item"] == "", "整条记录只有一条空 item 的表态单元")

    # ---------- 飞书面板那条路也落同一张表 ----------
    rid = "recvoy4YEneiac"
    r = c.post("/api/decide", json={"record_id": rid, "action": "确认", "actor": "测试"})
    ck(r.status_code == 200, f"面板「通过」仍然可用（{r.status_code}）")
    r = c.post("/api/decide", json={"record_id": rid, "action": "打回", "actor": "测试",
                                    "note": "图面注记与清单不符", "override": True})
    ck(r.status_code == 200, f"面板「退回」仍然可用（{r.status_code}）")

    conn = sqlite3.connect(os.path.join(TMP, "qc.db"))
    conn.row_factory = sqlite3.Row
    prows = conn.execute("SELECT * FROM qc_judgement WHERE src='records' AND src_key=?",
                         (rid,)).fetchall()
    ck(len(prows) == 2, f"面板两次动作都落到 qc_judgement（{len(prows)} 行）")
    live = [x for x in prows if x["id"] == max(y["id"] for y in prows)]
    ck(live[0]["stance"] == "dispute" and live[0]["note"] == "图面注记与清单不符",
       "面板退回记成「有意见」+ 手写理由，不是凭空补一个三选一")
    ck(json.loads(live[0]["evidence_json"]).get("via") == "panel",
       "面板来源在快照里标了 via=panel，能和分析口那条区分开")
    conn.close()

    print(f"\n{PASS} 通过 / {FAIL} 失败")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
