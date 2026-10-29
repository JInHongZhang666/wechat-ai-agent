# 视频转写独立服务

本目录用于将语音转写引擎与主应用隔离。它直接运行上游 Speaches 镜像，不复制、
不修改第三方源代码；后续可单独升级或移除，不影响资讯 Agent 和内容创作模块。

## 启动

CPU：

```powershell
docker compose -f .\services\video-transcriber\compose.yaml up -d
```

NVIDIA GPU：

```powershell
docker compose -f .\services\video-transcriber\compose.yaml -f .\services\video-transcriber\compose.cuda.yaml up -d
```

服务只监听 `127.0.0.1:8011`。通过项目根目录的 `start-video.ps1` 首次启动时，
会把配置的 faster-whisper 模型下载到 Docker 命名卷中，后续重启会复用缓存。

## 停止或移除

```powershell
docker compose -f .\services\video-transcriber\compose.yaml down
```

只有在确定也要删除已缓存的语音模型时，才追加 `--volumes`。
