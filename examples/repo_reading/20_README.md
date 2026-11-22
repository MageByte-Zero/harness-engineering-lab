# 第 20 讲　从批准交接准备隔离工作区

检出 `lesson-20-workspace-20261122` 后，在仓库根目录执行。使用 Python 3.12、uv、ripgrep、本地 Docker，以及 [第 19 讲](19_README.md) 已配置的模型接口。Docker 镜像预先取得，应用的探针使用 `--pull=never`。

```bash
docker pull python@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/workspace-prep \
  --access-config fixtures/access/projects.json --prepare-workspace \
  --port 18066 --business-port 18826
```

打开 http://127.0.0.1:18066。凭据在 `runs/workspace-prep/local-credentials.json`。按第 19 讲的输入在 alpha 发起计划，批准目标为 `code/src/ApiExceptionHandler.java` 的版本。申请人刷新「计划审批」，点「准备工作区」，看到工作区编号和可写目标。

接口使用相同的本地可信身份。申请人向 `POST /approvals/{approval_id}/workspace` 提交 `{"plan_revision":"审批记录里的完整摘要"}`。程序按编号查已有交接，再查当前项目成员资格和申请归属。批准人能在指定审批单中作决定，准备与读取工作区交给申请人。

| 观察 | 结果 |
| --- | --- |
| 准备通过 | HTTP 200，`state=ready`、原 `handoff_id`、`targets` 与 `probe` |
| 来源已变 | HTTP 409，`workspace_source_changed`，准备到此停止 |
| 申请归属不符 | HTTP 403 |
| 试图在准备请求里再指定目标 | HTTP 422 |
| 探针超时 | HTTP 504，`workspace_timeout`；清理成功后才给出该结果 |
| 清理无法确认 | HTTP 503，`workspace_cleanup_unconfirmed`；查看服务日志里的容器名 |

用同一个申请人的身份请求 `GET /approvals/{approval_id}/workspace`，可在重启后查看准备记录。服务器只在容器探针成功、容器删除并确认探针恢复了目标内容后，发布 `runs/workspace-prep/workspaces/<approval-id>/manifest.json`。应用一次只准备一个工作区。容器只读基础目录并按批准列表逐文件挂载可写副本，工作区根、独立检查资料和应用凭据由主进程保管。

下面一条命令用可控已审计划验证 HTTP 入口、来源漂移、越权拒绝、真实 Docker 探针和真实超时清理。真实模型全链路走上面的页面或相同 HTTP API。

```bash
HARNESS_DOCKER_WORKSPACE_TEST=1 uv run --frozen python -m pytest tests/repo_agent/test_approval.py -k workspace -q
```

该测试在真容器里用固定等待代码模拟卡住，并查 Docker 和工作区目录。运行阶段如果主进程被强制杀死，可能留下容器或暂存目录，需要先核对后清理。浏览器断开只结束等待响应。此版目标是验证受限编辑所需环境，后续才接入模型发起的编辑和业务验证。
