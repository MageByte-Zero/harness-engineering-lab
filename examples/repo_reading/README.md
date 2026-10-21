# 第 4–5 讲：仓库阅读 Agent

对应课程修订 `2026-10-19-systematic-agent`。旧 examples/v2、lesson-04、lesson-05 含义不变。本目录是当前课程入口；第 4、5 讲实现随提交提供，修订标签尚未发布。读者按文章给出的固定 commit 获取同一份源码。

## 准备

在仓库根目录运行，需要 Python 3.12、uv 与 ripgrep（`rg --version`）。模型资源必须支持 Chat Completions 工具调用。根目录 `.env` 使用现有 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`，参考 `.env.example`；不把密钥写进命令、源码或结果文件。

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

终端 `[request]` 是模型请求，`[result]` 是工具返回；两者用调用 ID 对应。JSON 保留指令、输入源码及哈希、版本、模型配置的非秘密字段、全部已收到消息、回答与结束原因；响应可见的模型标识、请求标识在消息元数据中。服务没有提供的指标不会猜测补齐。错误只保留类型和 HTTP 状态，不记录可能含秘密的原始异常文本。

`answer_returned` 表示至少有一次成功读取并返回了回答，**不表示语义正确或任务验收通过**。`no_source_read` 阻止把没有实际读取的回答算作正常源码调查。失败保留已收到事件，退出码为 1。第 5 讲把同一运行逻辑提取到 `src/repo_agent/execution.py`，第 4 讲命令、正常结束标识与拒绝覆盖保持有效；原来笼统的 `error` 细分为模型/工具预算耗尽、模型/工具超时、工具错误、输入错误等，旧记录不改写。

模型客户端 `timeout=90`、无隐式重试，并不等于整次运行 90 秒总期限；框架限制单次运行最多 12 次模型请求、16 次工具请求。一次模型回复可以包含多条工具请求；当前 `exit_behavior="error"` 会在超额批次进入工具执行前报错，不能把提出的请求都算作已执行。搜索的每次 rg 子进程另有 3 秒超时。

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
