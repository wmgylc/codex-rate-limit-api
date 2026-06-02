# Codex Rate Limit API

一个面向 Docker 部署的 Codex 用量接口服务。

服务在容器内通过 SSH 连接宿主机，启动宿主机真实 Codex CLI TUI，执行 `/status` 读取 5h 和 weekly 限额。容器启动时采集一次，之后默认每 5 分钟刷新内存缓存；外部 HTTP 请求只返回最近一次成功缓存。

## 接口

- `GET /health`
- `GET /api/codex/rate-limits`
- `GET /api/codex/usage`

`/api/codex/rate-limits` 和 `/api/codex/usage` 返回相同结构：

```json
{
  "agent": "codex",
  "available": true,
  "5h": {
    "used_percent": 15.0,
    "reset_at": 1780418820,
    "reset_at_iso": "2026-06-02T16:47:00Z"
  },
  "wk": {
    "used_percent": 45.0,
    "reset_at": 1780846020,
    "reset_at_iso": "2026-06-07T15:27:00Z"
  },
  "five_hour": {
    "used_percent": 15.0,
    "reset_at": 1780418820,
    "reset_at_iso": "2026-06-02T16:47:00Z"
  },
  "weekly": {
    "used_percent": 45.0,
    "reset_at": 1780846020,
    "reset_at_iso": "2026-06-07T15:27:00Z"
  },
  "model": "gpt-5.5",
  "updated_at": "2026-06-02T14:31:02.865856+00:00",
  "source_dir": "/root/.codex"
}
```

说明：

- `used_percent = 100 - Codex TUI 显示的 left`
- `reset_at` 是 UTC epoch seconds
- `reset_at_iso` 是 UTC ISO 时间
- `updated_at` 是最近一次成功采集时间
- 刷新失败时不会清空旧缓存

## Docker 部署

宿主机需要允许容器通过 SSH 登录，并且宿主机已安装 Codex CLI。

```bash
export CODEX_HOST_SSH_PASSWORD='你的宿主机 SSH 密码'
docker compose up -d --build
curl http://127.0.0.1:8080/api/codex/rate-limits
```

默认配置：

```yaml
ports:
  - "8080:8080"
environment:
  CODEX_HOST_SSH_HOST: ${CODEX_HOST_SSH_HOST:-host.docker.internal}
  CODEX_HOST_SSH_USER: ${CODEX_HOST_SSH_USER:-mac}
  CODEX_HOST_SSH_PASSWORD: ${CODEX_HOST_SSH_PASSWORD:?set CODEX_HOST_SSH_PASSWORD}
  CODEX_HOST_CODEX_PATH: ${CODEX_HOST_CODEX_PATH:-/opt/homebrew/bin/codex}
  CODEX_HOST_TIMEZONE: ${CODEX_HOST_TIMEZONE:-Asia/Shanghai}
  CODEX_RATE_LIMIT_REFRESH_SECONDS: ${CODEX_RATE_LIMIT_REFRESH_SECONDS:-300}
```

可选环境变量：

- `CODEX_HOST_SSH_HOST`：宿主机 SSH 地址，默认 `host.docker.internal`
- `CODEX_HOST_SSH_USER`：宿主机 SSH 用户，默认 `mac`
- `CODEX_HOST_SSH_PASSWORD`：宿主机 SSH 密码，必填
- `CODEX_HOST_CODEX_PATH`：宿主机 Codex CLI 路径，默认 `/opt/homebrew/bin/codex`
- `CODEX_HOST_CODEX_CWD`：运行 Codex CLI 的工作目录，默认 `/tmp`
- `CODEX_HOST_TIMEZONE`：解析 TUI reset 时间使用的时区，默认 `Asia/Shanghai`
- `CODEX_RATE_LIMIT_REFRESH_SECONDS`：后台刷新间隔，默认 `300`
- `CODEX_STATUS_TIMEOUT`：单次 TUI 采集超时时间，默认 `40`

## 本地测试

```bash
python3 -m unittest discover
```

## License

MIT
