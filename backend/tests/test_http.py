"""HTTP API 集成测试：健康检查、示例、裁决、错误响应与静态资源。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import importlib.util
import sys
from pathlib import Path

_SERVER_PATH = Path(__file__).resolve().parent.parent / "server.py"
spec = importlib.util.spec_from_file_location("dome_server", _SERVER_PATH)
server_mod = importlib.util.module_from_spec(spec)
sys.modules["dome_server"] = server_mod
spec.loader.exec_module(server_mod)


@pytest.fixture()
def http_server(tmp_path):
    # 静态目录指向临时目录，避免依赖前端构建产物
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>t</title>", encoding="utf-8")
    server_mod.STATIC_DIR = tmp_path
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return resp.status, resp.headers.get("Content-Type"), resp.read()


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_healthz(http_server):
    status, ctype, body = _get(http_server, "/healthz")
    assert status == 200 and "json" in ctype
    assert json.loads(body) == {"status": "ok"}


def test_sample_endpoint_shape(http_server):
    status, _, body = _get(http_server, "/api/sample")
    data = json.loads(body)
    assert status == 200
    assert 5 <= len(data["points"]) <= 8
    assert len(data["candidates"]) == len(data["points"])
    assert all(2 <= len(g) <= 3 for g in data["candidates"])


def test_sample_adjudication_smoke(http_server):
    with urllib.request.urlopen(http_server + "/api/sample", timeout=5) as r:
        sample = json.loads(r.read())
    status, report = _post(http_server, "/api/adjudicate", sample)
    assert status == 200
    assert report["feasible"] is True
    assert len(report["choice"]) == len(sample["points"])
    assert len(report["assignment"]) == len(sample["points"])
    assert all("rejected" in row for row in report["assignment"])
    assert all("actualSq" in row for row in report["edges"])
    assert all("actualSignedArea2" in row for row in report["triangles"])


def _deadlock_sample():
    # 与 test_solver 的分段落位死锁夹具一致
    return {
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
    }


def test_staging_disabled_response_has_no_review_field(http_server):
    status, report = _post(http_server, "/api/adjudicate",
                           _deadlock_sample())
    assert status == 200
    assert "stagingReview" not in report
    # 显式 false 与缺省返回完全一致
    status2, report2 = _post(http_server, "/api/adjudicate",
                             {**_deadlock_sample(),
                              "stagingReview": False})
    assert status2 == 200
    assert report2 == report


def test_staging_enabled_safe_order_report(http_server):
    with urllib.request.urlopen(http_server + "/api/sample", timeout=5) as r:
        sample = json.loads(r.read())
    status, report = _post(http_server, "/api/adjudicate",
                           {**sample, "stagingReview": True})
    assert status == 200
    assert report["feasible"] is True
    st = report["stagingReview"]
    assert st["enabled"] is True and st["feasible"] is True
    assert len(st["order"]) == len(sample["points"])
    assert len(st["steps"]) == len(sample["points"])
    assert st["safeOrderCount"] >= 1
    step = st["steps"][0]
    assert {"step", "pointIndex", "displacement", "affectedTriangles",
            "affectedEdges", "crossChecks", "cumulativePeakSq",
            "movedPoints"} <= set(step)


def test_staging_enabled_blocked_order_report(http_server):
    status, report = _post(http_server, "/api/adjudicate",
                           {**_deadlock_sample(),
                            "stagingReview": True})
    assert status == 200
    assert report["feasible"] is False
    st = report["stagingReview"]
    assert st["enabled"] is True and st["feasible"] is False
    assert st["blockedStep"] == 1
    assert st["placed"] == []
    assert len(st["attempts"]) == 5
    assert all({"displacement", "candidateIndex", "violation"}
               <= set(a) for a in st["attempts"])
    assert st["firstViolation"]["kind"] == "orientation"


def test_staging_non_boolean_flag_returns_400(http_server):
    status, body = _post(http_server, "/api/adjudicate",
                         {**_deadlock_sample(), "stagingReview": "true"})
    assert status == 400
    assert "布尔" in body["error"]


def test_bad_payload_returns_400(http_server):
    status, body = _post(http_server, "/api/adjudicate", {"points": []})
    assert status == 400
    assert "5 至 8" in body["error"]


def test_malformed_json_returns_400(http_server):
    req = urllib.request.Request(
        http_server + "/api/adjudicate",
        data=b"{not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400


def test_static_index_and_path_traversal(http_server):
    status, ctype, body = _get(http_server, "/")
    assert status == 200 and b"doctype" in body
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(
            http_server + "/..%2f..%2fetc%2fpasswd", timeout=5)
    assert exc_info.value.code in (403, 404)
