# AI Agent 应用实战：配套代码

本仓库面向有 Java、Go 或 Python 基础的后端开发者，使用 Python 与现成开源组件逐步实现代码库理解与排障助手。教学输入独立编写，不包含公司源码或真实客户数据。

## 当前课程：先理解机制，再实现仓库阅读 Agent

对应课程修订 `2026-10-19-systematic-agent`。第 1–3 讲为概念与架构，第 4 讲的新实践入口是 [examples/repo_reading](examples/repo_reading/README.md)：复用 LangChain `create_agent`，在固定源码快照内搜索、读取并解释订单查询功能。正常输入和缺资料输入独立保留，模型接入沿用根目录 MODEL_* 配置。

当前实现随本次提交提供，尚未发布 `lesson-04-v2` 标签；完整命令、限制和人工参考见上述运行说明。历史代码和标签保留，不视为新课程的前置验收。

第 5 讲沿用同一个 Agent，增加四条固定任务、停止原因分类和离线记录检查；入口 `examples/repo_reading/05_tasks.py`，判定参考与源码输入分开。运行与复现命令见同目录 README；尚未发布 `lesson-05-v2` 标签。

## 获取与运行

```bash
git clone https://github.com/MageByte-Zero/harness-engineering-lab.git
cd harness-engineering-lab
uv sync --frozen
```

使用 Python 3.12、uv 和 ripgrep。根目录 `.env.example` 提供 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME` 的配置位置；已有 `.env` 不要覆盖。完整运行、四条任务及判定说明见 [仓库阅读 Agent](examples/repo_reading/README.md)。

## 历史版本与仓库边界

历史 `lesson-04`、`lesson-05` 标签及其实现保留。TaskHub 案例的旧说明可查 [5f865ee 快照](https://github.com/MageByte-Zero/harness-engineering-lab/tree/5f865ee49f7f706a4f3a26346beff35322b73018)；它们不是当前课程的学习入口。

本仓库仅承载源码、测试、独立合成输入、配置示例和复现说明。付费文稿、研究笔记、配图和运行交付记录留在私有文稿仓库；密钥、公司源码和真实业务数据不提交。完整应用的 API、会话、授权和部署仍是后续课程增量。
