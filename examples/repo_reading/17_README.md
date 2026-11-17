# 修改计划：从需求到待人工审查产物

使用同一 `repo_agent`、SDK Runner 和 `12_app.py`。第17讲基线为 `lesson-16-mcp-20261117`；本讲固定版本为 `lesson-17-planning-20261117-r1`。依赖沿用锁文件中的 Agents SDK 0.24.0、MCP 2.3.0 和 Python 3.12，无新增依赖。

## 环境与一条命令

从固定 tag 获取代码后，在仓库根目录配置已有 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`，需要 uv、Python 3.12、ripgrep 和可访问的模型接口。端口需空闲。

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/change-plan \
  --port 18062 --business-port 18822 \
  --plan-request fixtures/planning/add-error-message.json \
  --question '只为 code/src/ApiExceptionHandler.java 增加固定 message=订单不存在 提出最小计划。先读取这个文件和 code/src/OrderController.java，随后用 submit_change_plan 提交一项修改步骤与输入两项接受条件的独立检查。事实引用实际观察编号。不要读取 policy/ 或查询业务数据，不修改、不执行。'
```

这里给出了具体文件和取材范围；资料定位已在此前讲次训练，本讲观察计划功能的接线。`--plan-request` 提供原始约束与接受条件，`--question` 补充本轮范围；两者都参与产物版本绑定。省略 `--question` 时采用需求文件的 goal，但模型取材路径和结果可能不同，不能把上面这次实测推广到任意提问。

命令使用当前 HTTP 适配。可以加 `--business-transport mcp` 沿第16讲路径连接，或加 `--skills-dir fixtures/skills` 暴露方法目录；规划不要求每次调用业务工具或加载 Skill。完整目录中增加了 `plan-change` 方法包，方法不是事实或授权。

程序启动合成业务服务并在退出时清理；上述任务只读取源码，不查询订单。Java 文件是静态教学材料，本讲没有编译或启动 Java 项目。

## 看哪里

- CLI 打印 `Run:`；原始记录在 `runs/change-plan/records/RUN_ID.json`。
- `model_inputs` 保留本轮需求、工具声明和实际往返；规划通过 `submit_change_plan` 的类型化参数提交，未使用最终响应的 `response_format`。
- `answer.steps` 是待执行建议，`answer.checks` 是将来怎样核对的计划，不是已运行测试。
- `answer_checks` 检查字段、来源、已读目标和输入编号覆盖。它不判断自然语言是否真正遵守约束；例如“关联保留404的编号、实际建议改成200”，仍需人审。
- SQLite 保存的 Run 结果含 `artifact.id`、`revision`、需求和证据。版本摘要绑定补充问题、原需求、源码快照、实际引用证据及计划内容。每次提交新建 Run，不覆盖原计划。

结果为 `plan_for_review` 时可交给人审查；`plan_needs_information` 时先补条件；`unavailable` 时查停止原因与检查错误，CLI 退出1。前两种的 Run 可以结束，但审批始终为 `not_requested`。

## 页面与重启

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/change-plan --port 18062 --business-port 18822
```

打开 http://127.0.0.1:18062，在已有会话运行列表中查看结果，或粘贴 Run 编号重新连接。提交新任务时选择“提出修改计划”，分别输入目标、约束、核对条件和未确认问题。刷新或停止后重启，上次保存的结果仍可查看；查看结果不调用模型。相同目录只允许单进程，容量限制沿用第14讲。

## 缺条件与失败

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/change-plan-missing \
  --port 18060 --business-port 18820 \
  --plan-request fixtures/planning/missing-message-contract.json \
  --question '输入已明确给出会影响方案的阻塞问题；现在只需通过 submit_change_plan 提交 needs_information，questions 写明请接口使用方确认 message 的解析与固定文案要求，其余事实、步骤和检查留空。不要读取源码、不要查询业务，不要用普通文本回答。'
```

该命令用于验证缺条件的交付路径，已知问题本身也可由普通程序直接展示，不意味着所有缺条件请求都值得调用模型。程序始终拒绝把带 `open_questions` 的请求作为可审计划；受控测试覆盖模型错误地提交 ready 的情况。

本轮真实 auto 曾在原型的“工具+最终结构化响应”路径返回 HTTP400，另一轮在只列目录/搜索后返回 `summary=placeholder`、`questions=[]`，被判不可用。调整为提交工具后，宽泛取材的两组运行仍在第4次模型请求超时。明确取材范围的运行取得了待审计划；不能据此宣布 auto 的复杂规划能力普遍通过。

作者当时的系统 DNS/代理链路无法访问模型域名，实际模型验证用了进程级临时 CONNECT 转发，保留原域名、TLS 校验与原接口。读者需要正常的模型网络；本项目没有依赖那份作者临时脚本。无模型响应时不补造成功输出。

## 程序验证

```bash
uv run --frozen python -m pytest -q tests/repo_agent/test_planning.py
uv run --frozen python examples/repo_reading/05_check.py runs/change-plan/records/RUN_ID.json
```

将 `RUN_ID` 换成实际编号。测试使用受控模型检查捏造引用、未读目标、漏约束/验收、同批未见证据、重复提交、需求版本变化及重启查看；不代替真实模型运行或人工语义审查。身份授权、审批、执行和真实业务测试分别在后续阶段实现。
