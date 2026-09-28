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

可选的「分段落位复核」（请求体 ``"stagingReview": true``）：
在静态可行组合之上，进一步要求存在一条逐点落位顺序，使每个中间混合网
（已落位点取采用位移、未落位点仍在基线）同样满足全部三类硬约束。联合裁决
在 *候选位移 × 落位顺序* 的完整组合上比较：先沿用三级静态目标
（最大位移平方 → 平方和 → 候选序号字典序），再比较逐步位移峰值（各前缀
位移平方最大值）向量与点序列字典序；绝不会先选静态方案再事后试排。
无安全次序时稳定给出最早受阻步骤、已落位集合、各尝试点候选与首条违约。
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


def _staging_flag(data: Any) -> bool:
    if not isinstance(data, dict) or "stagingReview" not in data:
        return False
    flag = data["stagingReview"]
    if not isinstance(flag, bool):
        raise AdjudicationError("stagingReview 必须为布尔值（true/false）")
    return flag


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


def _orientation_violation(ti: int, tri: list[int],
                           points: list[dict[str, Any]],
                           base_area: int, area: int) -> dict[str, Any]:
    return {
        "kind": "orientation",
        "order": (0, ti),
        "triangleIndex": ti,
        "vertices": tri,
        "originalSignedArea2": base_area,
        "actualSignedArea2": area,
        "message": _orientation_message(ti, tri, points, base_area, area),
    }


def _length_violation(ei: int, e: dict[str, Any],
                      points: list[dict[str, Any]], d2: int) -> dict[str, Any]:
    u, v = e["endpoints"]
    side = "小于下限" if d2 < e["minSq"] else "大于上限"
    return {
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


def _crossing_violation(pair_order: int, ei: int, ej: int,
                        a: list[int], b: list[int],
                        points: list[dict[str, Any]],
                        touching: bool) -> dict[str, Any]:
    kind = "相切（单点接触）" if touching else "相交"
    return {
        "kind": "crossing",
        "order": (2, pair_order),
        "edgeA": ei,
        "edgeB": ej,
        "endpointsA": a,
        "endpointsB": b,
        "touching": touching,
        "message": (
            f"不共享端点的边 "
            f"{points[a[0]]['name']}–{points[a[1]]['name']} 与 "
            f"{points[b[0]]['name']}–{points[b[1]]['name']}{kind}，"
            "缆线不得交叉或相切"),
    }


def _first_constraint_violation(
        points: list[dict[str, Any]],
        triangles: list[list[int]],
        edges: list[dict[str, Any]],
        edge_pairs: list[tuple[int, int]],
        base_area: list[int],
        pos: list[tuple[int, int]]) -> dict[str, Any] | None:
    """在给定网点 ``pos`` 上按 朝向 → 边长 → 非共端边相交 取首条违约。

    全部整数运算；无违约返回 None。
    """
    # 1) 严格朝向
    for ti, tri in enumerate(triangles):
        area = _signed_area2(pos, tri)
        if area * base_area[ti] <= 0:
            return _orientation_violation(ti, tri, points,
                                          base_area[ti], area)

    # 2) 边长平方闭区间
    for ei, e in enumerate(edges):
        u, v = e["endpoints"]
        d2 = ((pos[u][0] - pos[v][0]) ** 2
              + (pos[u][1] - pos[v][1]) ** 2)
        if d2 < e["minSq"] or d2 > e["maxSq"]:
            return _length_violation(ei, e, points, d2)

    # 3) 非共端边相交/相切
    for pair_order, (ei, ej) in enumerate(edge_pairs):
        a = edges[ei]["endpoints"]
        b = edges[ej]["endpoints"]
        hit = _segment_hit(pos[a[0]], pos[a[1]],
                           pos[b[0]], pos[b[1]])
        if hit.intersect:
            return _crossing_violation(pair_order, ei, ej, a, b,
                                       points, hit.touching)
    return None


def _conflicts_at(edges: list[dict[str, Any]],
                  edge_pairs: list[tuple[int, int]],
                  pos: list[tuple[int, int]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ei, ej in edge_pairs:
        a = edges[ei]["endpoints"]
        b = edges[ej]["endpoints"]
        hit = _segment_hit(pos[a[0]], pos[a[1]],
                           pos[b[0]], pos[b[1]])
        if hit.intersect:
            out.append({
                "edgeA": ei, "edgeB": ej,
                "endpointsA": a, "endpointsB": b,
                "touching": hit.touching,
            })
    return out


def _stage_step_evidence(
        points: list[dict[str, Any]],
        triangles: list[list[int]],
        edges: list[dict[str, Any]],
        edge_pairs: list[tuple[int, int]],
        base_area: list[int],
        origin: list[tuple[int, int]],
        disp: list[tuple[int, int]],
        prev: list[tuple[int, int]],
        pos: list[tuple[int, int]],
        placed: list[int],
        peak: int,
        step_no: int,
        p: int,
        candidate_index: int) -> dict[str, Any]:
    """构造第 ``step_no`` 步落位点 ``p`` 的精确证据（该步已确认安全）。"""
    dx, dy = disp[p]
    tri_rows = []
    for ti, tri in enumerate(triangles):
        if p not in tri:
            continue
        before = _signed_area2(prev, tri)
        after = _signed_area2(pos, tri)
        tri_rows.append({
            "triangleIndex": ti,
            "vertices": tri,
            "originalSignedArea2": base_area[ti],
            "previousSignedArea2": before,
            "actualSignedArea2": after,
            "preserved": after * base_area[ti] > 0,
        })
    edge_rows = []
    for ei, e in enumerate(edges):
        u, v = e["endpoints"]
        if p != u and p != v:
            continue
        before = ((prev[u][0] - prev[v][0]) ** 2
                  + (prev[u][1] - prev[v][1]) ** 2)
        after = ((pos[u][0] - pos[v][0]) ** 2
                 + (pos[u][1] - pos[v][1]) ** 2)
        edge_rows.append({
            "edgeIndex": ei,
            "endpoints": [u, v],
            "minSq": e["minSq"],
            "maxSq": e["maxSq"],
            "previousSq": before,
            "actualSq": after,
            "within": e["minSq"] <= after <= e["maxSq"],
        })
    # 移动 p 只会改变「某条边以 p 为端点」的非共端边对状态
    cross_checks = []
    for ei, ej in edge_pairs:
        a = edges[ei]["endpoints"]
        b = edges[ej]["endpoints"]
        if p not in a and p not in b:
            continue
        hit = _segment_hit(pos[a[0]], pos[a[1]],
                           pos[b[0]], pos[b[1]])
        cross_checks.append({
            "edgeA": ei, "edgeB": ej,
            "endpointsA": a, "endpointsB": b,
            "intersect": hit.intersect,
            "touching": hit.touching if hit.intersect else False,
        })
    return {
        "step": step_no,
        "pointIndex": p,
        "pointName": points[p]["name"],
        "candidateIndex": candidate_index,
        "displacement": [dx, dy],
        "displacementSq": dx * dx + dy * dy,
        "from": [origin[p][0], origin[p][1]],
        "to": [origin[p][0] + dx, origin[p][1] + dy],
        "affectedTriangles": tri_rows,
        "affectedEdges": edge_rows,
        "crossChecks": cross_checks,
        "placed": list(placed),
        "movedPoints": [[x, y] for x, y in pos],
        "cumulativePeakSq": peak,
    }


def _expand_signatures_to_masks(valid_sigs: int,
                                members: list[int], n: int) -> int:
    """把约束成员签名空间（m≤4 位）中的「有效签名位集」展开为 2ⁿ 掩码位集。

    返回整数的第 M 位为 1 当且仅当已落位集合 M 投到该约束成员位上得到的
    签名有效；非成员点是否落位与该约束无关，故沿这些维度整维展开。
    """
    out = 0
    for mask in range(1 << n):
        sig = 0
        for k, p in enumerate(members):
            sig |= ((mask >> p) & 1) << k
        if (valid_sigs >> sig) & 1:
            out |= 1 << mask
    return out


def _staging_constraint_bitsets(
        triangles: list[list[int]],
        edges: list[dict[str, Any]],
        edge_pairs: list[tuple[int, int]],
        base_area: list[int],
        origin: list[tuple[int, int]],
        target: list[tuple[int, int]],
        choice: list[int],
        n: int,
        cache: dict[Any, Any]) -> tuple[list[int], list[int], list[int]]:
    """每条约束给出一个 2ⁿ 位整数：第 M 位为 1 表示已落位集合 M 上该约束成立。

    几何判定只与「约束 + 各成员所取候选序号」有关，跨组合整体缓存复用。
    """
    def pick(i: int, bit: int) -> tuple[int, int]:
        return target[i] if bit else origin[i]

    tri_bits: list[int] = []
    for ti, (a, b, c) in enumerate(triangles):
        key = ("tb", n, ti, choice[a], choice[b], choice[c])
        got = cache.get(key)
        if got is None:
            valid_sigs = 0
            for sig in range(8):
                pa = pick(a, sig & 1)
                pb = pick(b, (sig >> 1) & 1)
                pc = pick(c, (sig >> 2) & 1)
                area = _cross(pb[0] - pa[0], pb[1] - pa[1],
                              pc[0] - pa[0], pc[1] - pa[1])
                if area * base_area[ti] > 0:
                    valid_sigs |= 1 << sig
            got = _expand_signatures_to_masks(valid_sigs, [a, b, c], n)
            cache[key] = got
        tri_bits.append(got)

    edge_bits: list[int] = []
    for ei, e in enumerate(edges):
        u, v = e["endpoints"]
        key = ("eb", n, ei, choice[u], choice[v])
        got = cache.get(key)
        if got is None:
            valid_sigs = 0
            for sig in range(4):
                pu = pick(u, sig & 1)
                pv = pick(v, (sig >> 1) & 1)
                d2 = ((pu[0] - pv[0]) ** 2 + (pu[1] - pv[1]) ** 2)
                if e["minSq"] <= d2 <= e["maxSq"]:
                    valid_sigs |= 1 << sig
            got = _expand_signatures_to_masks(valid_sigs, [u, v], n)
            cache[key] = got
        edge_bits.append(got)

    pair_bits: list[int] = []
    for pi, (ei, ej) in enumerate(edge_pairs):
        a = edges[ei]["endpoints"]
        b = edges[ej]["endpoints"]
        members = [a[0], a[1], b[0], b[1]]
        key = ("pb", n, pi, *(choice[p] for p in members))
        got = cache.get(key)
        if got is None:
            valid_sigs = 0
            for sig in range(16):
                pu = pick(members[0], sig & 1)
                pv = pick(members[1], (sig >> 1) & 1)
                pw = pick(members[2], (sig >> 2) & 1)
                px = pick(members[3], (sig >> 3) & 1)
                if not _segment_hit(pu, pv, pw, px).intersect:
                    valid_sigs |= 1 << sig
            got = _expand_signatures_to_masks(valid_sigs, members, n)
            cache[key] = got
        pair_bits.append(got)

    return tri_bits, edge_bits, pair_bits


def _member_patterns(n: int) -> list[int]:
    """patterns[p] 的第 M 位为 1 当且仅当掩码 M 含点 p（用于位并行转移）。"""
    size = 1 << n
    patterns = []
    for p in range(n):
        block = 1 << p
        ones = (1 << block) - 1
        pat = 0
        for start in range(block, size, 2 * block):
            pat |= ones << start
        patterns.append(pat)
    return patterns


def _staging_reachable_fast(n: int, tri_bits: list[int], edge_bits: list[int],
                           pair_bits: list[int],
                           patterns: list[int]) -> bool:
    """位并行判断是否存在一条从空集到全集、全程落在有效掩码上的落位顺序。

    仅用大整数按位运算：有效掩码 = 各约束位集按位与；可达性经至多 n 轮
    「给每个可达掩码追加一个未落位点」的位并行转移取不动点。
    """
    good = (1 << (1 << n)) - 1
    for bits in tri_bits:
        good &= bits
    for bits in edge_bits:
        good &= bits
    for bits in pair_bits:
        good &= bits

    reach = 1  # 仅掩码 0（基线，施工起点）可达
    for _ in range(n):
        nxt = reach
        for p in range(n):
            # 不含 p 的可达掩码，追加 p 后仍须有效
            nxt |= ((reach & ~patterns[p]) << (1 << p)) & good
        if nxt == reach:
            break
        reach = nxt
    return bool((reach >> ((1 << n) - 1)) & 1)


def _joint_staging(
        points: list[dict[str, Any]],
        triangles: list[list[int]],
        edges: list[dict[str, Any]],
        edge_pairs: list[tuple[int, int]],
        base_area: list[int],
        origin: list[tuple[int, int]],
        candidates: list[list[list[int]]],
        choice: list[int],
        cache: dict[Any, Any]) -> dict[str, Any]:
    """为一个已通过位并行可达性筛选的组合产出完整分段落位证据。

    在 2ⁿ 个子集上做详细 DP：按（逐步位移峰值向量, 点序列字典序）择优、
    统计安全全序数量；不存在安全顺序时给出最早受阻（再取字典序最小已落位
    序列）的死路证据与各尝试点的首条违约。
    """
    n = len(points)
    disp = [(candidates[i][choice[i]][0], candidates[i][choice[i]][1])
            for i in range(n)]
    disp_sq = [dx * dx + dy * dy for dx, dy in disp]
    target = [(origin[i][0] + disp[i][0], origin[i][1] + disp[i][1])
              for i in range(n)]

    tri_bits, edge_bits, pair_bits = _staging_constraint_bitsets(
        triangles, edges, edge_pairs, base_area, origin, target,
        choice, n, cache)
    good = (1 << (1 << n)) - 1
    for bits in tri_bits:
        good &= bits
    for bits in edge_bits:
        good &= bits
    for bits in pair_bits:
        good &= bits

    full = (1 << n) - 1
    size = full + 1

    # dp_peak[mask]：到达 mask 的最小逐步峰值向量；
    # dp_path[mask]：峰值最优中再取点序列字典序最小（整数大端打包）；
    # dp_lex[mask]：不看峰值的纯字典序最小可达序列（死路证据用）；
    # cnt[mask]：安全前缀数量。
    dp_peak: list[tuple[int, ...] | None] = [None] * size
    dp_path: list[int] = [-1] * size
    dp_lex: list[int] = [-1] * size
    cnt = [0] * size
    dp_peak[0] = ()
    dp_path[0] = 0
    dp_lex[0] = 0
    cnt[0] = 1

    for mask in range(size):
        peaks = dp_peak[mask]
        if peaks is None:
            continue
        last_peak = peaks[-1] if peaks else 0
        path = dp_path[mask]
        lex_prefix = dp_lex[mask]
        # 大端打包：第 k 个落位点放在高位，使整数序等于点序列字典序
        shift = 4 * (n - 1 - mask.bit_count())
        remaining = full ^ mask
        while remaining:
            bit = remaining & -remaining
            remaining ^= bit
            nxt = mask | bit
            if not ((good >> nxt) & 1):
                continue
            p = bit.bit_length() - 1
            cand_peak = peaks + (max(last_peak, disp_sq[p]),)
            cand_path = path | (p << shift)
            old_peak = dp_peak[nxt]
            if (old_peak is None or cand_peak < old_peak
                    or (cand_peak == old_peak and cand_path < dp_path[nxt])):
                dp_peak[nxt] = cand_peak
                dp_path[nxt] = cand_path
            cand_lex = lex_prefix | (p << shift)
            if dp_lex[nxt] < 0 or cand_lex < dp_lex[nxt]:
                dp_lex[nxt] = cand_lex
            cnt[nxt] += cnt[mask]

    total_orders = 1
    for k in range(1, n + 1):
        total_orders *= k

    result: dict[str, Any] = {
        "totalOrders": total_orders,
        "safeOrderCount": cnt[full],
    }

    if dp_peak[full] is not None:
        packed = dp_path[full]
        order = [(packed >> (4 * (n - 1 - k))) & 15
                 for k in range(n)]
        peaks_tuple = dp_peak[full]
        assert peaks_tuple is not None

        def state_after(path: list[int]) -> list[tuple[int, int]]:
            pos = list(origin)
            for i in path:
                pos[i] = target[i]
            return pos

        steps: list[dict[str, Any]] = []
        path: list[int] = []
        pos = list(origin)
        peak = 0
        for step_no, p in enumerate(order, start=1):
            prev = list(pos)
            path = path + [p]
            pos = state_after(path)
            peak = max(peak, disp_sq[p])
            steps.append(_stage_step_evidence(
                points, triangles, edges, edge_pairs, base_area, origin,
                disp, prev, pos, path, peak, step_no, p, choice[p]))
        result.update({
            "feasible": True,
            "order": order,
            "orderNames": [points[i]["name"] for i in order],
            "stepPeakSq": list(peaks_tuple),
            "maxStepSq": peaks_tuple[-1],
            "steps": steps,
        })
        return result

    # 无安全顺序：取可达子集中「最早受阻 → 已落位序列字典序最小」的死路
    dead_key: tuple[int, int] | None = None
    for mask in range(size):
        if dp_peak[mask] is None:
            continue
        remaining = full ^ mask
        bits = remaining
        extendable = False
        while bits:
            bit = bits & -bits
            bits ^= bit
            if (good >> (mask | bit)) & 1:
                extendable = True
                break
        if not extendable:
            key = (mask.bit_count(), dp_lex[mask])
            if dead_key is None or key < dead_key:
                dead_key = key
    assert dead_key is not None
    plen = dead_key[0]
    prefix = [(dead_key[1] >> (4 * (n - 1 - k))) & 15
              for k in range(plen)]

    pos = list(origin)
    for i in prefix:
        pos[i] = target[i]
    placed_set = frozenset(prefix)
    attempts: list[dict[str, Any]] = []
    for i in range(n):
        if i in placed_set:
            continue
        dx, dy = disp[i]
        trial = list(pos)
        trial[i] = target[i]
        violation = _first_constraint_violation(
            points, triangles, edges, edge_pairs, base_area, trial)
        assert violation is not None
        attempts.append({
            "pointIndex": i,
            "pointName": points[i]["name"],
            "candidateIndex": choice[i],
            "displacement": [dx, dy],
            "displacementSq": disp_sq[i],
            "displaced": [origin[i][0] + dx, origin[i][1] + dy],
            "movedPoints": [[x, y] for x, y in trial],
            "violation": {k: v for k, v in violation.items()
                          if k != "order"},
        })
    kind_rank = {"orientation": 0, "length": 1, "crossing": 2}
    first_attempt = min(
        attempts,
        key=lambda a: (kind_rank[a["violation"]["kind"]],
                       a["pointIndex"]))
    result.update({
        "feasible": False,
        "blockedStep": len(prefix) + 1,
        "placed": prefix,
        "placedNames": [points[i]["name"] for i in prefix],
        "attempts": attempts,
        "movedPoints": [[x, y] for x, y in pos],
        "firstViolation": first_attempt["violation"],
        "firstViolationPointIndex": first_attempt["pointIndex"],
    })
    return result


def adjudicate(data: Any) -> dict[str, Any]:
    """执行联合裁决，返回可直接序列化给前端的证据报告。"""
    points, triangles, edges, candidates = _validate(data)
    staging_enabled = _staging_flag(data)
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
    # 未启用：best = (静态键, choice)。
    # 启用：枚举阶段只记录「可达且静态键最小」的组合，详细落位 DP 在循环后
    # 仅对胜出组合（或报告受阻证据时的静态最优组合）执行一次。
    best: tuple | None = None
    reachable_best: tuple[
        tuple[int, int, tuple[int, ...]], list[int]] | None = None
    static_best: tuple[tuple[int, int, tuple[int, ...]], list[int]] | None = None
    first_violation: dict[str, Any] | None = None
    # 分段落位的「约束 × 成员候选序号」位集判定在各组合间复用
    sig_cache: dict[Any, Any] = {}
    if staging_enabled:
        patterns = _member_patterns(n)
    tried = 0

    for choice in itertools.product(*ranges):
        tried += 1
        choice_list = list(choice)
        moved = [
            (origin[i][0] + candidates[i][choice[i]][0],
             origin[i][1] + candidates[i][choice[i]][1])
            for i in range(n)
        ]
        violation = _first_constraint_violation(
            points, triangles, edges, edge_pairs, base_area, moved)

        if violation is not None:
            if first_violation is None:
                first_violation = {
                    **violation,
                    "choice": list(choice),
                    "movedPoints": [[x, y] for x, y in moved],
                }
            continue

        # 静态可行组合：按三级静态目标记录
        disp_sq = [
            candidates[i][choice[i]][0] ** 2
            + candidates[i][choice[i]][1] ** 2
            for i in range(n)
        ]
        static_key = (max(disp_sq), sum(disp_sq), choice)
        if static_best is None or static_key < static_best[0]:
            static_best = (static_key, choice_list)

        if not staging_enabled:
            if best is None or static_key < best[0]:
                best = (static_key, choice_list)
            continue

        # 启用分段落位：先用位并行可达性在 *候选位移 × 落位顺序* 的完整
        # 组合空间中筛选——静态三级键对每个 choice 唯一，故只需保留可达的
        # 最小静态键组合，次序与逐步峰值在该组合内部再行裁决。
        target = moved  # 终态位置
        tri_bits, edge_bits, pair_bits = _staging_constraint_bitsets(
            triangles, edges, edge_pairs, base_area, origin, target,
            choice_list, n, sig_cache)
        if _staging_reachable_fast(n, tri_bits, edge_bits, pair_bits,
                                  patterns):
            if reachable_best is None or static_key < reachable_best[0]:
                reachable_best = (static_key, choice_list)

    if staging_enabled:
        if reachable_best is not None:
            (_max_sq, _sum_sq, _choice_tuple), winner = reachable_best
            # 唯一胜出组合：在此组合内部对全部落位顺序做详细联合裁决
            staging = _joint_staging(points, triangles, edges, edge_pairs,
                                     base_area, origin, candidates, winner,
                                     sig_cache)
            assert staging["feasible"]
            best = (
                (_max_sq, _sum_sq, tuple(winner),
                 tuple(staging["stepPeakSq"]), tuple(staging["order"])),
                winner, staging)
        else:
            best = None  # 无联合可行方案；受阻证据在报告构造时计算

    return _build_report(points, triangles, edges, candidates, origin,
                         base_area, edge_pairs, best, static_best,
                         first_violation, tried, staging_enabled, sig_cache)


def _build_report(points, triangles, edges, candidates, origin,
                  base_area, edge_pairs, best, static_best,
                  first_violation, tried, staging_enabled,
                  sig_cache=None) -> dict:
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
        conflicts = _conflicts_at(edges, edge_pairs, moved)
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

    if not staging_enabled:
        # 未启用分段落位：报告结构与字段保持原样
        if best is not None:
            (max_sq, sum_sq, _choice_tuple), choice = best
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
            report.update(geometry_report(choice))
            report["firstViolation"] = first_violation
        return report

    # 启用分段落位复核
    if best is not None:
        # 联合裁决成功者：(联合键, choice, staging)
        (max_sq, sum_sq, _choice_tuple, _peak_tuple, _order_tuple), \
            choice, staging = best
        report["objective"] = {"maxSq": max_sq, "sumSq": sum_sq}
        report["choice"] = choice
        report["assignment"] = assignment_entries(choice)
        report.update(geometry_report(choice))
        report["firstViolation"] = None
        report["stagingReview"] = {"enabled": True, **staging}
        return report

    # 启用后无联合可行方案
    if static_best is not None:
        # 存在静态最优组合，但任何候选组合都找不到安全落位次序：
        # 顶层几何表展示该静态最优组合，受阻证据放入 stagingReview。
        (max_sq, sum_sq, _choice_tuple), choice = static_best
        report["objective"] = {"maxSq": max_sq, "sumSq": sum_sq}
        report["staticObjective"] = {"maxSq": max_sq, "sumSq": sum_sq}
        report["choice"] = choice
        report["assignment"] = assignment_entries(choice)
        report.update(geometry_report(choice))
        report["firstViolation"] = None
        # best 为 None 时各静态组合的受阻证据在裁决循环中丢弃，这里对静态
        # 最优组合重新计算一次确定性的分段落位受阻报告。
        staging = _joint_staging(points, triangles, edges, edge_pairs,
                                 base_area, origin, candidates, choice,
                                 sig_cache if sig_cache is not None else {})
        assert staging["feasible"] is False
        report["stagingReview"] = {"enabled": True, **staging}
        return report

    # 连静态可行组合都不存在：沿用首个违约证据
    assert first_violation is not None
    choice = first_violation["choice"]
    report["objective"] = None
    report["choice"] = choice
    report["assignment"] = assignment_entries(choice)
    report.update(geometry_report(choice))
    report["firstViolation"] = first_violation
    report["stagingReview"] = {
        "enabled": True,
        "feasible": False,
        "reason": "noStaticFeasibleCombination",
        "message": "不存在满足静态硬约束的候选组合，分段落位无从排起",
    }
    return report
