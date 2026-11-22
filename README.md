# AI Agent 应用实战：配套代码

使用 Python 和 OpenAI Agents SDK 持续开发同一个企业研发 Agent。面向有 Java、Go 或 Python 基础的后端开发者；源码、规程、业务 HTTP 服务都是独立编写的合成教学输入，不包含公司代码或客户数据。

## 当前实现

第 20 讲固定入口为 `lesson-20-workspace-20261122`。本版从 `lesson-19-approval-20261118` 演进，接入批准后的隔离工作区准备。应用可以读取限定资料、查询合成业务接口、生成带来源的结构化回答，并提供网页/CLI、进度事件、取消、连续会话和本地持久化。修改计划通过类型化提交工具交付，检查来源关联、目标文件和约束/接受条件覆盖后，保存为待审产物。任务方法按需加载；业务查询可以直接走 HTTP，也可以经过受控的本地 MCP stdio 服务。

这些能力共用 `src/repo_agent/` 的一个应用包、一个 SDK Runner。启用 `--prepare-workspace` 后，申请人能从已批准交接准备工作区。应用复查授权和来源摘要，在 Docker 中验证只读基线与逐文件可写挂载，保存准备记录。模型工具仍负责调查和计划；可写探针由应用固定，执行在任务副本中。

| 源文件 | 当前职责 |
| --- | --- |
| `access.py` | 本地预发凭据验证、可信项目配置、成员资格与私有资源归属检查 |
| `agent.py`、`model.py` | 指令与工具组装；MODEL_* 客户端配置 |
| `execution.py` | SDK 往返、预算、实际模型输入和工具事件留存 |
| `tools.py`、`retrieval.py` | 限定文本快照的读取、搜索和固定/按需取材 |
| `business.py`、`mcp_business.py` | 合成业务 HTTP 客户端、只读工具及固定 MCP 适配 |
| `output.py`、`checks.py` | 结构化结果、来源关联和离线记录核对 |
| `planning.py` | 需求与计划类型、提交工具、关联/覆盖检查和产物版本绑定 |
| `approval.py` | 计划送审、指定审批人、决定校验与交接 |
| `workspace.py` | 核对已批准交接、物化工作区、Docker 边界探针与清理 |
| `application.py`、`web.py` | 用例与 Run 状态；FastAPI、SSE 和取消接口 |
| `conversation.py`、`persistence.py` | 完整轮次选择/提交、SQLite 存储、重启与范围校验 |
| `skills.py` | 受信本地任务方法包的按需加载和内容版本绑定 |

包内有 19 个 Python 文件（含 `__init__.py`）和一个网页模板。`examples/repo_reading/` 是薄入口和运行说明；`fixtures/` 是输入，`tests/` 是验证，忽略的 `runs/` 是每次运行的输出。

## 获取固定版本与最小运行

需要 Git、Python 3.12、uv、ripgrep。第一次获取：

```bash
git clone --branch lesson-20-workspace-20261122 --single-branch https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
```

已有仓库则先保存自己的改动，再执行 `git fetch origin tag lesson-20-workspace-20261122` 和 `git switch --detach lesson-20-workspace-20261122`。已发布标签保持原义，后续修订使用新标签。

在根目录 `.env` 或进程环境配置 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`。`.env.example` 是空值模板，程序不读取模板；已有 `.env` 不要覆盖，密钥不提交也不写进命令。模型需支持 Chat Completions 工具调用和本讲的结构化输出组合，标识 `auto` 不保证服务端模型身份稳定。

在已配置好的仓库根目录，一条命令启动同一应用与合成业务服务：

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/project-access \
  --access-config fixtures/access/projects.json \
  --port 18064 --business-port 18824
```

打开 http://127.0.0.1:18064；首次启动的预发凭据在 `runs/project-access/local-credentials.json`，不要公开或提交。用户凭据、项目选择、有权规划、越权拒绝、CLI、MCP 与重启步骤见[第18讲运行说明](examples/repo_reading/18_README.md)。Alice 仅能请求 alpha，Bob 能请求 alpha/beta，会话和结果各自私有。Bob 另获 alpha 审批权限，只能处理明确指定给他的申请。源目录、业务凭据和允许订单由可信配置决定，模型不能选择。

这是本地预发凭据示例，不是企业 SSO。成员配置与凭据轮换在重启后生效；退出浏览器仅清除当前 Cookie 和显示内容，不吊销该凭据。旧的无归属数据保留但不开放。带归属的数据目录不能去掉访问配置后通过旧模式打开。旧命令不带 `--access-config` 时仍用于可信本地单用户演示，不能当团队授权入口。

可追加 `--business-transport mcp` 使用原 stdio 桥，或 `--skills-dir fixtures/skills` 启用任务方法。调查、修改计划共用 `12_app.py` 和一个 Runner。计划输入与程序检查见[第17讲运行说明](examples/repo_reading/17_README.md)；`plan_for_review` 只是可交给人审查，未获批准，也未修改或测试源码。

第 20 讲的 Docker 镜像、准备入口、观察字段和清理验证见[第20讲运行说明](examples/repo_reading/20_README.md)。使用有权限访问本地 Docker 的可信开发环境。

## 验证与限制

```bash
uv run --frozen python -m pytest -q
uv run --frozen python examples/repo_reading/05_check.py runs/mcp-agent/records/RUN_ID.json
```

第二条的 `RUN_ID` 换成实际编号。测试中真实协议/HTTP、可控模型及故障注入各有明确边界；测试通过不代表任意真实模型都会选对工具，来源关联通过也不是自动语义批准。实际输入默认最多 60,000 字符，非 token 数；模型/工具预算分别为 12/16。

持久化为本地单进程工作区，最多 32 个会话、64 次保留运行、2 个活动 Run。重启不重置容量；旧的未完成运行标记中断，不自动重放工具。容量为应用全局，尚无按用户配额、归档、多 worker 或共享存储部署。

MCP Python 2.3.0、Agents SDK 0.24.0 与其余依赖由 `uv.lock` 固定。MCP 入口是固定受信本地子进程，只开放两个已核对输入契约的业务工具，不是任意远端 MCP 工具平台。凭据不交给模型。子进程不是安全沙箱，读操作取消也不保证撤回已发出的请求。

## 分讲入口与历史

- [第4–9讲](examples/repo_reading/README.md)：只读循环、工具与取材；旧脚本保留为阶段入口。
- [第10讲](examples/repo_reading/10_README.md)、[第11讲](examples/repo_reading/11_README.md)：业务 API 与结构化结果。
- [第12讲](examples/repo_reading/12_README.md)：页面/CLI、事件和取消。
- [第13讲](examples/repo_reading/13_README.md)、[第14讲](examples/repo_reading/14_README.md)：连续会话与持久化。
- [第15讲](examples/repo_reading/15_README.md)、[第16讲](examples/repo_reading/16_README.md)：任务方法与 MCP。
- [第17讲](examples/repo_reading/17_README.md)：修改计划、来源与覆盖检查、保存和重启查看。
- [第18讲](examples/repo_reading/18_README.md)：可信身份、项目取材、私有会话/结果、HTTP/MCP 授权和撤权重启。
- [第19讲](examples/repo_reading/19_README.md)：持久送审、批准与拒绝、一次性交接和重启后的恢复验证。
- [第20讲](examples/repo_reading/20_README.md)：从批准交接准备工作区，验证工具路径、进程范围和超时清理。

历史固定版本保持不变。`lesson-04-sdk-20261025` / `lesson-05-sdk-20261025` 是 SDK 起步版；`lesson-16-mcp-20261117` 是第17讲基线。迁移前 LangChain 实现可从 `69d2233` 历史查看，不在当前源码中并行维护。

## 后续架构与仓库边界

后续沿同一应用接入受限执行、独立验证和恢复，再完善评测、发布与部署。按出现的职责逐步分包。当前模型执行仍由同一个 SDK Runner 驱动。

本公开仓库只保存源码、测试、合成输入、配置样例与开发者说明。付费正文、配图和写作证据在独立私有文稿库；真实运行副本、日志和补丁归入忽略的 `runs/<run-id>`，密钥和企业数据不发布。`fixtures/order-service` 与 `fixtures/order-service-missing` 是两个验收输入，均需保留。旧 `team_harness` 等工程仅查 Git 历史，不恢复平行应用；具体开发约束见 [AGENTS.md](AGENTS.md)。
