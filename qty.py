# -*- coding: utf-8 -*-
"""工程量统计：dwg/dxf 按图层统计 + 清单对撞。

三条口径（和质检口径保持一致，别在这里另立一套）：

1. **图面值有两种取法，分开列，不合并**：
   - `注记值`：图上写出来的数字（`26673.0平方米`、`L=125.6m`），这是主判据；
   - `图层合计`：按图层把线长、闭合面积、点位个数加起来，这是辅助量。
   两者不一致时只写说明，**不判问题** —— 图框会吞注记、标注可能重复计，属常见现象。
2. **单位不一致就不比**。清单写「工日」、图面只有面积，一律 `无法对位`，不硬凑数字。
3. **差异只说明**。本模块不做合格/不合格判定，出的是「对得上/对不上/对不上多少」，
   判不判由人和质检模块决定。

dwg 不能直接读：走 ODA File Converter 转 dxf（本机已装 27.1.0）。没装就直接报错，
不要静默返回空表 —— 空表比报错危险，看的人会以为「图上真没有」。
"""
import math
import os
import re
import shutil
import subprocess
import difflib

import ezdxf
from ezdxf import recover

# ---------------------------------------------------------------- 单位
# $INSUNITS：0=未声明 1=英寸 2=英尺 4=毫米 5=厘米 6=米
INSUNITS = {
    0: (1.0, "未声明"),
    1: (0.0254, "英寸"),
    2: (0.3048, "英尺"),
    4: (0.001, "毫米"),
    5: (0.01, "厘米"),
    6: (1.0, "米"),
}

# 清单单位 → 内部单位。长度 m、面积 m2、个数 cnt、工时 day。
UNIT_MAP = {
    "m": "m", "米": "m", "延长米": "m", "延米": "m", "公尺": "m",
    "km": "m", "公里": "m", "千米": "m",
    "m2": "m2", "㎡": "m2", "平方米": "m2", "平方": "m2", "m²": "m2", "平米": "m2",
    "公顷": "m2", "亩": "m2",
    "个": "cnt", "点": "cnt", "处": "cnt", "座": "cnt", "条": "cnt", "幢": "cnt",
    "套": "cnt", "项": "cnt", "份": "cnt", "组": "cnt", "台": "cnt", "根": "cnt",
    "工日": "day", "人日": "day", "日": "day",
}
# 单位换算到基准（公顷/亩/公里不是 1:1）
UNIT_FACTOR = {"km": 1000.0, "公里": 1000.0, "千米": 1000.0,
               "公顷": 10000.0, "亩": 666.6667}


def norm_unit(u):
    """清单/注记上的单位写法 → 内部单位码。认不出来的返回 ''，由调用方标『无法对位』。"""
    s = (u or "").strip().lower().replace(" ", "")
    return UNIT_MAP.get(s, "")


def unit_factor(u):
    return UNIT_FACTOR.get((u or "").strip().lower(), 1.0)


UNIT_LABEL = {"m": "米", "m2": "平方米", "cnt": "个", "day": "工日"}

# ---------------------------------------------------------------- 文字注记里的数字
NOTE_RE = re.compile(
    r"([-+]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(平方米|平方|平米|㎡|m2|m²|米|m|公里|km)",
    re.I,
)
# 明确不要的数字：比例尺、图号、日期、标高（带正负号且两位小数的是高程，不是工程量）
SKIP_RE = re.compile(r"^(1:|比例|图号|日期|20\d\d|19\d\d)|标高|高程|\$|±")


def parse_notes(text):
    """一段文字里所有『数字+单位』。返回 [(value, unit_code, raw)]。"""
    out = []
    if not text or SKIP_RE.search(text):
        return out
    for m in NOTE_RE.finditer(text):
        raw_num, raw_unit = m.group(1), m.group(2)
        try:
            val = float(raw_num.replace(",", ""))
        except ValueError:
            continue
        u = norm_unit(raw_unit)
        if not u:
            continue
        out.append((val * unit_factor(raw_unit), u, m.group(0)))
    return out


# ---------------------------------------------------------------- 几何量
def _pts_length(pts, closed=False):
    pts = [(float(p[0]), float(p[1])) for p in pts]
    if len(pts) < 2:
        return 0.0
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
    if closed and len(pts) > 2:
        total += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
    return total


def _pts_area(pts):
    """鞋带公式。顶点少于 3 个返回 0。"""
    pts = [(float(p[0]), float(p[1])) for p in pts]
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def ent_geom(e):
    """单实体几何量 → (线长, 闭合面积, 是否闭合)。不认识的类型返回 (0,0,False)。

    为什么不用 e.length：ezdxf 只有部分类型提供，且单位跟着图纸走；
    这里统一按顶点算，遇到 ARC/CIRCLE/SPLINE 用 flattening 采样，精度 0.1 足够统计用。
    """
    t = e.dxftype()
    try:
        if t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            return math.hypot(b.x - a.x, b.y - a.y), 0.0, False
        if t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            closed = bool(e.closed) or (len(pts) > 2 and pts[0] == pts[-1])
            return _pts_length(pts, closed), (_pts_area(pts) if closed else 0.0), closed
        if t == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            closed = bool(e.is_closed) or (len(pts) > 2 and pts[0] == pts[-1])
            return _pts_length(pts, closed), (_pts_area(pts) if closed else 0.0), closed
        if t == "ARC":
            r = float(e.dxf.radius)
            a0, a1 = math.radians(float(e.dxf.start_angle)), math.radians(float(e.dxf.end_angle))
            sweep = (a1 - a0) % (2 * math.pi)
            return r * sweep, 0.0, False
        if t == "CIRCLE":
            r = float(e.dxf.radius)
            return 2 * math.pi * r, math.pi * r * r, True
        if t in ("SPLINE", "ELLIPSE"):
            pts = [(p.x, p.y) for p in e.flattening(0.1)]
            closed = len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-6
            return _pts_length(pts, closed), (_pts_area(pts) if closed else 0.0), closed
    except Exception:
        return 0.0, 0.0, False
    return 0.0, 0.0, False


def _block_name(e):
    """INSERT 的块名，去掉动态块前缀（`*U12` 这种是匿名块，没信息量）。"""
    try:
        n = e.dxf.name or ""
    except Exception:
        n = ""
    return "" if n.startswith("*") else n


# ---------------------------------------------------------------- dwg → dxf
ODA_CANDIDATES = [
    r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe",
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
]


def oda_path():
    p = os.environ.get("ODA_CONVERTER", "").strip()
    if p and os.path.isfile(p):
        return p
    for c in ODA_CANDIDATES:
        if os.path.isfile(c):
            return c
    return ""


def dwg_to_dxf(src, workdir):
    """dwg 转 dxf。输入输出都用目录（ODA 只认目录），转完按同名取回。"""
    exe = oda_path()
    if not exe:
        raise RuntimeError(
            "这台机器没有 ODA File Converter，读不了 dwg。"
            "请在 CAD 里另存为 dxf 再上传，或在服务机装 ODA File Converter 27。"
        )
    # 用 _conv/ 子目录：调用方的 workdir 里往往就有 in/ 装着上传件，
    # 同名目的会变成「自己拷给自己」，Windows 直接 WinError 32。
    ind = os.path.join(workdir, "_conv", "in")
    outd = os.path.join(workdir, "_conv", "out")
    os.makedirs(ind, exist_ok=True)
    os.makedirs(outd, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    dst_in = os.path.join(ind, base + ".dwg")
    if os.path.abspath(src) != os.path.abspath(dst_in):
        shutil.copy2(src, dst_in)
    for ex in (".dxf",):
        p = os.path.join(outd, base + ex)
        if os.path.isfile(p):
            os.remove(p)
    # 参数：输入目录 输出目录 版本 格式 递归 审计 [过滤]
    cmd = [exe, ind, outd, "ACAD2018", "DXF", "0", "1"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    out = os.path.join(outd, base + ".dxf")
    if not os.path.isfile(out):
        tail = (r.stdout or b"")[-300:].decode("utf-8", "replace")
        raise RuntimeError(f"dwg 转 dxf 失败（退出码 {r.returncode}）：{tail}")
    return out


def open_doc(path, workdir):
    """读图。readfile 失败就退回 recover —— 现场图常见轻微损坏，能救就救。"""
    p = path
    if p.lower().endswith(".dwg"):
        p = dwg_to_dxf(path, workdir)
    try:
        return ezdxf.readfile(p), ""
    except Exception as exc:
        try:
            doc, auditor = recover.readfile(p)
            return doc, f"图形有损坏，已修复读取（{type(exc).__name__}）"
        except Exception:
            raise RuntimeError(f"读不了这份图：{exc}")


# ---------------------------------------------------------------- 扫描
LAYER_KIND = {
    "LINE": "线", "LWPOLYLINE": "多段线", "POLYLINE": "多段线", "ARC": "弧",
    "CIRCLE": "圆", "SPLINE": "样条", "ELLIPSE": "椭圆", "INSERT": "块",
    "TEXT": "文字", "MTEXT": "文字", "DIMENSION": "标注", "HATCH": "填充",
    "POINT": "点", "LEADER": "引线", "MULTILEADER": "引线",
}

# 本部门图层的命名习惯就是「口径-名称」：`长度-雨水直埋φ200A型缠绕管`、`个数-雨水500×500砖砌井`。
# 这一层前缀比「按实体类型去猜」可靠得多，所以优先用它定口径。
LAYER_PREFIX = {
    "长度": ("m", "长度"), "管长": ("m", "长度"), "线长": ("m", "长度"),
    "个数": ("cnt", "个数"), "数量": ("cnt", "个数"), "点位": ("cnt", "个数"),
    "面积": ("m2", "面积"), "范围": ("m2", "面积"),
}


def split_layer(layer):
    """`长度-雨水直埋φ200` → ('m','雨水直埋φ200')；认不出前缀返回 ('','原图层名')。"""
    s = (layer or "").strip()
    for sep in ("-", "_", "－", "—"):
        head, _, rest = s.partition(sep)
        if head in LAYER_PREFIX and rest.strip():
            return LAYER_PREFIX[head][0], rest.strip()
    for head, (unit, _label) in LAYER_PREFIX.items():
        if s.startswith(head) and len(s) > len(head):
            return unit, s[len(head):].lstrip("-_ －—")
    return "", s



def scan_dxf(path, workdir):
    """一份图 → 图层小计 + 文字注记 + 总量。所有长度按图纸单位换算成米。"""
    doc, warn = open_doc(path, workdir)
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    k, unit_name = INSUNITS.get(code, (1.0, "未声明"))
    layers = {}
    notes = []
    ent_total = 0

    for e in doc.modelspace():
        t = e.dxftype()
        lay = (e.dxf.get("layer", "0") or "0").strip()
        d = layers.setdefault(lay, {
            "layer": lay, "n": 0, "length": 0.0, "area": 0.0,
            "kinds": {}, "blocks": {}, "notes": 0,
        })
        d["n"] += 1
        ent_total += 1
        kind = LAYER_KIND.get(t, t)
        d["kinds"][kind] = d["kinds"].get(kind, 0) + 1

        if t in ("TEXT", "MTEXT"):
            try:
                txt = (e.plain_text() if t == "MTEXT" else e.dxf.text) or ""
            except Exception:
                txt = ""
            txt = txt.strip()
            d["notes"] += 1
            for val, u, raw in parse_notes(txt):
                notes.append({
                    "layer": lay, "text": txt, "value": round(val, 3),
                    "unit": u, "unit_label": UNIT_LABEL.get(u, u), "raw": raw,
                })
            continue

        if t == "INSERT":
            bn = _block_name(e)
            if bn:
                d["blocks"][bn] = d["blocks"].get(bn, 0) + 1
            continue

        length, area, _closed = ent_geom(e)
        d["length"] += length
        d["area"] += area

    for d in layers.values():
        d["length_m"] = round(d["length"] * k, 3)
        d["area_m2"] = round(d["area"] * k * k, 3)
        d["main_kind"] = max(d["kinds"].items(), key=lambda kv: kv[1])[0] if d["kinds"] else "—"
        d["block_list"] = sorted(d["blocks"].items(), key=lambda kv: -kv[1])[:3]
        u, nm = split_layer(d["layer"])
        d["qty_unit"] = u
        d["qty_name"] = nm
        # 有前缀就以前缀为准；没前缀不硬猜，留给「按实体类型合计」那条路
        if u == "m":
            d["qty_value"] = d["length_m"]
        elif u == "m2":
            d["qty_value"] = d["area_m2"]
        elif u == "cnt":
            d["qty_value"] = d["n"]
        else:
            d["qty_value"] = None

    items = sorted(layers.values(), key=lambda d: (-d["n"], d["layer"]))
    return {
        "file": os.path.basename(path),
        "unit": unit_name,
        "warn": warn,
        "entities": ent_total,
        "layers": items,
        "notes": notes,
        "total_length_m": round(sum(d["length_m"] for d in items), 3),
        "total_area_m2": round(sum(d["area_m2"] for d in items), 3),
    }


# ---------------------------------------------------------------- 清单解析
HDR_KEYS = ("项目内容", "工程量", "数量", "单价", "金额", "序号", "单位")


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return ("%g" % v).strip()
    return str(v).strip()


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = _s(v).replace(",", "").replace("，", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_list(path):
    """工程量清单 → 表头信息 + 明细行。

    只认「项目内容 / 数量 / 单位」这套列；两级表头（工程量下面再分数量、单位）在本部门模板里很常见，
    所以表头行与子表头行要分别定位，不能一刀切按第一行当表头。
    """
    ext = os.path.splitext(path)[1].lower()
    rows_raw = []
    if ext == ".xls":
        import xlrd
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        for r in range(sh.nrows):
            rows_raw.append([sh.cell_value(r, c) for c in range(sh.ncols)])
    else:
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True)
        ws = wb[wb.sheetnames[0]]
        for row in ws.iter_rows(values_only=True):
            rows_raw.append(list(row))

    meta = {"title": "", "client": "", "project": "", "task": ""}
    head_i = -1
    cols = {}
    for i, row in enumerate(rows_raw[:15]):
        cells = [_s(c) for c in row]
        hits = sum(1 for c in cells if any(k == c or k in c for k in HDR_KEYS))
        if head_i < 0 and hits >= 3:
            head_i = i
            for j, c in enumerate(cells):
                if not c:
                    continue
                if "序号" in c and "seq" not in cols:
                    cols["seq"] = j
                elif "项目内容" in c or c == "项目" or "名称" in c:
                    cols.setdefault("name", j)
                elif "数量" in c or "工程量" in c:
                    cols.setdefault("qty", j)
                elif "单位" in c:
                    cols.setdefault("unit", j)
                elif "金额" in c:
                    cols.setdefault("amount", j)
        for c in cells:
            if c.startswith("项目名称"):
                meta["project"] = re.sub(r"^项目名称[:：]?", "", c).strip()
            elif c.startswith("委托单位"):
                meta["client"] = re.sub(r"^委托单位[:：]?", "", c).strip()
            elif c and not meta["title"] and ("清单" in c or "结算" in c):
                meta["title"] = c
    if head_i < 0 or "name" not in cols:
        raise RuntimeError("清单里找不到表头（要有『项目内容』『数量』『单位』这几列）")

    # 子表头：紧跟表头那一行若是「数量/单位」这种短词，把列位置挪过去。
    # 注意有两列都叫「单位」（工程量的单位、单价单位），取**数量列右边第一个**，
    # 取最后一个会拿到单价单位 —— 实测样本 XDL26109948 就是这么错位的。
    if head_i + 1 < len(rows_raw):
        sub = [_s(c) for c in rows_raw[head_i + 1]]
        if any(c in ("数量", "单位", "价格") for c in sub):
            if "数量" in sub:
                cols["qty"] = sub.index("数量")
            qcols = [j for j, c in enumerate(sub) if c == "单位" and j > cols.get("qty", -1)]
            if qcols:
                cols["unit"] = qcols[0]

    rows = []
    for i in range(head_i + 1, len(rows_raw)):
        cells = [_s(c) for c in rows_raw[i]]
        if not any(cells):
            continue
        first = cells[0] if cells else ""
        if any(k in "".join(cells) for k in ("合计", "总计")):
            break
        name = cells[cols["name"]] if cols["name"] < len(cells) else ""
        qty = _num(rows_raw[i][cols["qty"]]) if cols.get("qty", 99) < len(cells) else None
        unit = cells[cols["unit"]] if cols.get("unit", 99) < len(cells) else ""
        if not name or qty is None:
            continue
        if name in ("项目内容", "数量"):
            continue
        rows.append({
            "seq": first if re.match(r"^\d+(\.0)?$", first) else str(len(rows) + 1),
            "name": name,
            "qty": qty,
            "raw_unit": unit,
            "unit": norm_unit(unit),
            "unit_label": unit or UNIT_LABEL.get(norm_unit(unit), ""),
            "amount": _num(rows_raw[i][cols["amount"]]) if cols.get("amount", 99) < len(cells) else None,
        })
    if not rows:
        raise RuntimeError("清单表头找到了，但一行数据都没解析出来（检查是不是合并单元格）")

    # 任务号常写在右上角或最后一行
    for row in rows_raw:
        for c in (_s(x) for x in row):
            m = re.search(r"\b(XDL\d+|GC\d+|WLJC\d+|WLCJ\d+)\b", c)
            if m:
                meta["task"] = m.group(1)
                break
        if meta["task"]:
            break
    meta["file"] = os.path.basename(path)
    meta["rows"] = rows
    return meta


# ---------------------------------------------------------------- 对撞
# 名称里的硬词：出现在两边就算同类（测绘清单的措辞和图层名很少一字不差）
KEYWORDS = ("管线", "雨水", "污水", "给水", "排水", "燃气", "电力", "通信", "通信管",
            "道路", "路面", "围墙", "地块", "土方", "地形", "高程", "控制点", "界址",
            "房屋", "建筑", "绿化", "河道", "塘", "桥", "管", "点", "长度", "面积",
            "剥离", "耕作层", "竣工", "放样", "沉降", "勘探", "钻孔", "断面")


def _ngrams(s, n=2):
    s = re.sub(r"[\s（）()【】\[\]、,，.。/\\-]", "", s or "")
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))} or {s}


def name_score(a, b):
    """两层相似度取大：字面序列比 + 关键字命中当量。只看语义不看数字。"""
    a2, b2 = re.sub(r"\d+", "", a), re.sub(r"\d+", "", b)
    ratio = difflib.SequenceMatcher(None, a2, b2).ratio()
    na, nb = _ngrams(a2), _ngrams(b2)
    jac = len(na & nb) / max(1, len(na | nb))
    kw = sum(1 for k in KEYWORDS if k in a2 and k in b2)
    return max(ratio, jac, min(0.9, 0.32 * kw))


def quantity_groups(scans):
    """把「长度-/个数-/面积-」这类图层按名称并起来，得到可对撞的工程量清单。

    为什么按名称并、不按实体类型并：同一根管在图上可能被切成 30 段（LINE + LWPOLYLINE 混排），
    按实体类型统计出来的是「段数」，不是工程量。图层名才是人写下的口径。

    没有前缀的图层不硬凑：按主要实体类型推一个口径，标 `inferred=True`。
    图框、0 层、Defpoints 这些整张图都有的层直接跳过 —— 推出来只会是噪音。
    """
    groups = {}
    for s in scans:
        for d in s["layers"]:
            u = d.get("qty_unit") or ""
            val = d.get("qty_value")
            inferred = False
            if u not in ("m", "m2", "cnt") or not val:
                u, val, inferred = _infer_qty(d)
                if not u:
                    continue
            key = (u, d["qty_name"])
            g = groups.setdefault(key, {"unit": u, "name": d["qty_name"], "value": 0.0,
                                        "inferred": inferred, "layers": [], "files": []})
            g["value"] += float(val)
            g["inferred"] = g["inferred"] and inferred
            g["layers"].append(d["layer"])
            if s["file"] not in g["files"]:
                g["files"].append(s["file"])
    for g in groups.values():
        g["value"] = round(g["value"], 3)
        g["unit_label"] = UNIT_LABEL.get(g["unit"], g["unit"])
    return sorted(groups.values(), key=lambda g: (-g["value"]))


# 整图通用层，推口径只会制造噪音
NOISE_LAYER = ("0", "defpoints", "图框", "tk", "标题栏", "边框", "打印", "视图")


def _infer_qty(d):
    """无前缀图层 → 按主要实体类型推 (单位, 值, 是否推断)。推不出返回 ('', 0, False)。"""
    name = (d.get("layer") or "").strip().lower()
    if name in NOISE_LAYER or any(k in name for k in NOISE_LAYER if len(k) > 1):
        return "", 0.0, False
    kind = d.get("main_kind") or ""
    if kind in ("线", "多段线", "弧", "样条", "椭圆") and d.get("length_m"):
        return "m", d["length_m"], True
    if kind in ("多段线", "圆", "样条", "椭圆") and d.get("area_m2"):
        return "m2", d["area_m2"], True
    if kind in ("块", "点"):
        return "cnt", d["n"], True
    return "", 0.0, False


def compare(meta, scans, tol_pct=0.5):
    """清单每一行去图上找对位。

    取值优先级：① 同名图层合计（口径由图层前缀定）② 同单位文字注记 ③ 都不中就 `无法对位`。
    两个来源都有时都留着，界面上一并显示 —— 只写一个，看的人会以为另一个不存在。
    """
    groups = quantity_groups(scans)
    notes = [n for s in scans for n in s["notes"]]
    out = []
    for r in meta["rows"]:
        u = r["unit"]
        row = dict(r)
        row.update({"fig_layer": None, "fig_layer_src": "", "fig_layer_inferred": False,
                    "fig_note": None, "fig_note_src": "", "diff": None, "pct": None,
                    "status": "无法对位", "note": ""})
        if not u:
            row["note"] = f"清单单位「{r['raw_unit']}」不在可对位的单位里（米/平方米/个/工日）"
            out.append(row)
            continue
        if u == "day":
            row["note"] = "工日与图面量纲不同，不可直比"
            out.append(row)
            continue

        # ① 同名图层合计。先用「图层名写了口径」的，不够像才退到推断口径
        def best_of(flag):
            c = [(name_score(r["name"], g["name"]), g) for g in groups
                 if g["unit"] == u and bool(g.get("inferred")) == flag]
            if not c:
                return None, 0.0
            s2, g2 = max(c, key=lambda t: (round(t[0], 3), t[1]["value"]))
            return g2, s2

        best_g, best_s = best_of(False)
        if not best_g or best_s < 0.5:
            g2, s2 = best_of(True)
            if g2 and s2 >= 0.5:
                best_g, best_s = g2, s2
        if best_g and best_s >= 0.5:
            row["fig_layer"] = best_g["value"]
            row["fig_layer_src"] = ("、".join(best_g["layers"][:3])
                                    + ("…" if len(best_g["layers"]) > 3 else ""))
            row["fig_layer_inferred"] = bool(best_g.get("inferred"))
            # 同类合计：同一张图里名字也像的几个图层加起来，给人一个上限参考
            same = [g["value"] for g in groups if g["unit"] == u
                    and name_score(r["name"], g["name"]) >= 0.5]
            if len(same) > 1:
                row["fig_layer_all"] = round(sum(same), 3)

        # ② 文字注记
        best_n, best_ns = None, 0.0
        for n in notes:
            if n["unit"] != u:
                continue
            s2 = name_score(r["name"], n["text"] + " " + n["layer"])
            if s2 > best_ns:
                best_n, best_ns = n, s2
        if best_n and best_ns >= 0.45:
            row["fig_note"] = best_n["value"]
            row["fig_note_src"] = f"{best_n['layer']} · {best_n['text'][:26]}"

        target = row["fig_layer"] if row["fig_layer"] is not None else row["fig_note"]
        if target is None:
            row["note"] = "图上既没有同名图层（长度-/个数-/面积- 前缀），也没有同单位注记"
            out.append(row)
            continue

        diff = float(target) - float(r["qty"])
        pct = (diff / r["qty"] * 100.0) if r["qty"] else None
        row["diff"] = round(diff, 3)
        row["pct"] = round(pct, 2) if pct is not None else None
        row["status"] = "一致" if pct is not None and abs(pct) <= tol_pct else "差异"
        if row["status"] == "差异":
            row["note"] = (f"图面比清单{'多' if diff > 0 else '少'} {abs(diff):g}"
                           f"（{pct:+.2f}%）")
        else:
            row["note"] = f"在 {tol_pct}% 容差内"
        # 两个来源互相不一致时点出来，但不定性 —— 图框会吞注记，是常见现象
        if row["fig_layer"] is not None and row["fig_note"] is not None:
            sub = abs(row["fig_layer"] - row["fig_note"])
            base = max(abs(row["fig_layer"]), abs(row["fig_note"]), 1)
            if sub / base > 0.002:
                row["note"] += f"；注记 {row['fig_note']:g} 与图层合计 {row['fig_layer']:g} 不同，按图层合计取值"
        out.append(row)
    return out


# ---------------------------------------------------------------- 汇总 / 导出
def build(files, list_path, workdir, tol_pct=0.5):
    """一次统计的完整结果。files 是 dxf/dwg 路径列表（可空），list_path 可空。"""
    scans = [scan_dxf(f, workdir) for f in files]
    meta = parse_list(list_path) if list_path else {"rows": [], "file": "", "title": "",
                                                    "project": "", "client": "", "task": ""}
    diffs = compare(meta, scans, tol_pct) if scans and meta["rows"] else []
    return {
        "scans": scans,
        "groups": quantity_groups(scans),
        "list": {k: v for k, v in meta.items() if k != "rows"},
        "rows": diffs,
        "tol_pct": tol_pct,
        "totals": {
            "files": len(scans),
            "layers": sum(len(s["layers"]) for s in scans),
            "entities": sum(s["entities"] for s in scans),
            "length_m": round(sum(s["total_length_m"] for s in scans), 3),
            "area_m2": round(sum(s["total_area_m2"] for s in scans), 3),
            "notes": sum(len(s["notes"]) for s in scans),
            "list_rows": len(meta["rows"]),
            "一致": sum(1 for r in diffs if r["status"] == "一致"),
            "差异": sum(1 for r in diffs if r["status"] == "差异"),
            "无法对位": sum(1 for r in diffs if r["status"] == "无法对位"),
        },
    }


def export_xlsx(result, path):
    """三个 sheet：图层小计 / 清单对照 / 文字注记。列宽按内容估，不追求好看。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1C5294")

    def sheet(ws, title, headers, rows, widths):
        ws.title = title
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(horizontal="center", vertical="center")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"

    ws1 = wb.active
    rows1 = [[g["name"], g["unit_label"], g["value"], "推断" if g.get("inferred") else "图层名前缀",
              len(g["layers"]), "、".join(g["files"]), "、".join(g["layers"][:4])]
             for g in result.get("groups") or []]
    sheet(ws1, "工程量汇总",
          ["名称（图层前缀后）", "单位", "数量", "口径来源", "图层数", "来源文件", "图层"],
          rows1, [40, 8, 14, 12, 9, 30, 44])

    ws1b = wb.create_sheet()
    rows1b = []
    for s in result["scans"]:
        for d in s["layers"]:
            rows1b.append([s["file"], d["layer"], d["n"], d["main_kind"],
                           d["length_m"], d["area_m2"], d["notes"],
                           "、".join(f"{b}×{c}" for b, c in d.get("block_list") or [])])
    sheet(ws1b, "图层小计",
          ["文件", "图层", "实体数", "主要类型", "线长(米)", "闭合面积(平方米)", "文字数", "主要块"],
          rows1b, [26, 30, 9, 10, 13, 16, 9, 26])

    ws2 = wb.create_sheet()
    rows2 = [[r["seq"], r["name"], r["qty"], r["unit_label"],
              r.get("fig_note") if r.get("fig_note") is not None else "",
              r.get("fig_note_src") or "",
              r.get("fig_layer") if r.get("fig_layer") is not None else "",
              r.get("diff") if r.get("diff") is not None else "",
              r.get("pct") if r.get("pct") is not None else "",
              r["status"], r.get("note") or ""] for r in result["rows"]]
    sheet(ws2, "清单对照",
          ["序号", "项目内容", "清单数量", "单位", "图面注记", "注记来源", "图层合计",
           "差异", "差异%", "判定", "说明"],
          rows2, [6, 34, 12, 8, 12, 30, 12, 11, 9, 10, 34])

    ws3 = wb.create_sheet()
    rows3 = [[s["file"], n["layer"], n["text"][:60], n["value"], n["unit_label"]]
             for s in result["scans"] for n in s["notes"]]
    sheet(ws3, "文字注记", ["文件", "图层", "文字", "数值", "单位"], rows3, [26, 22, 60, 12, 8])

    wb.save(path)
    return path
