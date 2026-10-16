# TaskHub Webhook 接入约定

文档 ID：webhooks-v1；版本：1.0；合成教学产品，不代表真实服务。

TaskHub 将事件以 HTTP POST 发送到用户配置的 HTTPS 地址。接收端应在 5 秒内返回 2xx；接收事件后应先持久化，再异步处理业务。事件包含 event_id，接收端用它识别重复事件。

请求头 X-TaskHub-Signature 是使用项目 Webhook secret 对原始请求体计算的 HMAC-SHA256 十六进制摘要。接收端应使用原始字节验证签名，不能先解析 JSON 再重新序列化。secret 由项目管理员配置，不应写入日志。

该文档只定义接口约定，不包含实时投递记录，也未定义 secret 的自动轮换周期。
