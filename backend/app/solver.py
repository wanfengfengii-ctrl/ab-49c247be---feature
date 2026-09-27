"""位移联合裁决核心（纯整数几何，零第三方依赖）。

输入（见 ``adjudicate`` 的 JSON 结构）：
  points     : [{"name"? , "x", "y"}]                 基准点，5~8 个，整数坐标
  triangles  : [[i, j, k], ...]                       已登记三角面（点索引）
  edges      : [{"endpoints": [i, j], "minSq", "maxSq"}]
  candidates : [[[dx, dy], ...], ...]                 每点 2~3 条整数候选位移

约束（硬约束，全部使用整数运算）：
  1. 每点恰选一条候选位移；
  2. 每个三角面位移后的有向面积（两倍）与原基线严格同号——保持严格朝向；
  3. 每条登记边的长度平方落在闭区间 [minSq, maxSq]；
  4. 任意两条不共享端点的边不得相交，亦不得在端点/延长方向上相切。

优化目标（字典序）：
  (a) 各点位移平方的最大值最小；
  (b) 位移平方和最小；
  (c) 按点录入顺序的候选序号字典序最小（序号从 0 起）。

无解时返回 ``feasible=False`` 及确定性的首个违约证据：按候选序号字典序
枚举第一种组合，并在该组合内按 朝向 → 边长 → 边界相交 的顺序取首个失败项。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any


class AdjudicationError(ValueError):
    """录入草稿不合法（区别于几何上不可行）。"""


# ---------------------------------------------------------------- 基础工具

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _cross(ax: int, ay: int, bx: int, by: int) -> int:
    return ax * by - ay * bx


def _dot(ax: int, ay: int, bx: int, by: int) -> int:
    return ax * bx + ay * by


@dataclass(frozen=True)
class _SegHit:
    intersect: bool
    touching: bool  # 交集为单点（端点相接）时为 True；重叠共线为 False


def _segment_hit(a: tuple[int, int], b: tuple[int, int],
                 c: tuple[int, int], d: tuple[int, int]) -> _SegHit:
    """整数判定线段 AB 与 CD 是否相交或相切。

    返回 touching=True 表示交集恰为一个点（端点接触或 T 字相接），
    touching=False 表示存在正向重叠；不相交时 intersect=False。
    """
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    qpx, qpy = c[0] - a[0], c[1] - a[1]
    rxs = _cross(rx, ry, sx, sy)
    qpxr = _cross(qpx, qpy, rx, ry)

    if rxs != 0:
        # 两线不平行：t = cross(QP, s) / rxs，u = cross(QP, r) / rxs
        num_t = _cross(qpx, qpy, sx, sy)
        num_u = qpxr
        if rxs > 0:
            inside = 0 <= num_t <= rxs and 0 <= num_u <= rxs
            boundary = num_t == 0 or num_t == rxs or num_u == 0 or num_u == rxs
        else:
            inside = rxs <= num_t <= 0 and rxs <= num_u <= 0
            boundary = (num_t == 0 or num_t == rxs
                        or num_u == 0 or num_u == rxs)
        if not inside:
            return _SegHit(False, False)
        return _SegHit(True, boundary)

    # 平行：不共线则无交集（r 为零向量时改用 s 判定共线）
    if qpxr != 0:
        return _SegHit(False, False)

    rr = _dot(rx, ry, rx, ry)
    ss = _dot(sx, sy, sx, sy)
    if rr == 0 and ss == 0:
        # 两条都是退化点段：仅当两点重合时相切
        return _SegHit(a == c, a == c)
    if rr == 0:
        # AB 退化为点 A：判定 A 是否落在 CD 上
        if _cross(qpx, qpy, sx, sy) != 0:
            return _SegHit(False, False)
        u = _dot(-qpx, -qpy, sx, sy)  # A 在 CD 上的投影参数（乘 ss）
        inside = 0 <= u <= ss
        return _SegHit(inside, inside)

    # 共线且 AB 非退化：沿 r 方向投影
    if _cross(qpx, qpy, sx, sy) != 0:
        return _SegHit(False, False)
    t0 = _dot(qpx, qpy, rx, ry)          # C 在 AB 上的投影参数（乘 rr）
    t1 = _dot(d[0] - a[0], d[1] - a[1], rx, ry)
    lo, hi = (t0, t1) if t0 <= t1 else (t1, t0)
    overlap_lo, overlap_hi = max(lo, 0), min(hi, rr)
    if overlap_hi < overlap_lo:
        return _SegHit(False, False)
    # 交集为单点即视为相切（CD 为退化点段时同样落到这里）
    return _SegHit(True, overlap_lo == overlap_hi)


# ---------------------------------------------------------------- 录入校验

def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise AdjudicationError(msg)


def _validate(data: Any) -> tuple[
        list[dict[str, Any]], list[list[int]], list[dict[str, Any]],
        list[list[list[int]]]]:
    _require(isinstance(data, dict), "请求体必须是 JSON 对象")

    raw_points = data.get("points")
    _require(isinstance(raw_points, list) and 5 <= len(raw_points) <= 8,
             "基准点数量必须在 5 至 8 个之间")
    points: list[dict[str, Any]] = []
    for i, p in enumerate(raw_points):
        _require(isinstance(p, dict), f"第 {i + 1} 个基准点格式不正确")
        x, y = p.get("x"), p.get("y")
        _require(_is_int(x) and _is_int(y),
                 f"基准点 {i + 1} 的 x/y 必须为整数")
        name = p.get("name") or f"P{i + 1}"
        _require(isinstance(name, str) and name.strip() != "",
                 f"基准点 {i + 1} 的名称不能为空")
        points.append({"name": name.strip(), "x": x, "y": y})

    n = len(points)

    raw_tris = data.get("triangles")
    _require(isinstance(raw_tris, list) and len(raw_tris) >= 1,
             "至少需要登记一个三角面")
    triangles: list[list[int]] = []
    seen_tri: set[tuple[int, int, int]] = set()
    for i, t in enumerate(raw_tris):
        _require(isinstance(t, list) and len(t) == 3
                 and all(_is_int(v) for v in t),
                 f"第 {i + 1} 个三角面必须是三个整数点索引")
        a, b, c = (int(v) for v in t)
        _require(0 <= a < n and 0 <= b < n and 0 <= c < n,
                 f"三角面 {i + 1} 存在越界点索引")
        _require(len({a, b, c}) == 3,
                 f"三角面 {i + 1} 的三个顶点必须互不相同")
        key = tuple(sorted((a, b, c)))
        _require(key not in seen_tri, f"三角面 {i + 1} 与先前条目重复")
        seen_tri.add(key)
        triangles.append([a, b, c])

    raw_edges = data.get("edges")
    _require(isinstance(raw_edges, list) and len(raw_edges) >= 1,
             "至少需要登记一条边")
    edges: list[dict[str, Any]] = []
    seen_edge: set[frozenset[int]] = set()
    for i, e in enumerate(raw_edges):
        _require(isinstance(e, dict), f"第 {i + 1} 条边格式不正确")
        ep = e.get("endpoints")
        _require(isinstance(ep, list) and len(ep) == 2
                 and all(_is_int(v) for v in ep),
                 f"边 {i + 1} 的 endpoints 必须是两个整数点索引")
        u, v = int(ep[0]), int(ep[1])
        _require(0 <= u < n and 0 <= v < n, f"边 {i + 1} 存在越界点索引")
        _require(u != v, f"边 {i + 1} 的两个端点必须不同")
        key = frozenset((u, v))
        _require(key not in seen_edge, f"边 {i + 1} 与先前条目重复")
        seen_edge.add(key)
        lo, hi = e.get("minSq"), e.get("maxSq")
        _require(_is_int(lo) and _is_int(hi),
                 f"边 {i + 1} 的 minSq/maxSq 必须为整数")
        _require(0 <= lo <= hi,
                 f"边 {i + 1} 的长度平方区间必须满足 0 ≤ minSq ≤ maxSq")
        edges.append({"endpoints": [u, v], "minSq": lo, "maxSq": hi})

    raw_cands = data.get("candidates")
    _require(isinstance(raw_cands, list) and len(raw_cands) == n,
             "candidates 必须为每个基准点提供一组候选位移")
    candidates: list[list[list[int]]] = []
    for i, group in enumerate(raw_cands):
        _require(isinstance(group, list) and 2 <= len(group) <= 3,
                 f"基准点 {points[i]['name']} 必须提供 2 至 3 条候选位移")
        cleaned: list[list[int]] = []
        seen_vec: set[tuple[int, int]] = set()
        for j, vec in enumerate(group):
            _require(isinstance(vec, list) and len(vec) == 2
                     and all(_is_int(w) for w in vec),
                     f"点 {points[i]['name']} 的第 {j + 1} 条候选位移 "
                     "必须是 [dx, dy] 整数对")
            dx, dy = int(vec[0]), int(vec[1])
            _require((dx, dy) not in seen_vec,
                     f"点 {points[i]['name']} 的候选位移 "
                     f"({dx}, {dy}) 重复")
            seen_vec.add((dx, dy))
            cleaned.append([dx, dy])
        candidates.append(cleaned)

    return points, triangles, edges, candidates


# ---------------------------------------------------------------- 主裁决

def _signed_area2(p: list[tuple[int, int]], tri: list[int]) -> int:
    a, b, c = tri
    return _cross(p[b][0] - p[a][0], p[b][1] - p[a][1],
                  p[c][0] - p[a][0], p[c][1] - p[a][1])


def adjudicate(data: Any) -> dict[str, Any]:
    """执行联合裁决，返回可直接序列化给前端的证据报告。"""
    points, triangles, edges, candidates = _validate(data)
    n = len(points)
    origin = [(p["x"], p["y"]) for p in points]

    base_area = [_signed_area2(origin, t) for t in triangles]
    for i, area in enumerate(base_area):
        if area == 0:
            raise AdjudicationError(
                f"原基线第 {i + 1} 个三角面退化（有向面积为 0），"
                "无法定义严格朝向")

    # 非共端边对，按边录入顺序确定证据次序
    edge_pairs: list[tuple[int, int]] = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            ei, ej = edges[i]["endpoints"], edges[j]["endpoints"]
            if not ({ei[0], ei[1]} & {ej[0], ej[1]}):
                edge_pairs.append((i, j))

    ranges = [range(len(g)) for g in candidates]
    best: tuple[tuple[int, int, tuple[int, ...]], list[int]] | None = None
    first_violation: dict[str, Any] | None = None
    tried = 0

    for choice in itertools.product(*ranges):
        tried += 1
        moved = [
            (origin[i][0] + candidates[i][choice[i]][0],
             origin[i][1] + candidates[i][choice[i]][1])
            for i in range(n)
        ]
        violation: dict[str, Any] | None = None

        # 1) 严格朝向
        for ti, tri in enumerate(triangles):
            area = _signed_area2(moved, tri)
            if area * base_area[ti] <= 0:
                violation = {
                    "kind": "orientation",
                    "order": (0, ti),
                    "triangleIndex": ti,
                    "vertices": tri,
                    "originalSignedArea2": base_area[ti],
                    "actualSignedArea2": area,
                    "message": _orientation_message(ti, tri, points,
                                                     base_area[ti], area),
                }
                break

        # 2) 边长平方闭区间
        edge_len: list[int] | None = None
        if violation is None:
            edge_len = []
            for ei, e in enumerate(edges):
                u, v = e["endpoints"]
                d2 = ((moved[u][0] - moved[v][0]) ** 2
                      + (moved[u][1] - moved[v][1]) ** 2)
                edge_len.append(d2)
                if d2 < e["minSq"] or d2 > e["maxSq"]:
                    side = "小于下限" if d2 < e["minSq"] else "大于上限"
                    violation = {
                        "kind": "length",
                        "order": (1, ei),
                        "edgeIndex": ei,
                        "endpoints": [u, v],
                        "minSq": e["minSq"],
                        "maxSq": e["maxSq"],
                        "actualSq": d2,
                        "side": side,
                        "message": (
                            f"边 {points[u]['name']}–{points[v]['name']} "
                            f"实际长度平方 {d2} {side}（闭区间 "
                            f"[{e['minSq']}, {e['maxSq']}]）"),
                    }
                    break

        # 3) 非共端边相交/相切
        conflicts: list[dict[str, Any]] = []
        if violation is None:
            assert edge_len is not None
            for pair_order, (ei, ej) in enumerate(edge_pairs):
                a = edges[ei]["endpoints"]
                b = edges[ej]["endpoints"]
                hit = _segment_hit(moved[a[0]], moved[a[1]],
                                   moved[b[0]], moved[b[1]])
                if hit.intersect:
                    conflicts.append({
                        "edgeA": ei,
                        "edgeB": ej,
                        "endpointsA": a,
                        "endpointsB": b,
                        "touching": hit.touching,
                    })
                    if violation is None:
                        kind = "相切（单点接触）" if hit.touching else "相交"
                        an = edges[ei]["endpoints"]
                        bn = edges[ej]["endpoints"]
                        violation = {
                            "kind": "crossing",
                            "order": (2, pair_order),
                            "edgeA": ei,
                            "edgeB": ej,
                            "endpointsA": an,
                            "endpointsB": bn,
                            "touching": hit.touching,
                            "message": (
                                f"不共享端点的边 "
                                f"{points[an[0]]['name']}–"
                                f"{points[an[1]]['name']} 与 "
                                f"{points[bn[0]]['name']}–"
                                f"{points[bn[1]]['name']}{kind}，"
                                "缆线不得交叉或相切"),
                        }

        if violation is not None:
            if first_violation is None:
                first_violation = {
                    **violation,
                    "choice": list(choice),
                    "movedPoints": [[x, y] for x, y in moved],
                }
            continue

        # 可行组合：按三级目标择优
        disp_sq = [
            candidates[i][choice[i]][0] ** 2
            + candidates[i][choice[i]][1] ** 2
            for i in range(n)
        ]
        key = (max(disp_sq), sum(disp_sq), choice)
        if best is None or key < best[0]:
            best = (key, list(choice))

    return _build_report(points, triangles, edges, candidates, origin,
                         base_area, edge_pairs, best, first_violation, tried)


def _orientation_message(ti: int, tri: list[int],
                         points: list[dict[str, Any]],
                         original: int, actual: int) -> str:
    names = "、".join(points[v]["name"] for v in tri)
    if actual == 0:
        state = "退化为共线（有向面积为 0）"
    elif actual * original < 0:
        state = "朝向翻转"
    else:
        state = "朝向未保持"
    return (f"三角面 {ti + 1}（{names}）位移后{state}：两倍有向面积 "
            f"{original} → {actual}，须与原基线严格同号")


def _build_report(points, triangles, edges, candidates, origin,
                  base_area, edge_pairs, best, first_violation, tried) -> dict:
    def assignment_entries(choice: list[int]) -> list[dict[str, Any]]:
        out = []
        for i, ci in enumerate(choice):
            dx, dy = candidates[i][ci]
            out.append({
                "pointIndex": i,
                "pointName": points[i]["name"],
                "candidateIndex": ci,
                "displacement": [dx, dy],
                "displacementSq": dx * dx + dy * dy,
                "displaced": [origin[i][0] + dx, origin[i][1] + dy],
                "rejected": [[dx2, dy2]
                             for j, (dx2, dy2) in enumerate(candidates[i])
                             if j != ci],
            })
        return out

    def geometry_report(choice: list[int]) -> dict[str, Any]:
        moved = [
            (origin[i][0] + candidates[i][choice[i]][0],
             origin[i][1] + candidates[i][choice[i]][1])
            for i in range(len(points))
        ]
        edge_rows = []
        for ei, e in enumerate(edges):
            u, v = e["endpoints"]
            d2 = ((moved[u][0] - moved[v][0]) ** 2
                  + (moved[u][1] - moved[v][1]) ** 2)
            edge_rows.append({
                "edgeIndex": ei,
                "endpoints": [u, v],
                "minSq": e["minSq"],
                "maxSq": e["maxSq"],
                "actualSq": d2,
                "within": e["minSq"] <= d2 <= e["maxSq"],
            })
        tri_rows = []
        for ti, tri in enumerate(triangles):
            area = _signed_area2(moved, tri)
            tri_rows.append({
                "triangleIndex": ti,
                "vertices": tri,
                "originalSignedArea2": base_area[ti],
                "actualSignedArea2": area,
                "preserved": area * base_area[ti] > 0,
            })
        conflicts = []
        for ei, ej in edge_pairs:
            a = edges[ei]["endpoints"]
            b = edges[ej]["endpoints"]
            hit = _segment_hit(moved[a[0]], moved[a[1]],
                               moved[b[0]], moved[b[1]])
            if hit.intersect:
                conflicts.append({
                    "edgeA": ei, "edgeB": ej,
                    "endpointsA": a, "endpointsB": b,
                    "touching": hit.touching,
                })
        return {
            "movedPoints": [[x, y] for x, y in moved],
            "edges": edge_rows,
            "triangles": tri_rows,
            "conflicts": conflicts,
        }

    report: dict[str, Any] = {
        "feasible": best is not None,
        "triedCombinations": tried,
        "totalCombinations": 1,
        "points": [{"name": p["name"], "x": p["x"], "y": p["y"]}
                   for p in points],
        "candidateCount": [len(g) for g in candidates],
    }
    total = 1
    for g in candidates:
        total *= len(g)
    report["totalCombinations"] = total

    if best is not None:
        (max_sq, sum_sq, choice_tuple), choice = best
        report["objective"] = {"maxSq": max_sq, "sumSq": sum_sq}
        report["choice"] = choice
        report["assignment"] = assignment_entries(choice)
        report.update(geometry_report(choice))
        report["firstViolation"] = None
    else:
        assert first_violation is not None
        choice = first_violation["choice"]
        report["objective"] = None
        report["choice"] = choice
        report["assignment"] = assignment_entries(choice)
        geo = geometry_report(choice)
        report.update(geo)
        report["firstViolation"] = first_violation
    return report
