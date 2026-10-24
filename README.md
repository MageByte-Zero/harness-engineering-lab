# AI Agent 应用实战：配套代码

本仓库面向有 Java、Go 或 Python 基础的后端开发者，使用 Python 与现成开源组件逐步实现企业研发 Agent 及应用级 Harness，训练可迁移的工具、上下文、状态、工作流和验证能力。教学输入独立编写，不包含公司源码或真实客户数据。

## 唯一应用：企业研发 Agent（当前为只读核心）

第 4–5 讲已有实现对应 `2026-10-19-systematic-agent`。课程当前规划继续扩展为理解、设计、受控开发与测试；现有源码仍是只读核心：复用 LangChain `create_agent`，在固定源码快照内搜索、读取并解释订单查询功能。

**应用源码在 `src/repo_agent/`，运行入口在 [examples/repo_reading](examples/repo_reading/README.md)。** 后续讲次继续扩展这一应用，不复制成新的项目目录。

```text
harness-engineering-lab/
├── src/repo_agent/          # 唯一应用包
│   ├── agent.py            # 任务指令、工具与框架循环的组装
│   ├── execution.py        # 一次运行的生命周期、预算结果与事件留存
│   ├── tools.py            # 有界源码快照、搜索与读取
│   ├── model.py            # 模型客户端与 MODEL_* 配置
│   └── checks.py           # 离线记录核对，不承担答案语义验收
├── examples/repo_reading/   # 薄 CLI、固定任务与独立人工参考
├── fixtures/               # 正常输入、缺资料输入；均为合成源码
└── tests/repo_agent/        # 当前应用的边界与框架回归测试
```

`fixtures/order-service` 与 `fixtures/order-service-missing` 是不同验收输入，需要同时保留。`runs/` 是运行时生成的本地记录，不提交 Git。模型配置沿用根目录 `.env.example`。

当前已发布实现见下方固定快照，本次版本收敛了目录与规范；尚未发布 `lesson-04-v2` 标签；完整命令、限制和人工参考见上述运行说明。历史代码和标签保留，不视为新课程的前置验收。

第 5 讲沿用同一个 Agent，增加四条固定任务、停止原因分类和离线记录检查；入口 `examples/repo_reading/05_tasks.py`，判定参考与源码输入分开。运行与复现命令见同目录 README；尚未发布 `lesson-05-v2` 标签。

## 获取与运行

```bash
git clone https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
uv sync --frozen
```

使用 Python 3.12、uv 和 ripgrep。根目录 `.env.example` 提供 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME` 的配置位置；已有 `.env` 不要覆盖。完整运行、四条任务及判定说明见 [仓库阅读 Agent](examples/repo_reading/README.md)。

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

当前已用 LangChain create_agent；尚未接入 OpenAI Agents SDK、Deep Agents、Codex SDK 或 Claude Agent SDK。正式扩展前，用当前固定任务验证 OpenAI Agents SDK 与现有实现的工具、资源兼容、事件、状态、审批和取消，确定一种后续主底座；不在同一应用里并行维护框架副本。Deep Agents 的预组装能力在需要时验证。

[OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents/sdk)用于构建自己的 Agent/Harness；[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)用于复用成熟 Codex 研发执行；App Server 是更底层的集成接口。状态、审批和恢复的机制按实际采用的底座解释，不套用另一框架接口。

资料获取从直接读取、按需搜索和现有系统查询开始，向量/混合索引由实际漏检和成本决定，不强制增加向量数据库。SDK 和索引组件在真实验证后才加入锁文件，当前运行与 fixture 不因规划改变。
