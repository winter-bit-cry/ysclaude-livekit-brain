# YSClaude LiveKit Brain

可直接部署到 Zeabur 的 YSClaude 实时语音后端：

```text
YSClaude App --WebRTC--> LiveKit
                            |
                            +--> Aliyun Qwen3 realtime STT
                            +--> 当前 OpenAI-compatible LLM
                            +--> Cartesia streaming TTS
                            |
YSClaude App <--WebRTC------+
```

当前只处理语音通话，不发布摄像头或屏幕轨道。

## Zeabur 运行结构

Zeabur 会自动识别根目录中的 `Dockerfile`。同一个容器内运行：

- FastAPI：监听 Zeabur 注入的 `PORT`，提供 `/health` 和 `/api/livekit/session`。
- LiveKit Agent worker：以生产模式连接 LiveKit，接收房间作业。

`main.py` 会同时守护两个进程。任一进程异常退出时容器会退出，由 Zeabur 自动重启，避免 API 存活但 Agent 已停止。

## 部署到 Zeabur

### 1. 上传代码

选择一种方式：

1. 将 `ysclaude-livekit-brain` 初始化为独立 Git 仓库并推送到 GitHub，然后在 Zeabur 选择 `Add Service -> GitHub`。
2. 使用 Zeabur Local Project 上传本目录。
3. 如果把它放进 monorepo，在服务设置中把 Root Directory 指向 `ysclaude-livekit-brain`。

Zeabur 检测到 `Dockerfile` 后会使用 Docker 构建。构建阶段会安装依赖并执行 `python -m livekit.agents download-files`，提前下载 LiveKit/Silero 所需模型文件。

### 2. 配置 Variables

在 Zeabur 服务的 Variables 页面添加：

```dotenv
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_AGENT_NAME=ysclaude-voice

BRAIN_SHARED_SECRET=生成一个足够长的随机访问令牌
BRAIN_CONFIG_KEY=Fernet密钥
LOG_LEVEL=INFO
```

不要手动设置 `PORT`；Zeabur 会自动注入。

在本地生成 `BRAIN_CONFIG_KEY`：

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`BRAIN_CONFIG_KEY` 必须保持稳定。修改它会让部署切换期间尚未被 Agent 读取的旧会话配置无法解密。

### 3. 配置域名与健康检查

为服务添加一个 `zeabur.app` 或自定义 HTTPS 域名，例如：

```text
https://ysclaude-brain.example.com
```

在 Zeabur `Settings -> Health Check` 中将 HTTP Path 设置为：

```text
/health
```

部署完成后访问：

```text
https://ysclaude-brain.example.com/health
```

应返回类似：

```json
{
  "ok": true,
  "service": "ysclaude-livekit-brain",
  "agent": "ysclaude-voice"
}
```

### 4. 配置 YSClaude App

在语音设置中选择：

```text
通话引擎：LiveKit Agents
通话 STT：Aliyun
通话 TTS：Cartesia
Brain Server URL：https://ysclaude-brain.example.com
Brain Access Token：与 BRAIN_SHARED_SECRET 相同
```

同时填写：

- 当前聊天 LLM 的 OpenAI-compatible Base URL、API Key、模型名。
- 阿里 DashScope API Key、`qwen3-asr-flash-realtime` 和语言 `zh`。
- Cartesia API Key、模型和 Voice ID。

App 每次开始通话时通过 HTTPS 把当前模型配置交给 Brain。Brain 使用服务端 LiveKit API 创建加密的 Agent dispatch；模型密钥不会进入 App 获得的 LiveKit JWT。dispatch 配置的解密有效期为 15 分钟。

## 区域和资源建议

- Zeabur、LiveKit 与模型服务之间的距离会直接影响语音延迟。阿里北京区 Key 优先选靠近中国大陆的 Zeabur/LiveKit 区域；新加坡区 Key 优先选新加坡区域。
- 建议至少分配 1 GB 内存。LiveKit Agents、ONNX Runtime 和 Silero 启动时会占用明显高于普通 FastAPI 服务的内存。
- Agent 通过出站 WebSocket 连接 LiveKit；Zeabur 只需公开 FastAPI 的一个 HTTP 端口。
- 多副本可以共享同一组 LiveKit credentials 和 `BRAIN_CONFIG_KEY`，LiveKit 会负责 Agent 作业分发。

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env.local
```

分别运行：

```powershell
.\run-token-server.ps1
```

```powershell
.\run-agent.ps1
```

## 安全要求

- 不要提交 `.env` 或 `.env.local`。
- 不要在 Zeabur 构建日志、访问日志中打印 session API 请求体、Authorization 或 LiveKit JWT。
- `BRAIN_SHARED_SECRET` 只适合个人部署；正式多用户服务应替换成用户登录 JWT，并按用户限流。
- 生产环境必须使用 HTTPS Brain URL。
- LiveKit API Secret 与 `BRAIN_CONFIG_KEY` 只能存在于 Zeabur Variables。
