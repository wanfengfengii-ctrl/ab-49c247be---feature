"""裁决 API 冒烟脚本（仅用标准库，供本地与 Compose verify 服务调用）。

检查项：
  1. /healthz 健康；
  2. 内置示例经 /api/adjudicate 裁决可行，证据字段完整；
  3. 构造一个无解草稿（把一条边锁到不可能的长度平方），
     接口必须稳定返回 feasible=false 及首个违约证据，而非伪结论；
  4. 非法草稿返回 400；
  5. 分段落位复核：未启用响应不含新字段；启用后内置示例给出安全落位
     次序与逐步证据，构造的死锁草稿稳定报告最早受阻步骤与尝试点；
  6. 静态首页可访问。

任一检查失败即以非零退出码结束。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _request(method: str, url: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, ctype, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" —— {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    # 1) 健康检查
    status, _, raw = _request("GET", f"{base}/healthz")
    check("GET /healthz 返回 200", status == 200, f"HTTP {status}")
    check("健康体 status=ok", json.loads(raw)["status"] == "ok")

    # 2) 内置示例可行
    status, _, raw = _request("GET", f"{base}/api/sample")
    check("GET /api/sample 返回 200", status == 200, f"HTTP {status}")
    sample = json.loads(raw)
    n = len(sample["points"])
    check("示例基准点数量在 5~8", 5 <= n <= 8, f"n={n}")
    check("每点候选位移 2~3 条",
          all(2 <= len(g) <= 3 for g in sample["candidates"]))

    status, _, raw = _request("POST", f"{base}/api/adjudicate", sample)
    check("示例裁决返回 200", status == 200, f"HTTP {status}")
    report = json.loads(raw)
    check("示例存在可行方案", report["feasible"] is True)
    check("未启用分段落位时响应不含 stagingReview 字段",
          "stagingReview" not in report)
    check("每点恰选一条位移", len(report["choice"]) == n
          and all(0 <= c < len(sample["candidates"][i])
                  for i, c in enumerate(report["choice"])))
    check("采用/未采用候选证据完整",
          all({"candidateIndex", "displacement", "displacementSq",
               "displaced", "rejected"} <= set(row)
              for row in report["assignment"]))
    check("各边长度平方均在闭区间内",
          all(row["within"] for row in report["edges"]))
    check("各三角面朝向均严格保持",
          all(row["preserved"] for row in report["triangles"]))
    check("非共端边无相交/相切", report["conflicts"] == [])
    obj = report["objective"]
    check("优化目标自洽（平方和 ≥ 最大值）",
          obj["sumSq"] >= obj["maxSq"] >= 0, json.dumps(obj, ensure_ascii=False))

    # 3) 无解草稿：第一条边锁死为 999999，小幅候选位移无法满足
    impossible = json.loads(json.dumps(sample))
    first_edge = impossible["edges"][0]
    first_edge["minSq"] = 999_999
    first_edge["maxSq"] = 999_999
    status, _, raw = _request("POST", f"{base}/api/adjudicate", impossible)
    check("无解草稿返回 200（裁决结果而非服务错误）", status == 200)
    bad = json.loads(raw)
    check("裁决明确报告 feasible=false", bad["feasible"] is False)
    v = bad.get("firstViolation")
    check("附带首个违约证据", isinstance(v, dict)
          and {"kind", "message", "choice"} <= set(v))
    check("证据指向被锁死边长的长度约束",
          v["kind"] == "length" and v["edgeIndex"] == 0,
          f"kind={v['kind']} actualSq={v.get('actualSq')}")
    check("证据组合长度恰为点数", len(v["choice"]) == n)
    check("证据几何表中该边确实越界",
          bad["edges"][0]["within"] is False
          and bad["edges"][0]["actualSq"] == v["actualSq"])

    # 4) 非法草稿 400
    status, _, raw = _request("POST", f"{base}/api/adjudicate", {"points": []})
    check("非法草稿返回 400", status == 400, f"HTTP {status}")
    check("400 响应给出中文原因", "error" in json.loads(raw))

    # 5) 分段落位复核
    status, _, raw = _request("POST", f"{base}/api/adjudicate",
                              {**sample, "stagingReview": True})
    check("启用分段落位的示例裁决返回 200", status == 200, f"HTTP {status}")
    staged = json.loads(raw)
    check("启用后存在联合可行方案", staged["feasible"] is True)
    st = staged.get("stagingReview")
    check("附带 stagingReview 复核报告", isinstance(st, dict) and st.get("enabled") is True)
    check("安全落位次序覆盖全部点",
          isinstance(st.get("order"), list) and len(st["order"]) == n
          and sorted(st["order"]) == list(range(n)))
    check("逐步证据完整（每步含新落位点/受影响三角面/边/边对/累计状态）",
          isinstance(st.get("steps"), list) and len(st["steps"]) == n
          and all({"step", "pointIndex", "displacement",
                   "affectedTriangles", "affectedEdges", "crossChecks",
                   "placed", "movedPoints", "cumulativePeakSq"} <= set(s)
                  for s in st["steps"]))
    check("逐步峰值非降且以最大位移平方收尾",
          all(st["stepPeakSq"][i] <= st["stepPeakSq"][i + 1]
              for i in range(n - 1))
          and st["stepPeakSq"][-1] == staged["objective"]["maxSq"])
    check("每步受影响三角面/边均继续满足约束",
          all(t["preserved"] for s in st["steps"]
              for t in s["affectedTriangles"])
          and all(e["within"] for s in st["steps"]
                  for e in s["affectedEdges"])
          and all(not c["intersect"] for s in st["steps"]
                  for c in s["crossChecks"]))
    status, _, raw400 = _request("POST", f"{base}/api/adjudicate",
                                 {**sample, "stagingReview": "yes"})
    check("stagingReview 非布尔返回 400", status == 400, f"HTTP {status}")
    check("400 给出中文原因", "布尔" in json.loads(raw400).get("error", ""))

    # 5b) 死锁草稿：终态静态可行但无任何安全落位次序
    deadlocked = {
        "points": [
            {"name": "A", "x": 0, "y": 0},
            {"name": "B", "x": 10, "y": 0},
            {"name": "C", "x": 14, "y": 6},
            {"name": "D", "x": 6, "y": 12},
            {"name": "E", "x": -4, "y": 6},
        ],
        "triangles": [[0, 1, 2], [0, 2, 3], [0, 3, 4]],
        "edges": [{"endpoints": [0, 1], "minSq": 58, "maxSq": 58}],
        "candidates": [
            [[0, 0], [1, 0]],
            [[-3, 3], [6, 6]],
            [[0, 12], [0, -1]],
            [[0, 0], [1, 0]],
            [[0, 0], [1, 0]],
        ],
        "stagingReview": True,
    }
    status, _, raw = _request("POST", f"{base}/api/adjudicate", deadlocked)
    check("死锁草稿返回 200（裁决结果而非服务错误）", status == 200)
    dl = json.loads(raw)
    dst = dl.get("stagingReview", {})
    check("死锁草稿明确无联合可行方案",
          dl["feasible"] is False and dst.get("feasible") is False)
    check("稳定指出最早受阻步骤与已落位集合",
          dst.get("blockedStep") == 1 and dst.get("placed") == [])
    check("给出全部尝试点候选及其首条被破坏约束",
          isinstance(dst.get("attempts"), list) and len(dst["attempts"]) == 5
          and all({"pointIndex", "candidateIndex", "displacement",
                   "violation"} <= set(a) for a in dst["attempts"])
          and dst["firstViolation"]["kind"] == "orientation")

    # 6) 静态首页
    status, ctype, raw = _request("GET", f"{base}/")
    check("GET / 返回 200 HTML",
          status == 200 and "html" in ctype, f"HTTP {status} {ctype}")
    check("首页为裁决页面",
          "基线网位移联合裁决".encode() in raw)

    print("\n全部冒烟检查通过。")


if __name__ == "__main__":
    main()
