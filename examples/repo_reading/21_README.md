# 第 21 讲　从已就绪工作区生成补丁

检出 `lesson-21-controlled-edit-20261122` 后，在仓库根目录运行。开发起点是 `lesson-20-workspace-20261122`。准备同一 Docker 镜像，沿用 Python 3.12、uv 与已配置的工具调用模型。

```bash
uv run --frozen python examples/repo_reading/12_app.py \
  --conversation --data-dir runs/workspace-edit \
  --access-config fixtures/access/projects.json --prepare-workspace --edit-workspace \
  --port 18067 --business-port 18827
```

沿 [第 20 讲](20_README.md) 的方法生成异常响应计划，批准对应版本，再由申请人准备工作区。在同一张审批单上点「开始受限编辑」。编辑有自己的 Run 编号，读取和替换由原 SDK Runner 派发。进度页可请求取消；成功产物是待检查的补丁。只读底稿、前一讲准备的可写副本与项目原件留作对照。

- 启动接口 `POST /approvals/{approval_id}/workspace/edit` 只接收 `plan_revision`。准备记录缺失返回 409，审批人代替申请人编辑返回 403。
- 通过 `GET /runs/{run_id}` 查结果和状态，用同一申请身份 `GET /runs/{run_id}/patch` 下载补丁。补丁里的 `code/` 是取材名称空间。运行结束前、取消后或没有字节差异时，下载返回 404。
- 准备和编辑使用同一受限 Docker 配置。编辑只在尝试副本中替换唯一匹配的旧内容。取消等待已发出的有限时 Docker 操作清理，然后丢弃尝试副本。若清理无法确认，保留暂存目录与服务日志供核对。
- 补丁写入本地运行记录，供下一讲独立检查业务行为。模型没有 Shell、Git 提交和发布工具。

使用可控模型和真实 Docker 验证编辑、反馈、拒绝与取消。命令需要本机已有第 20 讲指定镜像，测试不读取模型配置。

```bash
HARNESS_DOCKER_WORKSPACE_TEST=1 uv run --frozen python -m pytest tests/repo_agent/test_editing.py -q
```
