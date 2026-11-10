# -*- coding: utf-8 -*-
"""工程量统计内核自测：真图、真清单、真导出。不起服务。

覆盖 18 项：图层前缀识别、单位归一、按图层统计（实体/线长/面积）、文字注记提取、
清单表头与两级列定位、对撞三种出口（一致 / 差异 / 无法对位）、工日不可直比、
无表头报错、导出四个 sheet、dwg 自动转 dxf。

用法：python _selftest_qty.py
"""
import os
import sys
import tempfile

from openpyxl import Workbook, load_workbook

import qty

ok = fail = 0
TMP = tempfile.mkdtemp(prefix="qtytest_")
DXF = r"E:\XDLFC\管线标注-样本\滨海雨水_原始.dxf"
DWG = r"E:\质检skill\_dwg_recover\WLJC26028569-GC001\城南西山标高.dwg"
XLS = r"E:\质检skill\_qc_sample\XDL26109948结算清单.xls"


def check(label, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}" + (f"  ({extra})" if extra else ""))
    else:
        fail += 1
        print(f"  FAIL  {label}  {extra}")


def xlsx(rows, path):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def main():
    print("\n[1] 口径与单位")
    check("长度前缀", qty.split_layer("长度-雨水直埋φ200A型缠绕管") == ("m", "雨水直埋φ200A型缠绕管"))
    check("个数前缀", qty.split_layer("个数-雨水500×500砖砌井") == ("cnt", "雨水500×500砖砌井"))
    check("面积前缀", qty.split_layer("面积-实测范围") == ("m2", "实测范围"))
    check("无前缀原样返回", qty.split_layer("GCD") == ("", "GCD"))
    check("㎡ 归一", qty.norm_unit("㎡") == "m2" and qty.norm_unit("平方米") == "m2")
    check("米 归一", qty.norm_unit("米") == "m" and qty.norm_unit("m") == "m")
    check("工日独立成类", qty.norm_unit("工日") == "day")
    check("公顷换算", qty.unit_factor("公顷") == 10000.0)

    print("\n[2] 按图层统计（真图）")
    s = qty.scan_dxf(DXF, TMP)
    check("实体 1590", s["entities"] == 1590, str(s["entities"]))
    check("图层 15", len(s["layers"]) == 15, str(len(s["layers"])))
    check("总长 2878.588", abs(s["total_length_m"] - 2878.588) < 0.01, str(s["total_length_m"]))
    layers = {d["layer"]: d for d in s["layers"]}
    k = "长度-雨水直埋φ200A型缠绕管"
    check("该层按口径记长度", layers[k]["qty_unit"] == "m" and layers[k]["qty_value"] > 1300,
          str(layers[k]["qty_value"]))
    check("块数统计", layers["个数-雨水500×500砖砌井"]["n"] == 111, str(layers["个数-雨水500×500砖砌井"]["n"]))

    print("\n[3] 清单解析（真清单，两级表头）")
    m = qty.parse_list(XLS)
    check("解析出 1 行", len(m["rows"]) == 1, str(len(m["rows"])))
    r0 = m["rows"][0]
    check("数量 25230.77", abs(r0["qty"] - 25230.77) < 0.01, str(r0["qty"]))
    check("单位 ㎡ → m2（不能取到单价单位）", r0["unit"] == "m2", r0["raw_unit"])
    check("任务号 XDL26109948", m["task"] == "XDL26109948", m["task"])

    print("\n[4] 对撞三出口")
    res = qty.build([DXF], XLS, TMP)
    row = res["rows"][0]
    check("图里没有面积层 → 无法对位", row["status"] == "无法对位", row["status"])
    check("无法对位要写清原因", "没有同名图层" in row["note"], row["note"][:30])
    check("工程量按图层前缀分组", any(g["name"].startswith("雨水直埋") and g["unit"] == "m"
                                     for g in res["groups"]))

    # 一致：图和注记对得上（用带注记的 dwg）
    if os.path.isfile(DWG) and qty.oda_path():
        res2 = qty.build([DWG], None, TMP)
        notes = res2["scans"][0]["notes"]
        check("dwg 自动转 dxf 并抓到注记", len(notes) == 2, str(len(notes)))
        v = next(n["value"] for n in notes if "S1" in n["text"])
        lst = xlsx([["工程量清单"], ["项目名称：自测"],
                    ["序号", "日期", "项目内容", "工程量", "", "收费单价", "", "金额（元）", "任务编号"],
                    ["", "", "", "数量", "单位", "价格", "单位", "", ""],
                    [1, "2026年11月9日", "实测范围面积S1", v, "㎡", "", "", "", "XDL00000000"]],
                   os.path.join(TMP, "selftest_list.xlsx"))
        res3 = qty.build([DWG], lst, TMP)
        r3 = res3["rows"][0]
        check("注记与清单一致 → 判定一致", r3["status"] == "一致", f"{r3['status']} {r3.get('note')}")
        check("差异值为 0", r3["diff"] == 0, str(r3["diff"]))
    else:
        check("dwg 用例跳过（缺样本或缺 ODA）", True, "skip")

    # 工日不可直比
    lst2 = xlsx([["结算清单"], ["序号", "项目内容", "数量", "单位"], [1, "外业工日", 12, "工日"]],
                os.path.join(TMP, "day.xlsx"))
    res4 = qty.build([DXF], lst2, TMP)
    check("工日 → 无法对位且说明量纲", res4["rows"][0]["status"] == "无法对位"
          and "量纲" in res4["rows"][0]["note"], res4["rows"][0]["note"][:20])

    print("\n[5] 异常与导出")
    bad = xlsx([["随便什么"], ["没有表头"]], os.path.join(TMP, "bad.xlsx"))
    try:
        qty.parse_list(bad)
        check("无表头要报错", False)
    except RuntimeError as e:
        check("无表头报错且说清缺什么", "表头" in str(e), str(e)[:30])

    out = os.path.join(TMP, "out.xlsx")
    qty.export_xlsx(res, out)
    wb = load_workbook(out)
    check("导出四个 sheet", wb.sheetnames == ["工程量汇总", "图层小计", "清单对照", "文字注记"],
          str(wb.sheetnames))
    check("汇总表有数据", wb["工程量汇总"].max_row > 1, str(wb["工程量汇总"].max_row))
    check("对照表带判定列", any(c.value == "判定" for c in wb["清单对照"][1]))

    print(f"\n合计 {ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
