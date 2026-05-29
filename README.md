# mimi3 (mimo2api)

小米 AI Studio 自动化控制网关，将 MIMO 模型进行转发并兼容。

## 功能

- OpenAI 兼容 API 中转（支持 `/v1/chat/completions`, `/v1/responses`, `/anthropic/v1/messages`）
- Web 控制面板（实时监控、日志查看）
- 多账号轮询负载均衡
- 流式响应支持
- 参数自动钳制（temperature / top_p 超出上游范围时自动修正，避免 400 错误）
- WebUI 三段式状态可视化：云端状态 / create 认证探测 / 本地在线节点状态
- WebUI 节点归属显示：尽量将在线 websocket 节点精确绑定到具体 userId / account name

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 复制并配置环境变量
cp env.example .env

# 启动服务
python main.py
```

## Docker 启动

```bash
cp env.example .env
docker compose up -d --build
```

默认服务端口为 `8000`，可在 `.env` 中通过 `SERVER_PORT` 调整。

Docker Compose 会挂载以下本地目录：

- `./users` -> `/app/users`
- `./logs` -> `/app/logs`
- `./data` -> `/app/data`

容器内默认将指标数据库、指标快照、进程锁和模型映射文件放在 `/app/data`，对应宿主机 `./data` 目录：

```bash
MIMO_METRICS_DB_PATH=/app/data/gateway_metrics.db
MIMO_METRICS_SNAPSHOT_PATH=/app/data/gateway_snapshot.json
MIMO_PROCESS_LOCK_PATH=/app/data/mimo2api.lock
```

## 前置条件
一台拥有公网 ip 的机器，或者本机进行内网穿透。此为必备配置选项
```bash
WS_TUNNEL_URL=ws://your-domain.com:8000/ws
```

## WebUI 状态说明

新版 WebUI 不再只显示单一的 `AVAILABLE / DESTROYED`，而是把状态拆成三层：

1. **云端状态（claw status）**
   - 来源：Xiaomi `status` 接口
   - 用于表示云端记录中的实例状态
   - 常见值：`AVAILABLE`、`DESTROYED`、`EXPIRED(401)`

2. **认证探测（create probe）**
   - 来源：Xiaomi `create` 探测接口
   - 用于判断当前账号是否真的还能被 API / manager 接管
   - 常见值：`AVAILABLE`、`AUTH_FAILED`、`RATE_LIMITED`

3. **本地在线（local online）**
   - 来源：本地 gateway websocket 节点状态
   - 用于表示当前是否真的有 bridge 节点接入并可被调度

### 为什么会出现“WebUI 显示启用，但 API 不可用”？

因为这三个状态源不是同一个系统：

- 云端状态只能说明 Xiaomi 侧记录中实例“看起来可用”
- 认证探测才能说明该账号是否还能被程序真正接管
- 本地在线才说明当前 gateway 是否真的有节点在服务请求

因此会出现以下典型情况：

- **云端 `AVAILABLE`，但认证探测 `AUTH_FAILED`**
  - 说明 UI 看起来启用，但该账号 token / API 授权其实失效
- **云端 `AVAILABLE`，认证探测正常，但本地在线为否**
  - 说明该账号云端有环境，但 bridge 并没有真正连回本地网关
- **云端 `AVAILABLE`，认证探测正常，本地在线为是**
  - 说明这个账号更接近“真正可用于 API 转发”的状态

## 节点归属说明

为了减少“多个 claw 都显示启用，但不知道哪个真的在线”的困惑，gateway 现在会记录 websocket 节点的身份元数据，并在 WebUI 中展示：

- `节点UID`
- `节点名`

bridge 建立连接后会先向 gateway 发送一个 `hello` 包，内容包含：

- `user_id`
- `account_name`
- `ph`

gateway 收到后会将该在线节点尽量绑定到具体账号。因此在 WebUI 中：

- `判定: exact_user_node`
  - 表示已经将某个在线节点精确匹配到当前账号
- `判定: gateway_has_online_node`
  - 表示网关里确实有在线节点，但还没有精确归属到该账号
- `判定: none`
  - 表示当前没有证据表明该账号拥有本地在线节点

> 注意：如果账号长期处于 `429`、`AUTH_FAILED` 或者 bridge 未成功回连，本地节点归属仍可能显示“未匹配”。这通常意味着问题在上游账号 / Claw 生命周期，而不是 WebUI 显示错误。

## 免责声明

1. **本项目仅供学习交流使用，禁止一切商业/滥用行为。**
2. 本项目为个人独立开发的开源项目，与小米公司及其关联方**无任何隶属、授权或合作关系**。
3. MIMO、Xiaomi AI Studio 等名称及商标归小米公司所有，本项目不主张任何权利。
4. 本项目不提供任何小米账号、密钥或付费服务的破解，仅作为技术研究用途。
5. 使用者应遵守所在地法律法规及小米服务条款，因使用本项目产生的一切后果由使用者自行承担。
6. 本项目代码随缘更新，作者不提供任何保证或技术支持。
7. **建议优先使用小米官方 API**，本项目仅为技术研究备选方案。
8. 如有任何权益问题，请联系删除。

## 致谢
[linux.do](https://linux.do)
