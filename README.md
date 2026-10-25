# AI Agent 应用实战：配套代码

本仓库面向有 Java、Go 或 Python 基础的后端开发者，使用 Python 与现成开源组件逐步实现企业研发 Agent 及应用级 Harness，训练可迁移的工具、上下文、状态、工作流和验证能力。教学输入独立编写，不包含公司源码或真实客户数据。

## 唯一应用：企业研发 Agent（当前为只读核心）

第 4–6 讲当前实现对应 `2026-10-25-openai-agents-sdk`。课程当前规划继续扩展为理解、设计、受控开发与测试；现有源码仍是只读核心：复用 OpenAI Agents SDK 的 `Agent` 和 `Runner`，在固定源码快照内列举路径、搜索、读取并解释订单查询功能。

**应用源码在 `src/repo_agent/`，运行入口在 [examples/repo_reading](examples/repo_reading/README.md)。** 后续讲次继续扩展这一应用，不复制成新的项目目录。

```text
harness-engineering-lab/
├── src/repo_agent/          # 唯一应用包
│   ├── agent.py            # 任务指令、工具与框架循环的组装
│   ├── execution.py        # 一次运行的生命周期、预算结果与事件留存
│   ├── tools.py            # 有界源码快照、路径清单、搜索与读取
│   ├── model.py            # 模型客户端与 MODEL_* 配置
│   └── checks.py           # 离线记录核对，不承担答案语义验收
├── examples/repo_reading/   # 薄 CLI、固定任务与独立人工参考
├── fixtures/               # 正常输入、缺资料输入；均为合成源码
└── tests/repo_agent/        # 当前应用的边界与框架回归测试
```

`fixtures/order-service` 与 `fixtures/order-service-missing` 是不同验收输入，需要同时保留。`runs/` 是运行时生成的本地记录，不提交 Git。模型配置填写在根目录 `.env`；`.env.example` 仅为公开空值模板，程序不会自动读取它。

第 4–5 讲 SDK 版固定入口为 `lesson-04-sdk-20261025` 与 `lesson-05-sdk-20261025`；两讲共用同一代码版本。完整命令、限制和独立参考见上述运行说明。69d2233 对应迁移前的 LangChain 原始运行，历史标签和证据保持原义。

第 5 讲沿用同一个 Agent，增加四条固定任务、停止原因分类和离线记录检查；入口 `examples/repo_reading/05_tasks.py`，判定参考与源码输入分开。运行与复现命令见同目录 README；尚未发布 `lesson-05-v2` 标签。

第 6 讲固定入口为 `lesson-06-tools-20261025`，沿同一应用完善工具契约：`list_source_files` 只列绑定快照的路径，所有工具结果带 `scope: snapshot_only` 和 `snapshot_id`；读取另给出字符截断与行段续读信息。离线检查支持这些字段及旧版记录。复现步骤与比较边界见[第6讲说明](examples/repo_reading/README.md#第-6-讲工具契约与快照路径)。

## 获取与运行

```bash
git clone https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
uv sync --frozen
```

使用 Python 3.12、uv 和 ripgrep。在上述仓库根目录执行 `cp -n .env.example .env`，然后用编辑器填写 `.env` 的 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`；已有 `.env` 不要覆盖。三个值来自模型服务控制台或企业网关管理员。真实Key不提交，完整配置步骤、四条任务及判定说明见 [仓库阅读 Agent](examples/repo_reading/README.md#准备)。

## 后续架构与分包约定

目前代码规模适合单包内按模块分工。后续按入口、应用用例、Agent 定义、运行时、工具、基础设施逐步分包；实际新增对应能力时才创建目录。业务流程决定任务何时接受或交付，运行时负责模型与工具循环，模型 SDK 只处理模型通信。工具授权由程序检查，执行隔离由执行环境落实，不能仅靠提示词。

CLI/API 复用同一应用用例；运行时产生可关联的事件供入口展示和记录；离线检查与语义验收保持独立。会话/运行/事件、上下文装配、取消/预算及持久化按具体课程需求加入。Claude Agent SDK 或 Codex 的集成属于运行时接入，不能当作换一个模型 URL；不预建多运行时抽象，不同时维护多套循环。

设计借鉴 [Claude Code 的循环与上下文管理](https://code.claude.com/docs/en/how-claude-code-works)、[Codex 的客户端接口及 Thread/Turn/Item](https://developers.openai.com/codex/app-server) 和 [审批与隔离的区分](https://learn.chatgpt.com/docs/agent-approvals-security#sandbox-and-approvals)。上述分包是本项目的工程取舍，不声称是两者的内部源码结构。具体新增约束见 [AGENTS.md](AGENTS.md)。

## 历史版本与仓库边界

当前工作区已移除早期 `examples/lesson04`、`examples/lesson05`、`examples/v2`、`src/team_harness`、`src/support_agent` 及其专属服务、数据和测试。旧代码保留在 Git 历史，不另建归档目录。历史 `lesson-04`、`lesson-05` 标签含义不变；TaskHub 可查 [5f865ee 快照](https://github.com/MageByte-Zero/harness-engineering-lab/tree/5f865ee49f7f706a4f3a26346beff35322b73018)，清理前的仓库阅读 Agent 可查 [69d2233 快照](https://github.com/MageByte-Zero/harness-engineering-lab/tree/69d2233deea0a7c5a637d45f4ce171cf18962c2a)。使用独立 checkout 查阅旧版，避免混用旧入口与当前依赖。

本仓库仅承载源码、测试、独立合成输入、配置示例和复现说明。付费文稿、研究笔记、配图和运行交付记录留在私有文稿仓库；密钥、公司源码和真实业务数据不提交。完整应用的 API、会话、授权和部署仍是后续课程增量。

## 文件位置与新功能约束

本仓库是唯一教学实现位置。程序、依赖、合成输入和测试不在 pay-columns、research-notes 或 experiments 中开发；付费正文、提纲、配图和研究记录不复制到这里。具体位置与创建前检查见 AGENTS.md。

设计方案生成、业务 API 连接、按需上下文获取、受控修改和测试是后续目标，按实际任务逐步接入同一应用。当前不预建空包、不新增第二套 Agent 工程、不把角色名称自动实现成多个 Agent。运行产物与可写任务副本归入忽略的 runs/<run-id>，独立测试和参考不交给被测 Agent 修改。

## 主底座与 SDK 选型边界

主线采用 OpenAI Agents SDK（Python），当前 repo_agent 已迁移到 Agent / Runner / function_tool / RunHooks，锁定 openai-agents 0.24.0。第 4–5 讲在同一实现中提供只读工具、模型轮次与工具总预算、停止分类和离线检查。LangChain 旧源码及运行记录从历史固定版本获取，不在活动源码中并行维护。

[OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents/sdk)用于构建自己的 Agent/Harness；[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)用于复用成熟 Codex 研发执行；App Server 是更底层的集成接口。后续状态、审批和恢复复用 OpenAI Agents SDK 原生机制，企业授权、Task/Run、隔离与接受条件由应用实现。不引入 LangGraph/Deep Agents 作为主线运行时。

资料获取从直接读取、按需搜索和现有系统查询开始，向量/混合索引由实际漏检和成本决定，不强制增加向量数据库。SDK 已按实际验证写入锁文件；新增索引组件仍须有对应任务与验证，不能因为规划而增加依赖。

SDK 用于复用循环、工具派发、流式事件、会话/运行状态、审批和追踪等机制。实现仍需提供业务工具、身份/数据范围、持久化配置、隔离执行和任务评测。模型/工具边界回归与真实任务分别验收，任务结束不自动等于答案正确；后续会话、审批、MCP、Web 与执行隔离尚待对应讲次实现。
