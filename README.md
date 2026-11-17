# Forge · Research Cluster

一个 agent 研究平台。CPU 控制面负责规划、代码和研究循环；CPU / GPU 节点分别管理计算容量，通过异步作业协议接入。Web 界面连接真实后端，管理研究、实验、资源池、日志和产物。

## 本机启动

```bash
cd /home/zhengpeizhen/SpecForge/research-cluster
uv sync --extra dev --extra temporal --extra postgres
uv run research-cluster dev
```

打开 <http://127.0.0.1:8787>。`dev` 启动 API、研究编排器、CPU 执行节点和只读硬件监控；安装 Lean 工具链后另起独立验证节点。默认 SQLite 持久化到 `.state/`，无需 GPU、模型 API 或 Docker。可在工作台点击“运行 CPU 基准研究”，运行固定预算的 Rosenbrock 优化实验，查看真实指标、候选谱系和产物。

已有虚拟环境也可运行 `.venv/bin/research-cluster dev`。`--cpus`、`--memory-mb`、`--concurrency` 控制本地执行池容量；例如 `dev --cpus 2 --memory-mb 2048 --concurrency 2`。

## 从自己电脑访问远程服务

如果本项目运行在 SSH 服务器或远程开发容器中，`http://127.0.0.1:8787` 是服务器上的地址。在自己电脑的浏览器中使用该地址，需要先建立端口转发。

使用 VS Code Remote SSH 时，在连接这台服务器的窗口打开 **Ports / 端口** 面板，选择 **Forward a Port / 转发端口**，输入 `8787`，再点击该条目的浏览器图标。使用面板显示的 **Forwarded Address / 转发地址**；本机端口被占用时，转发后的端口可能不同。参见 [VS Code 官方端口转发说明](https://code.visualstudio.com/docs/remote/ssh#_forwarding-a-port-creating-ssh-tunnel)。

也可以在**自己电脑的终端**运行下面的命令，保持终端开启；将 `SSH_HOST` 替换为你平时连接这台服务器的 SSH 别名，并保留需要的 SSH 端口或跳板参数：

```bash
ssh -N -L 18787:127.0.0.1:8787 SSH_HOST
```

随后打开 <http://127.0.0.1:18787/#verification>，研究树为 <http://127.0.0.1:18787/#tree>。该转发将本机端口 18787 接到服务器端口 8787；无需修改服务的监听地址。

## 架构与边界

```mermaid
flowchart TD
    UI[Web 管理台] --> API[FastAPI / 任务与实验档案]
    ORCH[CPU 研究编排器 / Temporal] --> API
    API --> DB[(PostgreSQL / SQLite)]
    CPU[独立 CPU Worker] -->|领取 CPU 队列 / 心跳 / 回传| API
    GPU[独立 GPU Worker] -->|领取 GPU 队列 / 心跳 / 回传| API
    CPU --> CEX[CPU 进程或容器]
    GPU --> GEX[GPU 进程或训练容器]
    CEX --> ART[指标 / 日志 / 产物]
    GEX --> ART
```

- **默认研究流程**：检索近期论文与必要基线 → 核验来源和阅读层次 → 结合既有发现综合候选任务 → 评审采纳 → 配置实验 → 提交作业 → 比较和归档结果。等待执行资源时不占用 agent 工作线程。
- **任务协议**：`JobInput` 包含命令 argv、文本源码包、资源请求、资源池、超时与幂等键。作业 ID、状态、日志、指标和产物通过 HTTP 传递；远程执行节点不依赖共享文件系统或控制面数据库。
- **资源管理**：CPU / GPU 各有独立进程、队列、容量和运行镜像。CPU 节点无法领取 GPU 作业，GPU 作业按节点注册的设备清单独占分配。没有 GPU 节点时只有 GPU 队列等待。
- **可靠性**：事务领取任务，租约与 fencing token 隔离迟到结果；日志分片和完成回调可重复提交。节点失联后停止本地执行，服务端在租约过期和宽限期后重排，达到尝试上限则标记失败。取消请求通过心跳传递。
- **持久性**：本机研究编排与 Temporal 使用同一组幂等步骤。研究、候选、作业、事件都在数据库中；进程重启后可恢复待执行的流程。候选生成也有可过期租约。
- **领域扩展**：`domain` 用于算法、金融和通用研发分类。具体能力由目标仓库、评测脚本、执行镜像提供，可以接入因子研究、回测或金融业务代码测试。

核心文件：`api.py`（控制 API）、`service.py`（任务协议与资源队列）、`research.py`（研究循环）、`worker.py`（执行后端）、`temporal_engine.py`（Temporal 编排）。控制面没有 PyTorch、CUDA 或 GPU 探测依赖。

## Lean 形式验证

打开 <http://127.0.0.1:8787/#verification> 管理证明记录，在研究树中查看「候选 → Lean 形式验证 → 证明目标」，点击节点查看公理依赖、内核重检、哈希、诊断与证明源码。文献提案默认登记待评估的证明义务；可以在“查看实验设计 → 评估证明义务”中更新适用性、理由和目标。模型综合也会评估这一项。将提案标为“必须通过”后，配置执行研究时必须启用 required 策略。

执行研究有三种策略：`off`（不启用）、`advisory`（记录证明结果，按实验指标选择）、`required`（所有固定目标通过后才能晋级）。默认不强制所有实验写证明。验证通过与实验成功分别记录，命题未证明不等于命题被反驳。已有研究和历史结果不会被补标为已证明。

验证 worker 使用固定的 Lean **4.35.0**，独立的 `lean-cpu` CPU 资源池、容量、超时和日志；控制面和 GPU worker 不需要 Lean。Linux x86_64 可以安装到本项目的忽略目录：

```bash
uv run --no-project --with zstandard python scripts/install_lean.py
CLUSTER_REPO_ROOT=/home/zhengpeizhen/SpecForge uv run research-cluster dev \
  --cpus 2 --memory-mb 2048 --concurrency 2 --lean-cpus 1
```

安装脚本校验固定发行包的 SHA-256。工具链存在且 CPU affinity 容量足够时，`dev` 额外启动 1 CPU / 1024 MiB / 1 槽的 Lean worker，CPU affinity 与普通 worker 分开；上例总共接入 3 个逻辑 CPU 的执行配额。`--lean-cpus 0` 关闭自动验证节点，`--lean-memory-mb` 修改其内存预约。工具链不存在时仍可启动平台，证明检查会等待独立验证节点。

远程 CPU 主机安装同一工具链后单独接入；路径在该执行主机解释：

```bash
research-cluster worker --url http://CONTROL_HOST:8787 --kind cpu --pool lean-cpu \
  --cpus 1 --memory-mb 1024 --concurrency 1 --cpu-offset 2 \
  --lean-home /absolute/path/to/lean-4.35.0-linux
```

`--cpu-offset` 跳过同机其他 worker 已预约的 affinity 条目，需要按实际部署规划。完整内核重检包含导入的声明，可能需要几十秒到数分钟；任务超时可配置为 5–1800 秒，优先使用最小必要的 import。

新建研究或配置实验时填写固定的 `ClusterSpec.lean` 内容和目标。例如，自然数转账模型：

```lean
import Init
namespace ClusterSpec
def Conserves : Prop :=
  ∀ (a b k : Nat), k ≤ a → (a-k)+(b+k)=a+b
end ClusterSpec
```

目标 JSON：

```json
[{"id":"conservation","proposition":"ClusterSpec.Conserves","description":"自然数模型总额守恒"}]
```

候选源码包中的 `formal/proofs.json`（路径可配置）：

```json
{"conservation":"by intro a b k h; omega"}
```

规格、目标、覆盖范围与工具链在研究创建时冻结。代码 Agent 会收到固定规格并写证明 JSON；单独的证明修复步骤只能更新证明项，使用独立的次数、最大输出 token 和超时预算。默认每个候选最多修复 1 次、2048 输出 token、60 秒；只有配置模型时才调用。无模型时直接检查候选已有的证明，缺失或错误会如实记录。

每次验证绑定候选源码包、固定规格和当前证明输入的 SHA-256。可信 runner 不执行候选的 Lake 配置或构建钩子，只把受限证明项嵌入平台生成的模块，检查它确实具有指定的固定 Prop 类型；审核传递公理依赖，仅允许 `propext`、`Classical.choice`、`Quot.sound`，再运行 `leanchecker --fresh ClusterProofs` 重放内核检查。`sorry`/`admit`、命令、元编程和 `native_decide` 不在当前证明输入范围内。仅有进程退出成功或 `verification_passed=1` 指标不能通过门槛。证明文件、固定规格、报告和重检日志归档为独立验证作业的产物。

当前提供 core / Std 的受限证明项验证，未自动部署 Mathlib、外部独立内核或完整 Python/CUDA 精化工具链。固定规格由研究配置提供并需要审核；证明覆盖其中的数学模型，代码哈希只是绑定所评估版本，不构成实现与模型等价的证明。Lean worker 属于现有可信执行节点边界。正式代码级验证需要补充实现语义与规格的对应证明。

“运行 Lean 验证自检”执行四个 Python 样例、自然数模型证明和晋级检查，不调用模型，不使用 GPU。它验证平台链路，不代表金融系统已获得正确性认证。

| 操作 | 接口 |
| --- | --- |
| 配置策略 | `POST /api/studies` 或提案执行接口的 `verification`，匹配 `LeanPolicy` |
| 更新提案的证明义务 | `PUT /api/studies/{id}/proposals/{task_id}/formal-verification` |
| 查看候选证明与验证作业 | `GET /api/jobs/{candidate_id}/verification` |
| 人工重检未完成证明 | `POST /api/jobs/{candidate_id}/verification/retry`，研究须仍在运行 |
| 运行基础设施自检 | `POST /api/verification/smoke` |

协议与门槛在 `verification.py`，固定执行器在 `lean_runner.py`。实现依据 [Lean 验证指南](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) 与 [内置 leanchecker 的说明](https://github.com/leanprover/lean4checker)。

## 并行研究与研究树

打开 <http://127.0.0.1:8787/#tree>，选择研究查看「目标 → 检索与论文证据 → 综合与候选任务 → 独立执行分支 → 候选版本 → 评测结果」。树从持久化记录生成，每 3 秒刷新；支持展开、收起、拖动、缩放和点击查看实验设计、真实日志、指标与产物。虚线表示通过 `parent_id` 记录的候选版本谱系，重试也保留来源。论文的收录状态与摘要/部分正文的阅读层次分别显示。

文献、综合和代码生成节点表示逻辑角色步骤，运行状态由实际租约和任务记录推导。界面单独显示模型配置、正在运行的模型步骤、在线 CPU/GPU 执行节点与任务槽；不会把工作线程或确定性的 CPU 参数搜索计作多个智能 agent。

本机编排器可同时推进多个研究或已配置的执行分支；单个分支按「生成候选 → 等待评测 → 选择最佳 → 下一轮」推进。`CLUSTER_MAX_CONCURRENT_RESEARCH=4` 控制每个编排器的研究步骤并发上限（1–32），本地线程池和 Temporal activity worker 使用相同配置。这是每进程上限，多台编排器的总并发需分别规划。计算并发另外由 CPU/GPU worker 的容量与 `--concurrency` 管理；等待实验不会持续占用研究步骤槽。

完整模型研究需要配置模型 API，并为已采纳提案配置仓库和评测；采纳提案本身不会启动实验。该平台目前提供分支研究与候选迭代，不具备自动扩容主机或任意生成新角色进程的能力。

## 默认的文献驱动研究

新建研究默认选择“文献驱动研究”。填写目标、检索关键词（可从英文术语提取）、时间窗（默认 90 天）、论文数量上限，以及可选的基线 arXiv ID。基线可以早于时间窗。参考仓库和相对证据路径用于读取已有实现、研究结论、负结果与评测约束；文件会记录哈希并限制在 `CLUSTER_REPO_ROOT` 内。

系统按近期更新时间查询 arXiv，分别保存首次发布与修订日期，去重并排除未来日期。前四篇近期论文会尝试获取部分 HTML 正文，阅读层次与内容哈希单独记录；不能取得正文时保留摘要级标签。当前来源覆盖 arXiv 返回的有限结果，不代表穷尽检索或全部论文精读。

在“文献与任务”页面可以查看检索时间、来源、证据与提案。每个任务包含假设、引用、对照、实验步骤、评测指标、验收/停止标准、风险和预估预算。“采纳任务”仅更新评审队列；点击“配置实验”、完成模型/仓库/执行池配置后，才创建继承该证据和任务设计的实验研究。实验创建支持幂等重试。

已配置 `LLM_MODEL` / `LLM_API_KEY` 时，模型会结合论文与本地证据综合任务，引用必须属于已核验的论文集合。未配置时仍可检索论文并生成明确标注的“规则核验草案”；它不冒充模型推理或新架构创新。模型/来源失败时记录错误并等待重试，不会静默跳入训练。

仓库顶层 `AGENTS.md` 同时保存研究任务的默认规范。`examples/dspark_literature_review.json` 是截至 2026-11-03 的 DSpark 案例：包含调研配置、本地约束和六个经助手核验综合的候选任务。它是历史研究案例，后续应先刷新文献再评估提案。

核心 API：

| 操作 | 接口 |
| --- | --- |
| 新建文献研究 | `POST /api/studies`，省略 `mode` 即默认 `literature` |
| 查看调研与任务 | `GET /api/studies/{id}`，查看 `config.literature` |
| 查看研究与候选树 | `GET /api/studies/{id}/tree`，只读派生结构与运行状态 |
| 重新检索/综合 | `POST /api/studies/{id}/literature/refresh` |
| 导入经过核验的综合任务 | `PUT /api/studies/{id}/synthesis`，匹配 `LiteratureSynthesis` schema |
| 采纳/暂缓/恢复 | `POST /api/studies/{id}/proposals/{task_id}/accept`（或 `defer` / `restore`） |
| 配置采纳任务的执行研究 | `PUT /api/studies/{id}/proposals/{task_id}/execution`，提交实验配置 |

显式的 `mode=benchmark` 是基础设施 smoke 测试，继续直接运行 CPU 基准；实际研究任务不会默认落入此模式。

## 模型驱动的仓库研究

```bash
uv sync --extra agent --extra temporal --extra postgres --extra dev
export LLM_MODEL='your-provider/model'
export LLM_API_KEY='your-key'
# 兼容网关可额外设置 LLM_BASE_URL。
export CLUSTER_REPO_ROOT=/absolute/path/to/allowed/repositories
uv run --extra agent research-cluster dev
```

在文献队列中采纳任务并选择“配置实验”，填写 Git 仓库根目录、评测命令、指标名称和比较方向。仓库路径必须位于 `CLUSTER_REPO_ROOT` 下；评测命令需要引用已提交的脚本，例如 `python evaluate.py`。每轮默认最多 30 个 agent 步骤、2 USD 的 SDK 模型预算和 600 秒，模型预算与步骤数可在界面中调整；费用统计依赖模型提供方的计价信息。文献综合阶段是独立的有界模型调用，最多生成 6000 个输出 token。

系统固定研究开始时的 Git 提交，为每轮创建独立 Git 副本，继承当前最佳候选，将历史指标提供给 OpenHands，随后打包源码提交到计算节点。评测命令引用的脚本在每轮提交前恢复为基线内容。源仓库及其工作区不被修改。每轮的源码快照保存在作业记录中，`parent_id` 记录谱系。

评测程序写出 `metrics.json`，内容为有限数值字典：

```python
import json
from pathlib import Path

Path('metrics.json').write_text(json.dumps({'score': 0.85, 'latency_ms': 12.3}))
```

`artifacts/` 下的普通文件、`metrics.json` 和 `RESEARCH_NOTES.md` 自动回传，管理台可下载。源码包当前支持最多 2000 个 UTF-8 文本文件、8 MiB；大型数据、权重和二进制资源应由预制执行镜像或外部数据系统提供。产物回传上限为 16 MiB，适合指标和报告，大型 checkpoint 应上传对象存储并记录引用。

内置基准使用确定性的参数搜索，**不调用 LLM**；它用于验证整套系统，不代表通用科研能力。OpenHands 模式需要单独配置有效模型凭据，API 费用由使用者的模型账户承担。

## 独立执行节点

### 硬件数量与调度配额

工作台和计算资源页中的**硬件清单**来自独立监控，不按 worker 的 `--cpus` 或 `--gpu-ids` 参数推算：

- CPU 分别显示主机逻辑 CPU、物理核、affinity 和容器 CFS 配额。例如，主机有 192 个逻辑 CPU、96 个物理核，而当前容器的 CPU 配额可为 96。
- GPU 通过 `nvidia-smi` 只读查询，显示设备型号、显存和 UUID，并应用环境中的 GPU 可见性限制。没有查询工具或驱动查询失败时显示“未探测”，不会冒充 0 张。
- 按主机标识和 GPU UUID 去重；多个 worker 上报同一台机器不会使硬件数量翻倍。必要时可用 `CLUSTER_HOST_ID` 为同一物理主机设置一致标识。
- 执行池单独显示**平台占用 / 已接入配额**。检测到 GPU 不代表已经将它授权给执行池；只读监控不会领取或启动实验。

`dev` 自动每 15 秒更新当前主机的硬件清单。远程主机可单独运行监控，无需启动 GPU worker：

```bash
research-cluster monitor --url http://CONTROL_HOST:8787
```

CPU worker 仅探测 CPU 信息；GPU worker 可以上报其主机可见的 GPU。90 秒未上报的硬件快照不计入当前清单。worker 正常停止后会立即撤回在线配额。

先在执行主机安装本项目。节点只需能通过 HTTP(S) 访问 API；启用认证时设置同一个 `CLUSTER_TOKEN`。

```bash
# CPU 主机
research-cluster worker --url http://CONTROL_HOST:8787 \
  --kind cpu --pool cpu-default --cpus 8 --memory-mb 16384 --concurrency 4

# GPU 主机：控制面不需要安装 NVIDIA 驱动或训练依赖。
research-cluster worker --url http://CONTROL_HOST:8787 \
  --kind gpu --pool gpu-default --gpu-ids 0,1 \
  --cpus 16 --memory-mb 65536 --concurrency 2 \
  --runtime docker --image YOUR_TRAINING_IMAGE
```

GPU Docker 运行方式需要执行主机安装 Docker 和 NVIDIA Container Toolkit。每个物理 GPU 应只注册到一个执行节点；同机多个 worker 的 CPU 和 GPU 清单应互不重叠，外部调度系统也应遵守其所有权。资源池可从 Web 新建，节点接入命令可直接复制。

`native` 用于可信开发代码：实施 CPU affinity、线程数、进程组取消和超时，内存作为容量预约，不提供单作业内存硬隔离。`docker` 对每个实验施加 CPU、内存、PID 限制，并关闭网络。执行镜像需要预装实验依赖。控制面和模型 API 密钥不会注入实验环境。Docker 命令由执行主机发出，控制服务无需访问 Docker socket。

OpenHands 当前运行于可信本地 Git 工作副本，其终端工具拥有研究进程的操作系统权限；它不是对抗性代码沙箱。固定脚本恢复也不能替代独立评测服务。将平台用于外部不可信代码或正式金融业务前，应增加独立评测服务、账户与权限隔离；金融业务生产服务独立部署，通过确定性接口接入研发产物。

## PostgreSQL + Temporal

```bash
cp .env.example .env
# 编辑 .env，填写非空的 CLUSTER_TOKEN。
docker compose up --build -d
```

管理台默认 <http://127.0.0.1:8787>，首次连接填写 `.env` 中的令牌。Temporal 自带 UI 在 <http://127.0.0.1:8233>。Compose 使用 PostgreSQL 保存业务数据、独立持久卷保存产物与 Temporal 状态，只启动 CPU 执行节点。

独立 Lean 镜像使用固定版本和校验和，按需启用 `lean` profile：

```bash
docker compose build api
docker compose --profile lean up --build -d
```

默认普通 CPU worker 的 affinity 为前两个条目，Lean worker 跳过这两个条目；宿主机至少需要三个可用 affinity 条目。该 profile 为验证节点增加 1 CPU 配额，不启动 GPU worker。

Compose 中的 Temporal CLI server 是**本地开发服务**。正式部署时替换为自建生产 Temporal 或托管服务；项目的 `TEMPORAL_ADDRESS` / `TEMPORAL_NAMESPACE` 可指向外部实例。正式环境还需要配置 TLS、备份、对象存储和固定镜像摘要。API 和编排器分别启动：

```bash
export CLUSTER_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/research'
export CLUSTER_ORCHESTRATOR=temporal
export TEMPORAL_ADDRESS=HOST:7233
export CLUSTER_TOKEN='your-long-random-token'
research-cluster serve --host 0.0.0.0
# 另一进程，保持相同的数据库和控制面配置
research-cluster orchestrator
```

数据库首次初始化由 API 完成，再启动编排器。CPU worker 与 GPU worker 独立扩缩容，不需要数据库配置。当前 Web 状态快照有界返回最近 500 项研究、1000 次实验和 100 条事件；完整历史保存在数据库中。

## 验证

```bash
uv run --extra dev pytest -q
uv run --extra dev python -m playwright install chromium
CLUSTER_TEST_WEB=1 uv run --extra dev pytest tests/test_web.py -q
# 下载并启动真实的临时 Temporal 本地服务：
CLUSTER_TEST_TEMPORAL=1 uv run --extra dev --extra temporal pytest tests/test_temporal.py -q
# 安装固定 Lean 后，运行实际内核检查与 CPU 队列测试：
CLUSTER_TEST_LEAN=1 uv run --extra dev pytest tests/test_verification.py -q
# 包含真实证明和浏览器交互的完整验证：
CLUSTER_TEST_LEAN=1 CLUSTER_TEST_WEB=1 CLUSTER_TEST_TEMPORAL=1 uv run --extra dev --extra temporal pytest -q
```

测试覆盖资源类型隔离、GPU 独占预约、并发领取、幂等提交、租约失效、取消竞态、认证、归档路径、真实进程执行、超时、产物回传、多轮研究闭环、研究树谱系，以及两个真实 CPU 分支同时执行。Web 和 Temporal 集成测试按环境变量选择运行。
