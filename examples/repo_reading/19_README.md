# 第 19 讲　让已保存的计划等待人工审批

沿用 `12_app.py` 和 `repo_agent`。SDK 生成计划，应用将送审、决定和交接保存到同一个 SQLite 数据目录。

## 启动和验证

从 `lesson-19-approval-20261118` 取得代码。在仓库根目录配置已有的 `MODEL_API_KEY`、`MODEL_BASE_URL`、`MODEL_NAME`，需要 Python 3.12、uv、ripgrep 和可用模型连接。

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/plan-approval \
  --access-config fixtures/access/projects.json \
  --port 18065 --business-port 18825
```

打开 http://127.0.0.1:18065。运行者从 `runs/plan-approval/local-credentials.json` 获取两个本地测试身份的凭据。凭据文件包含服务身份，保持私有。用独立浏览器窗口分别登录 Alice 与 Bob。

1. 用 Alice 在 alpha 选择「提出修改计划」。将下列问题填入目标，约束与接受条件按 `fixtures/planning/add-error-message.json` 填写，每行一条。任务成功后会显示「修改计划 · 待人工审查」。
2. 在计划下方填写审批人账号 `bob`，点「送审」。到「计划审批」查看原计划版本和「待审批」。记下申请编号。
3. 停止进程，保留数据目录和端口，执行相同命令。Bob 登录后在「计划审批」打开原申请并点「批准本版本」。新出现一个交接编号。Alice 原来的 Run 仍是完成的计划生成记录。
4. 重新生成一份计划，送审后改选「拒绝」。申请记录成为「已拒绝」，交接编号为空。撤回待审申请后可以提交新计划，原申请不能批准。

```text
只为 code/src/ApiExceptionHandler.java 增加固定 message=订单不存在 提出最小计划。先读取这个文件和 code/src/OrderController.java，随后用 submit_change_plan 提交一项修改步骤与输入两项接受条件的独立检查。事实引用实际观察编号。不要读取 policy/ 或查询业务数据，不修改、不执行。
```

模型输入、工具观察和生成结果留存在 `records/<run-id>.json`。复查审批和交接不需要模型调用。这里的批准只创建 `prepare_isolated_change` 的交接记录，后续阶段要检查执行边界并准备隔离工作区。

## 从 API 观察拒绝位置

所有接口使用第 18 讲的可信身份。受保护 API 可使用为当前身份预发的 Bearer 凭据。

| 请求 | 请求体 | 观察点 |
| --- | --- | --- |
| `POST /runs/{run_id}/approval` | `reviewer_id` + `plan_revision` | 创建者送审返回 201。审批人须是项目成员且有审批权限。 |
| `GET /approvals` | 无 | 只列出本人送审或获准处理的指定申请。 |
| `GET /approvals/{approval_id}` | 无 | 取得计划和决定。审批人通过这一入口审查范围有限的材料。 |
| `POST /approvals/{approval_id}/decision` | `plan_revision` + `decision`（approve/reject） | 仅指定且仍有权限的审核人能决定。旧版本、过期或已决申请返回 409。 |
| `POST /approvals/{approval_id}/withdraw` | `plan_revision` | 创建者撤回仍在等待的申请。 |

可以在重启时去掉配置中的 `approvers.alpha` 来观察成员身份和审批权限的区别。修改配置使用本地副本时，请把项目来源路径改为绝对路径或按新配置目录重新定位。重启后持有同一份 Bob 的凭据仍可访问 alpha。审批列表和决定接口会按新授权作判断。

本版每个计划 Run 只接受一次送审，等待期 24 小时。审批允许列表在启动时读取，持久数据目录由单个进程独占。批准时重算来源快照，并用 SQLite 条件更新和唯一约束保存一次决定与交接。重复请求得到 409 后可读取原结果。已有的可信第 18 讲数据目录会在打开时补建审批表。

```bash
uv run --frozen python -m pytest tests/repo_agent/test_approval.py -q
```

回归用可控计划验证权限、重复请求、版本变化、有效期和事务回滚。实际模型验证需按上方网页或 HTTP 入口生成计划，结果受当前连接和模型行为影响。
