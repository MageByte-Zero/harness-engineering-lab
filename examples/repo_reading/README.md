# 第 4–6 讲：仓库阅读 Agent

对应课程修订 `2026-10-25-openai-agents-sdk`，使用 OpenAI Agents SDK 0.24.0。本目录是唯一课程运行入口，共用 `src/repo_agent/` 的应用源码；第 4、5 讲共用同一实现，固定版本入口由下方标签和文章给出。读者按文章给出的固定 commit 获取同一份源码。旧示例已从当前工作区移除，历史标签与查看方式见[仓库说明](../../README.md#历史版本与仓库边界)。

## 准备

在仓库根目录运行，需要 Python 3.12、uv 与 ripgrep（`rg --version`）。模型资源必须支持 Chat Completions 工具调用。根目录 `.env` 使用现有 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`，参考 `.env.example`；不把密钥写进命令、源码或结果文件。

根目录指包含 `pyproject.toml` 的 `harness-engineering-lab/`，不是 `examples/repo_reading/`。克隆只得到空值模板 `.env.example`，程序不会自动读取模板。首次在根目录执行下面命令创建实际配置文件，已有文件不会被覆盖：

```bash
cp -n .env.example .env
```

用编辑器填写 `.env` 的三个值：`MODEL_API_KEY` 是模型服务的API密钥，`MODEL_BASE_URL` 是服务提供的API基地址，`MODEL_NAME` 是该服务的实际模型ID。它们来自同一模型服务控制台或企业网关管理员，不是网页登录地址或模型的展示名称。

`src/repo_agent/model.py` 加载当前执行目录的 `.env`，已有同名进程环境变量优先。点开头的文件可用 `ls -a` 查看；真实配置留在被Git忽略的 `.env`，公开 `.env.example` 只保留空值。不要输出密钥来排查配置。

```bash
uv sync --frozen
uv run --frozen python examples/repo_reading/04_agent.py --help
```

Java 文件是独立编写的只读教学输入，包含入口、业务调用、内存数据来源与异常映射；没有 Maven 工程、数据库或 Spring Boot 启动入口。无需 JDK，不把源码解释当成 HTTP 实测。

## 运行与核对

```bash
uv run --frozen python examples/repo_reading/04_agent.py --repo fixtures/order-service --output runs/repo-reading-normal-01.json
uv run --frozen python examples/repo_reading/04_agent.py --repo fixtures/order-service-missing --output runs/repo-reading-missing-01.json
uv run --frozen python -m pytest tests/repo_agent -q
```

相同输出路径不会被覆盖。再次运行改用 `-02.json` 等新文件名。使用 `--question '你的问题'` 改任务；`--repo /path/to/small/source` 由人选择输入。先人工检查材料是否适合提交给当前模型服务；这个工具不是任意企业仓库的完整秘密检测器。

终端 `[request]` 是模型请求，`[result]` 是工具返回；两者用调用 ID 对应。JSON 保留指令、输入源码及哈希、版本、模型配置的非秘密字段、SDK 生命周期回调的归一化观察、回答与结束原因。`event_format=normalized-lifecycle-v1` 表明它不是原始网络包；`requested_model` 只记录配置名称，不能据此确定兼容服务实际调用的底层模型。服务没有提供的指标不会猜测补齐。错误只保留类型和 HTTP 状态，不记录可能含秘密的原始异常文本。

`answer_returned` 表示至少有一次成功读取并返回了回答，**不表示语义正确或任务验收通过**。`no_source_read` 阻止把没有实际读取的回答算作正常源码调查。失败保留已收到事件，退出码为 1。同一运行逻辑位于 `src/repo_agent/execution.py`，第 4 讲命令、正常结束标识与拒绝覆盖保持有效；原来笼统的 `error` 细分为模型/工具预算耗尽、模型/工具超时、工具错误、输入错误等，旧记录不改写。

模型客户端设置 `timeout=90`、`max_retries=0`，并不等于整次运行 90 秒总期限；SDK 的 `Runner.run_sync(..., max_turns=12)` 限制模型轮次；应用通过 `RunHooks.on_llm_end` 累计工具请求，超过 16 条时在新批次进入执行前抛出 `ToolBudgetExceeded`。一次模型回复可以包含多条工具请求，两种预算不能互相替代；提出的请求不等于已执行。SDK 可能包装工具异常，应用识别超时原因后保存停止类别。搜索的每次 rg 子进程另有 3 秒超时。

工具只接触启动时加载的快照：支持 `.java/.go/.py/.sql/.md`，跳过隐藏路径和符号链接。最多 100 个文件、单文件 100 KB、总共 2 MB；超限明确失败。加载后，工具不再访问用户给出的文件路径，搜索在快照文本上使用 rg 字面匹配。每次最多 20 处匹配；读取最多 120 行、12000 字符，截断明确标记。它适用于可信的小型本地教学输入，不防御快照装载时恶意并发修改目录，不是安全沙箱或完整仓库接入服务。

## 独立人工参考

参考答案放在本说明，不放进 Agent 的源码输入目录。正常任务须从实际工具观察支持以下关系，允许不同搜索顺序和表述：

| 判断 | 固定输入中的位置 |
|---|---|
| 查询入口是 `GET /orders/{id}`，调用 `service.find(id)` | `src/OrderController.java:8–20` |
| 服务调用 `repository.findById(id)`，不存在时抛出 `OrderNotFound` | `src/OrderService.java:13–15` |
| 数据来自内存 Map，存在合成订单 o-101，没有数据库 | `src/OrderRepository.java:8–13` |
| 异常类型继承 RuntimeException | `src/OrderNotFound.java:3–6` |
| 异常映射明确返回 404、`ORDER_NOT_FOUND` | `src/ApiExceptionHandler.java:8–12` |

缺资料输入只移除了 `ApiExceptionHandler.java`，其余文件完全一致。可以确认入口、数据读取和抛出的异常；无法从当前输入确认 HTTP 状态及错误体，不能把正常输入的结论带过去。模型推测与读过的事实应分开。自动测试证明程序边界与框架消息往返，不能替代上述实际模型任务的人工核对。

## 第 5 讲：不同任务、停止原因和初始检查

四条任务定义在 `05_tasks.json`，[独立判定参考](05_reference.md) 与 fixture 分开。模型只收到问题、既有指令和实际调用的工具结果，没有参考答案。新入口继续使用同一个 Agent、工具和 MODEL_* 配置，不增加模型订阅。

在仓库根目录运行，每条两次，旧文件不会被覆盖：

```bash
for task in full-chain data-source missing-mapping incident; do
  for trial in 01 02; do
    uv run --frozen python examples/repo_reading/05_tasks.py --task "$task" --output "runs/repo-reading-05/${task}-${trial}.json"
  done
done
```

一条命令失败也保留 JSON，不删除后重跑挑结果。重新做整组实验请使用新的目录。`incident` 的线上报错是假设问题，Agent 没有日志或执行权限，只能给静态依据、待验证假设和所需补充材料。

离线检查与确定性边界检查：

```bash
uv run --frozen python examples/repo_reading/05_check.py runs/repo-reading-05/full-chain-01.json
uv run --frozen python -m pytest tests/repo_agent -q
```

`05_check.py` 检查保存的源码哈希、已收到的请求/结果关联、成功读取和搜索观察与源码的一致性；显示请求、结果、成功读取和重复请求数量。`ok: true` 只表示这些机械检查通过；即便一条预算中断记录完整，它也可能通过记录检查。`semantic_review` 始终为 `pending`，按独立参考另行核对答案，允许合理替代路径。

脚本模型替身会稳定制造重复搜索、一次超额工具批次、搜索不可用、工具异常和模型请求超时，验证停止/保存逻辑，**不代表真实模型会作出这些选择**。当前只读任务不实现自动重试、总运行期限、暂停后恢复或自动模型裁判。

## SDK 与应用各负责什么

`Agent` 绑定指令、模型与 `function_tool`；`Runner` 派发工具、回填观察并继续调用模型。应用保留快照范围、工具总预算、输出预占、停止分类和独立参考。使用 `RunHooks` 观察模型与工具完成事件，不手写模型/工具循环。当前不消费 token 流，Web/SSE 在后续讲次接入。

本例使用 SDK 的 `OpenAIChatCompletionsModel` 连接现有兼容资源，不代表任意兼容服务支持 SDK 全部功能。当前 `RunConfig(tracing_disabled=True)` 关闭远端 tracing，运行证据仅写本地 JSON；后续追踪接入需另行配置和验证。旧 LangChain 版本与输出从历史固定提交获取，不与 SDK 版混用。

## 第 6 讲：工具契约与快照路径

第4–5讲固定版本保持原义。当前工具在同一应用中演进为三种分工：

| 工具 | 能回答的问题 | 结果不能支持的判断 |
| --- | --- | --- |
| `list_source_files()` | 快照收录了哪些路径和文件版本 | 完整磁盘目录、仓库不存在某文件 |
| `search_code(keyword)` | 已收录文件的内容在哪里包含字面关键词 | 文件名是否存在、未匹配概念一定不存在 |
| `read_file(path, start_line, end_line)` | 快照内某路径、某行段的内容 | 运行过服务、验证过HTTP响应 |

每个结果的 `scope` 是 `snapshot_only`，`snapshot_id` 绑定排序后的路径/文件哈希清单；它标识这次输入，不是外部仓库的Git提交，也不能证明结果可信或安全。快照装载规则不变，尤其 `.xml/.yml/.properties` 未被收录；工具没有让模型指定磁盘根目录。列表无参数且完整列出受100文件上限约束的快照，明确 `repository_complete: false`。模型凭实际任务选择工具，不强制先列目录。

搜索成功但没有命中是 `status: ok, matches: []`；已知搜索服务失败是 `unavailable`。空白/过长/多行关键词是 `invalid_keyword`；空路径是 `invalid_path`；快照未收录路径是 `not_found`；越界路径是 `denied`；不合法行段是 `invalid_range`。Schema/Pydantic先检查行号类型与大于等于1，函数再检查起止和范围。`failure_error_function=None` 使未处理异常停止并由应用留存，未伪装成空成功；搜索超时和未知故障的既有停止分类保留。

`read_file` 仍最多120行/12000字符。`truncated` 表示返回内容并未覆盖到文件末尾，`content_truncated` 专指本次行段被字符上限截断。`next_start_line` 非空时，下一次同时设置 `start_line=next_start_line`、`end_line=min(start_line+119,total_lines)`；不要只改start而保留默认end=120。字符截断时该字段为null，先缩小行段；如果单行已经超限，本工具不能完整返回该行，须报告缺口而不是跳过剩余字符。

新记录声明 `tool_contract_version: 2`。离线检查核对每个结果的范围与快照标识、完整路径清单及读取续接字段，并继续核对旧版无这些字段的记录。它仍不判答案语义。真实比较同时修改了工具能力、描述、结果及有关指令，不能把变化单独归因于名称或某句话。

使用现有任务入口复现本讲重点任务，每条两次，文件名须未被使用：

```bash
uv sync --frozen
uv run --frozen python -m pytest -q
for task in data-source missing-mapping; do
  for trial in 01 02; do
    uv run --frozen python examples/repo_reading/05_tasks.py --task "$task" --output "runs/repo-reading-06/${task}-${trial}.json"
  done
done
uv run --frozen python examples/repo_reading/05_check.py runs/repo-reading-06/data-source-01.json
```

与旧版比较时先在第5讲固定版本执行并写入另一个输出目录，再取得第6讲固定版本执行同组命令；保持输入、问题、MODEL_*与预算相同。不要在脏工作区切换版本，不覆盖已有结果。兼容资源的配置名不保证底层模型身份固定；少量任务不能证明质量普遍提升。逐项核对Map数据来源、缺映射时状态/响应体未知，以及回答有没有越过快照范围；路径列表调用成功本身不算答案通过。独立参考继续使用 `05_reference.md`，不发送给模型。
