# Harness 企业实战：配套代码

本仓库是「Harness 企业实战专栏」的公开代码仓库，面向 Java 后端开发者，逐步用 Python 构建团队研发 Agent 工作系统。

## 当前状态

第 4 讲提供 Python 工具执行器：运行固定 Java 测试，区分通过、失败、超时与取消，清理子进程并返回结构化结果。尚未接入模型或 Agent Loop。

## 逐讲演进的结构

```text
src/team_harness/         # 持续演进的 Agent 与 Harness 实现
  tools/java_tests.py     # 第 4 讲：受控 Java 测试工具
examples/lesson04/        # 本讲入口与独立编写的教学夹具
tests/                   # 实际行为验收
pyproject.toml            # Python 与直接依赖约束
uv.lock                  # 完整依赖锁定
```

后续按「Agent Loop → 模型适配 → 工具调用 → 运行记录」逐步增加实际需要的模块。当前只实现工具层，不预建空目录或多 Agent 框架。团队任务与审批、模型建议、业务系统判定分别保留自己的职责。

每讲使用 `lesson-NN` 标签固定源码版本；第 4 讲为 `lesson-04`。标签只在对应版本验收后建立，不覆盖已有章节标签。Git 推送与公开发布按作者授权另行执行。

## 第 4 讲：运行

环境：macOS 或 Linux、Python 3.12、JDK 17、uv。复现本文版本：

```bash
git checkout lesson-04
uv sync --frozen
uv run --frozen python examples/lesson04/demo.py
uv run --frozen python -m unittest discover -s tests -v
```

尚未推送标签时，使用作者提供的本讲 ZIP 资料包，在解压根目录运行以上命令（省略 `git checkout`）。ZIP 与标签源码一致；不要假设 GitHub 已经存在本地标签。

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
