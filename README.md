# 玻璃穹顶基线网 · 位移联合裁决系统

历史建筑玻璃穹顶布设裂纹监测点时，每个基准点有 2~3 条**候选整数位移**。
本系统对所有基准点**联合**选定唯一一条位移，保证复测后的基线网仍可作为
可解释的变形依据，而不是让局部位移看似最小、却令三角网翻折或缆线交叉。

## 业务规则（硬约束）

1. **每点恰选一条**候选位移；
2. **严格朝向**：每个登记三角面位移后的两倍有向面积（叉积）必须与原基线
   **严格同号**——翻转（异号）与退化为共线（为 0）均不允许；
3. **边长闭区间**：每条登记边位移后的长度平方必须落在 `[minSq, maxSq]`
   闭区间内；
4. **非共端边不相交也不相切**：任意两条不共享端点的边，既不能正向相交，
   也不能端点相接/T 字接触/共线端点接触（交集为单点同样判负）。

## 优化目标（可行方案之间，按顺序比较）

1. 各点位移平方的**最大值最小**（minimax）；
2. 最大值相同时，位移**平方和最小**；
3. 再相同时，取**按点录入顺序的候选序号字典序最小**（序号从 0 起）。

## 无方案时的证据

系统穷举全部组合（至多 3⁸ = 6561 种）。若无一可行，**不会**给出局部可用的
伪结论，而是稳定返回**首个违约证据**：

- 取候选序号字典序第一种组合；
- 在该组合内按 **朝向 → 边长 → 非共端边相交** 的次序取第一项失败；
- 证据包含违约类型、涉及的三角面/边端点索引、原数值与实际数值、
  违约时的候选序号向量，以及完整的位移后几何表与证据图。

全部几何判定只使用整数（叉积、点积、长度平方），无浮点误差，证据可人工复核。

## 可选：分段落位复核（`stagingReview`）

工程师可在请求体中加 `"stagingReview": true` 启用。系统除选出每点最终位移外，
还要排出一条**逐点从基线移至采用位移的施工次序**：每一步落位一个点，已落位点
取采用位移、未落位点仍在基线，该混合网必须继续满足全部三类硬约束
（三角朝向、边长闭区间、非共端边不相交/相切）。

- **完整组合联合裁决**：在「候选位移 × 落位顺序」的完整空间中穷举，先按既有
  三级静态目标（最大位移平方 → 平方和 → 候选序号字典序）比较最终方案，
  再在胜出方案内部取**逐步位移平方峰值向量**最小、再取**点序列字典序**最小
  的次序。绝不会先选静态方案再事后试排——静态最优组合可能根本无法安全落位，
  此时联合裁决会改选静态次优但可落位的组合。
- **逐步证据**：安全次序中每一步都给出新落位点与采用候选、受影响三角面的
  原/落位前/落位后两倍有向面积、受影响边的落位前/后长度平方与区间判定、
  本步发生变化的非共端边对相交复核，以及累计已落位集合、累计网点与峰值。
- **无安全次序**：稳定报告最早受阻步骤号、此前已安全落位点序列、在该状态下
  对每个未落位点尝试其采用候选的结果及各自首条被破坏约束（按
  朝向 → 边长 → 相交 取第一条），并注明该组合终态可行但无法抵达。
- 未启用（缺省或显式 `false`）时请求、响应字段、优化结果与页面展示与原来
  完全一致，不返回任何分段落位字段。

实现上以 2ⁿ 子集位掩码 DP 完成可达性与计数（n≤8，状态只依赖已落位集合），
并用大整数位集做位并行筛选；穷举主循环只做位运算，详细逐步 DP 仅对最终胜出
组合执行一次。

## 目录结构

```
backend/            零第三方依赖的 Python 服务（标准库 http.server）
  app/solver.py     裁决核心：校验、整数几何、穷举择优、违约证据
  app/sample.py     内置六基准点示例草稿
  server.py         HTTP API + 静态托管
  tests/            26 个 pytest 用例（几何谓词/目标/证据/HTTP）
frontend/           Vite + 原生 ES Module 前端（可构建）
  src/main.js       草稿编辑、提交、证据渲染、SVG 几何图
scripts/
  smoke.py          裁决 API 冒烟（标准库，以退出码报告）
  verify.sh         verify 容器入口：测试 → 构建 → 冒烟
Dockerfile          多阶段：frontend-build / runtime / verify
docker-compose.yml  web（健康检查、可配置宿主端口）+ 一次性 verify
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/healthz` | 健康检查，返回 `{"status":"ok"}` |
| `GET` | `/api/sample` | 内置示例草稿 |
| `POST` | `/api/adjudicate` | 提交草稿，返回裁决报告 |

请求体：

```json
{
  "points":     [{"name": "P1", "x": 0, "y": 0}],
  "triangles":  [[0, 1, 2]],
  "edges":      [{"endpoints": [0, 1], "minSq": 70, "maxSq": 130}],
  "candidates": [[[0, 0], [1, -1]]],
  "stagingReview": false
}
```

`stagingReview` 可省略或为布尔值；为 `true` 时启用分段落位复核（非布尔值返回
400）。启用后报告额外含 `stagingReview`：成功时含 `order`/`orderNames`、
`stepPeakSq`、`safeOrderCount`/`totalOrders` 与逐步证据 `steps`；无安全次序时
`feasible=false` 且含 `blockedStep`、`placed`、各尝试点 `attempts` 与
`firstViolation`。若连静态可行组合都不存在，则 `stagingReview.reason` 为
`noStaticFeasibleCombination`，顶层仍返回原首个违约证据。

约束：基准点 5~8 个；每点 2~3 条**不重复**整数位移；坐标、位移、`minSq`、
`maxSq` 均为整数且 `0 ≤ minSq ≤ maxSq`。录入不合法返回 `400` 与中文原因
（这与几何上不可行不同：不可行返回 `200` 且 `feasible=false`）。

报告含：`feasible`、`choice`（候选序号向量）、`objective`、`assignment`
（每点采用位移、位移平方、位移后坐标、未采用候选）、`edges`（各边实际长度
平方与区间判定）、`triangles`（各面原/实际两倍有向面积与朝向判定）、
`conflicts`（非共端边相交清单）、`firstViolation`（无解时的首个违约证据）。

## 本地开发（无需 Docker）

后端仅需 Python 3.11+（标准库）：

```bash
cd backend
python server.py                 # http://localhost:8080，托管 ../frontend 源码目录
```

前端热更新（可选）：

```bash
cd frontend
npm install
npm run dev                      # http://localhost:5173，/api 代理到 8080
```

测试与冒烟：

```bash
pip install pytest
cd backend && python -m pytest -q
python ../scripts/smoke.py --base-url http://127.0.0.1:8080
```

## Docker / Docker Compose

启动服务（宿主端口可用 `.env` 中 `HOST_PORT` 配置，默认 8080）：

```bash
cp .env.example .env             # 需要时修改 HOST_PORT
docker compose up -d --build
# 浏览器打开 http://localhost:8080
curl -s http://localhost:8080/healthz
```

一次性 verify 流水线（随 `up` 自动执行：等待 `web` 健康检查通过后，
在容器内执行 **代码测试 → 前端构建 → 裁决 API 冒烟**，以退出码报告结果）：

```bash
docker compose up --build
# 观察 verify 容器日志：docker compose logs verify
# 其退出码 0 表示全部通过，非 0 表示对应阶段失败
docker compose ps                  # verify 状态为 exited(0)

# 也可在 web 已运行时单独触发一次
docker compose run --rm verify
```

健康检查：`web` 服务与运行时镜像均配置了 `/healthz` HTTP 健康检查
（5 秒间隔，12 次重试），`verify` 通过
`depends_on: condition: service_healthy` 确保只在依赖健康后启动。

## 裁决算法说明

- 对至多 3⁸ 种候选组合做完全穷举（规模小，毫秒级完成），保证全局最优、
  不存在回溯/近似带来的伪结论；
- 朝向：整数叉积 `cross(B-A, C-A)`，用 `新旧面积相乘 ≤ 0` 判定未严格保持；
- 边相交：参数化交叉的分子/分母整数比较，覆盖平行共线重叠、T 字接触、
  端点接触与零长度退化边；交集恰为单点即记为**相切**；
- 仅检查**不共享端点**的边对，共享端点的边在公共端点相接是合法拓扑。
