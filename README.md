# AI Agent 应用实战：配套代码

本仓库面向有 Java、Go 或 Python 基础的后端开发者，使用 Python 与现成开源组件逐步实现企业技术支持 Agent。TaskHub 是独立设计的合成教学产品，不包含公司源码或真实客户数据。

## 第二版：当前开发版本

新版入口位于 `examples/v2/`，实现位于 `src/support_agent/`。第 01–03 讲分别增加带资料问答、提示词对照、结构化输出与业务校验；第 04–05 讲接入真实本地查询 API 和 LangChain Agent。后续各阶段的完成情况以实际运行记录为准，不把规划当作已经实现。

新版代码在 `main` 分支持续开发，逐讲标签尚未发布；旧版 `lesson-04`、`lesson-05` 对应下方历史案例，不能作为新版入口。修订标签计划为 `lesson-NN-v2`，实际发布后再列出固定版本。

在仓库根目录使用 Python 3.12 与 uv：

```bash
uv sync --locked
test -e .env || cp .env.example .env
```

仅在尚无 `.env` 时复制模板；已有配置不要覆盖。在本地填写 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`。地址采用服务提供的 OpenAI-compatible Chat Completions Base URL，SDK 自动补请求路径。密钥不进入版本库，模型服务按实际资源计费。

如果本机必须通过 SOCKS 代理访问模型，当前环境还需安装对应支持：`uv pip install socksio==1.0.0`。这是网络环境的可选依赖，应用不自动关闭代理。`uv sync --locked` 后如缺少该包，重新安装即可。

```bash
uv run python examples/v2/01_ask.py --prepare-only --output runs/v2/01-prepared.json
uv run python examples/v2/01_ask.py --output runs/v2/01-with-docs.json
uv run python examples/v2/02_prompt.py --output runs/v2/02-first
uv run python examples/v2/03_structured.py --question 'TaskHub Webhook 的签名应该如何验证？' --output runs/v2/03-signature.json
uv run python -m pytest -q
```

输出文件/目录需使用新路径，保留每次结果。提示词对照计划为 12 次真实请求，API 错误也会记录。结构化输出先检查完整性、类型、引用和澄清一致性；引用存在不证明事实正确。模型别名 `auto` 不提供稳定底层模型身份；测试替身通过不能代替真实模型验收。

### 第 04–05 讲：运行工具查询

第 04 讲先在一个终端启动合成业务服务：

```bash
uv run uvicorn services.taskhub.app:app --host 127.0.0.1 --port 8004
```

另一个终端在相同仓库根目录运行：

```bash
uv run python examples/v2/04_agent.py --question '投递 d-104 一直失败，帮我看看怎么处理。' --output runs/v2/04-delivery.json
```

第 05 讲将服务启动端口改为 8005，再运行：

```bash
uv run python examples/v2/05_agent.py --question '投递 d-105 返回 429，现在应该怎么处理？' --output runs/v2/05-multistep.json
```

框架负责循环，内置中间件分别限制模型和工具调用次数。教学服务绑定固定项目，不代表生产鉴权。停止原因与 `business_review` 分开保存；模型返回回答也需要人工核对。

2026-10-16：干净源码快照执行 `uv sync --locked`、无需密钥的输入准备与 55 项测试通过。真实模型已完成文档问答、结构化回答、三组工具任务和一次两步查询。提示词比较 12 次中仅 3 次成功、9 次超时，不据此判断提示词质量提升。确定性故障测试不冒充线上模型表现，当前也尚无页面、会话持久化、审批写入或部署验收。

## 第一版历史案例

以下内容与命令保留旧标签语义，第二版无需先完成 Java 工具案例。

### 第一版状态

第 4 讲提供 Python 工具执行器：运行固定 Java 测试，区分通过、失败、超时与取消，清理子进程并返回结构化结果。第 5 讲在其上增加只读调查循环、Responses API 接入、证据引用校验和最小运行记录。离线协议与真实工具已验证；模型实测状态见第 5 讲说明。

## 逐讲演进的结构

```text
src/team_harness/         # 持续演进的 Agent 与 Harness 实现
  tools/java_tests.py     # 第 4 讲：受控 Java 测试工具
  investigation.py       # 第 5 讲：只读调查与模型工具循环
examples/lesson04/        # 第 4 讲入口与独立编写的教学夹具
examples/lesson05/        # 第 5 讲入口、合成日志与开发集
tests/                   # 实际行为验收
pyproject.toml            # Python 与直接依赖约束
uv.lock                  # 运行时依赖锁定
```

后续按「Agent Loop → 模型适配 → 工具调用 → 运行记录」逐步增加实际需要的模块。当前实现工具层与最小只读 Loop，不预建多 Agent 框架。团队任务与审批、模型建议、业务系统判定分别保留自己的职责。

每讲使用 `lesson-NN` 标签固定源码版本；第 4 讲为 `lesson-04`。标签只在对应版本验收后建立，不覆盖已有章节标签。Git 推送与公开发布按作者授权另行执行。

## 第 4 讲：运行

环境：macOS 或 Linux、Python 3.12、JDK 17、uv。复现本文版本：

```bash
git clone --branch lesson-04 https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
uv sync --frozen
uv run --frozen python examples/lesson04/demo.py
uv run --frozen python -m unittest discover -s tests -v
```

已有克隆可先执行 `git fetch origin --tags`，再执行 `git switch --detach lesson-04`。每讲从对应标签取得完整代码，不另发压缩包。

默认夹具独立编写，只包含 `(100, 500]` 的四个合成输入，不包含公司源码、客户数据或完整风控实现。成功返回 `status=passed`、`exit_code=0`，输出 `4 boundary checks passed`。

故障验收是明确植入的教学情形：

```bash
uv run --frozen python examples/lesson04/demo.py --scenario fail
uv run --frozen python examples/lesson04/demo.py --scenario timeout --timeout 2
uv run --frozen python examples/lesson04/demo.py --scenario cancel
```

三条命令的退出码分别为 1、124、130；这是预期结果。取消在调用者处理，执行工具完成清理后继续抛出 `CancelledError`。

已有授权访问的目标仓库可直接使用：

```bash
uv run --frozen python examples/lesson04/demo.py --backend-dir /path/to/your/backend --timeout 120
```

该入口仅运行固定的三个测试类，需要 Maven 和兼容测试类；它不是任意仓库适配器。使用其他项目时，由人修改 `run_java_tests` 中的固定测试清单，不接受模型传入 shell 命令。调用测试会产生或更新该仓库的 `target/` 构建产物，不修改业务源码。

本例通过独立 POSIX 进程组清理本次启动的后代进程，不能控制主动脱离该组的进程；它不是沙箱。暂未支持 Windows。输出使用临时文件，返回有长度限制的摘要；磁盘配额、强隔离与完整秘密检测属于后续 Worker 范围。

子进程只继承必要环境变量，不继承 API key；已知环境秘密在返回摘要中替换。工具仍可能读取本机配置或网络，不能把该教学措施理解为完整凭据隔离。

## 仓库边界

- 本仓库：配套源码、测试、可公开的脱敏 fixture、运行说明与复现所需的配置示例。
- 专栏私有文稿仓库：收费文章、提纲、研究笔记、配图源文件和写作交付记录，由作者单独管理。
- 早期私有实验项目：候选材料；需要审查、脱敏和验证后，才可将选定代码引入本仓库。

每个教学案例准备完成后，将记录对应讲次、固定 commit/tag、环境与依赖、输入、复现命令、预期结果和失败验收。文章引用固定版本，避免后续代码变动影响读者复现。

## 获取仓库

```bash
git clone https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
```

## 第 5 讲：只读排查 Agent

第 4 讲实现与命令保持不变。第 5 讲新增 `src/team_harness/investigation.py`，
入口 `examples/lesson05/demo.py`，三个独立合成样本和人工标签位于同目录。
本讲固定版本为 `lesson-05`，已有标签不移动。取得本讲代码后从仓库根目录运行：

```bash
git clone --branch lesson-05 https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
uv sync --frozen
uv run --frozen python examples/lesson05/demo.py --mode scripted --case boundary
uv run --frozen python examples/lesson05/eval.py --mode scripted
uv run --frozen python -m unittest discover -s tests -v
```

已有克隆先执行 `git fetch origin --tags`，再执行 `git switch --detach lesson-05`。

`scripted` 是固定工具选择与答案的协议替身，不调用真实模型；日志读取和 Java 测试实际执行。
`boundary` 在 value=100 的植入故障上返回测试失败，`normal` 的四边界检查通过，
`missing` 缺失输入与版本、返回 unknown。运行记录保存在 `runs/`，不自动提交。
记录中每次响应、工具结果、call_id、证据编号、停止原因均保留；未知 usage 为 null。
可用 `--max-turns 1` 观察 `step_limit` 与退出码 1，失败同样留存 JSON。

真实调用使用 OpenAI Responses HTTPS API，不读取 CLI 登录态或个人订阅。自行在当前终端
安全设置 `OPENAI_API_KEY` 与 `OPENAI_MODEL`（支持 function calling 与 json_schema 的实际可用模型），
不要把密钥放入命令历史、源码或运行记录。然后执行：

```bash
uv run --frozen python examples/lesson05/demo.py --mode agent --case boundary
uv run --frozen python examples/lesson05/eval.py --mode direct --output-dir runs/lesson05-direct
uv run --frozen python examples/lesson05/eval.py --mode agent --output-dir runs/lesson05-agent
```

API 有网络、地区、项目权限和计费条件；HTTP 超时 60 秒，不自动重试，重定向拒绝。
认证、限流或不支持的模型返回错误时停止，并在 run 中保留异常类别与 HTTP 状态。
未配置凭据时离线协议验证可以完成，但不算真实模型 Agent 跑通。

直接模式只有原问题、不取证，Agent 模式可读取限定日志和测试。两者比较的是取证流程，
不是在相同信息下证明模型能力提升。三例是开发集，不是独立测试集；布尔标签只检查状态、
特征文字和必要工具，语义是否得到证据支持仍需人工核对。引用校验只能阻止引用不存在的
编号或伪造引文，不能证明推论正确。本讲不修改业务源码、不部署服务，固定测试会启动 Java，
仍沿用第 4 讲的可信本地教学环境和 POSIX 进程边界，不是生产沙箱。
