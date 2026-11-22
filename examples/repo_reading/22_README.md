# 第 22 讲　独立检查与一次修正

从固定标签 `lesson-22-independent-verification-20261122` 检出后在仓库根目录运行。本讲的开发起点为 `lesson-21-controlled-edit-20261122`。继续使用 Python 3.12、uv、Git、Docker 和原模型配置。检查器使用真实 Java 源码及 Spring MockMvc。

## 准备检查镜像

先取得第 20 讲指定的 Python 基础镜像。准备脚本下载固定版本的 Linux Temurin 17、Spring 和依赖，校验 SHA-256，再构建本地检查镜像。若 Maven 本地缓存已有相同依赖，会先读缓存。也可传入 `--jdk-archive` 复用校验过的下载文件。

```bash
docker pull python@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527
uv run --frozen python tests/order_contract/prepare.py
```

镜像构建和运行分开。验证运行不会拉取镜像或下载依赖。候选源码只读挂载，JDK 将编译结果写到容器自己的临时目录。验证使用应用预置的程序、网络关闭、普通用户和有时限的容器。

## 沿原应用运行

在 [第 21 讲](21_README.md) 的启动参数后追加 `--verify-order-patch`。沿用上讲的数据目录，或按上讲输入重新生成、批准、准备并编辑。该检查器覆盖 alpha 项目示例订单响应的固定约束和验收条件。

申请人在「补丁待检查」的运行里点「验证订单响应」或「验证并试修一次」。接口为 `POST /runs/{候选Run}/verification`，请求体只包含可选的 `repair` 布尔值。该接口绑定应用已保存的补丁，不接收请求上传的补丁或测试命令。验证另有一个 Run，原候选继续可以按原编号查看。

- 两个请求的状态码与 JSON 都满足条件后，结果为 `verification_for_review`，可下载这个 Run 的补丁。
- 业务不符时，检查记录保存每项预期和实际值。打开一次修正后，应用只把失败项交给原受限编辑工具，复用批准范围。第二次验证仍失败时停下。
- 缺少镜像、编译或检查进程无法完成时，保存检查未完成的原因并停止。这个教学版本只对已得到业务断言结果的失败启动修正。
- 取消会等待正在运行的容器完成清理，再处理本次副本。进程强杀后的恢复进入下一讲。

## 最小复现

两份本地镜像就绪后，以下命令使用可控模型和真实 Docker，核对通过、错误补丁拒绝、修正复验和缺少镜像时停下。命令无需模型凭据。

```bash
HARNESS_DOCKER_WORKSPACE_TEST=1 uv run --frozen python -m pytest tests/repo_agent/test_verification.py -q
```
