# 项目权限接入同一个 Agent

固定版本：`lesson-18-authorization-20261117`。在 17-r1 的同一应用上增加身份、项目选材和私有产物访问检查；仍只读，不批准或执行修改。

## 最小启动

从该标签取得仓库，需要 Python 3.12、uv、ripgrep，根目录 `.env` 或进程环境已配置 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`。端口需空闲。在仓库根目录执行一条命令：

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/project-access \
  --access-config fixtures/access/projects.json \
  --port 18064 --business-port 18824
```

打开 http://127.0.0.1:18064。首次启动生成 `runs/project-access/local-credentials.json`（0600，本地私密文件，已被 runs 忽略规则排除）；`users.alice` 和 `users.bob` 是各自预发的访问凭据，`services` 是业务连接凭据。运行者将对应用户凭据交给使用者；教学时自己分别登录两个浏览器窗口。不要把整份文件交给用户，也不要上传它。程序不会打印凭据值。

这不是注册/SSO 服务。配置、凭据和数据目录都由可信本地运行者维护。浏览器通过 `/session` 验证凭据，使用 HttpOnly、SameSite=Strict Cookie（HTTPS 下加 Secure）；原生 EventSource 使用同源 Cookie，事件 URL 不带凭据。API 的 Bearer 验证同一预发凭据。退出清除当前浏览器 Cookie、已显示结果和输入，不吊销该凭据的其他副本；轮换凭据文件并重启才撤销旧凭据。没有企业 SSO、过期刷新、集中会话或网络部署能力。

## 有权与越权

可信配置在 `fixtures/access/projects.json`，资料路径相对这个 JSON 所在目录。不要把配置当 HTTP 请求体上传；它包含服务端才能决定的来源根和允许资源。

| 身份 | 可请求项目 | 资料和业务范围 |
| --- | --- | --- |
| alice | alpha | 原合成代码/规程；o-999、o-404 |
| bob | alpha、beta | 选 alpha 时同上；选 beta 时只有 other-project 和 o-1000 |

两项目都有 `code/src/ApiExceptionHandler.java`；beta 中包含独有标记 `BETA_PRIVATE_CATALOG`。同名不意味着同一资料。项目成员默认只能查看、续用和取消自己的会话/Run。

1. Alice 登录后只有 alpha。选“提出修改计划”，输入 `fixtures/planning/add-error-message.json` 的目标、四条约束和两条核对条件；网页每行一条，会生成 c1/a1 等本次标识，不要求与 JSON 字面 ID 相同。
2. 为复用本轮验证的取材路径，目标可填写下面的问题。提交后观察两个读取工具，再观察 `submit_change_plan`；结果仍需人工审查。
3. 保存 Run 编号。Bob 即使在 alpha 项目，也无法用这个编号查看结果、事件、取消，或沿 Alice 的会话发起下一轮。403 不附带对方内容；不知道的资源编号也不附带内容。
4. 关闭进程，保留数据目录，用相同命令启动。Alice 可以重新查看；Bob 仍不能。撤销配置中 Alice 的 alpha 成员资格，停止旧进程并重新启动后，Alice 的旧结果、历史续用和事件也被拒绝。本版策略在启动时绑定，不热更新。

本轮真实模型的限定问题：

```text
只为 code/src/ApiExceptionHandler.java 增加固定 message=订单不存在 提出最小计划。先读取这个文件和 code/src/OrderController.java，随后用 submit_change_plan 提交一项修改步骤与输入两项接受条件的独立检查。事实引用实际观察编号。不要读取 policy/ 或查询业务数据，不修改、不执行。
```

先前未携带归属的记录保留但不开放，不自动归给首次登录者。带归属的数据目录不能去掉 `--access-config` 后以旧模式打开。项目资料/允许资源变动导致会话范围不匹配时，旧结果仍按归属授权查看；续用要新建会话。成员资格撤销则连旧结果也不再开放。

## CLI 和协议边界

CLI 同样需要可信凭据：在当前进程环境设置 `AGENT_CREDENTIAL` 为该用户的预发凭据，追加 `--project alpha --plan-request fixtures/planning/add-error-message.json --question '…上述限定问题…'`。它不接受 `--user-id alice` 来冒充身份，调用的仍是 RunManager 与 ConversationWorker。已有会话用 `--conversation-id ID`，同样检查创建者与项目。不要把凭据值直接写在命令、脚本或日志里。

追加 `--business-transport mcp` 可走原 stdio 连接。应用传入项目服务凭据及允许订单集合；MCP 服务端检查本地范围，合成 HTTP 服务按自己收到的服务凭据再检查一次。把 MCP 客户端的允许集合扩大，也不能用 alpha 凭据读 o-1000。用户凭据、业务凭据和模型密钥不进入模型输入；MCP 子进程环境剔除 MODEL_/OPENAI_/AGENT_ 变量。

## 验证与限制

```bash
uv run --frozen python -m pytest tests/repo_agent/test_authorization.py -q
uv run --frozen python -m pytest -q
```

权限测试包含真实 HTTP 服务和独立 MCP 客户端、持久化重启、无归属旧记录、撤权后的拒绝；规划的测试模型是可控替身。真实模型另在网页完整调用：两次模型往返、两次代码读取、一次计划提交，得到 `plan_for_review`。一次输入通过不能证明所有复杂任务可靠；程序关联检查不等于语义批准。作者网络验证使用临时解析转发，仍是原配置接口和 TLS 校验；读者需要可正常调用的 MODEL_* 接口。

当前 17 个 Python 源文件、一个页面模板，无新增依赖。授权检查位于选材/历史注入之前，以及资源访问和工具边界；SDK 仍负责原有一次 Runner 循环。宿主机用户可以直接读取磁盘，stdio 子进程也不是沙箱；本讲不宣称实现操作系统隔离。容量仍为全局 32 会话、64 Run、2 个活动任务，未实现按用户配额。人工审批、隔离执行和恢复由后续增量完成。
