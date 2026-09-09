---

## ✨ 主要更新
- 接入 BIC-QA 云端知识库（官方 `/open-api/v1`，SSE 流式返回），作为本地 12 专家的能力补充。
- 智能诊断中心 BIC-QA 页 Tab 重排、国际化、Markdown 表格渲染、诊断发现导入等 UI/UX 修复。

## 🐳 Docker 镜像

推荐使用 Docker Hub（国内可用）：

```powershell
docker pull jackge12345/dbcheck:v26.9.9.0
docker pull jackge12345/dbcheck:latest
```

或 GitHub Container Registry：

```powershell
docker pull ghcr.io/fiyo/dbcheck:v26.9.9.0
docker pull ghcr.io/fiyo/dbcheck:latest
```

运行示例：

```powershell
docker run -d -p 5003:5003 --name raccoonx jackge12345/dbcheck:v26.9.9.0
```

> 镜像同时支持 `linux/amd64` 与 `linux/arm64`（ARM64 信创主机）。

## ⚠️ 安装注意事项
- **macOS**：当前分发包**未做 Apple 公证（二进制未签名）**。首次打开若被 Gatekeeper 拦截，请右键点击 App / 可执行文件 →「打开」并在弹窗中确认即可运行；如需彻底消除提示，后续可接入 Apple 开发者证书做正式公证。
- **Windows**：解压后双击 `start.bat` 启动，浏览器访问 http://localhost:5003 。
- **Linux**：客户端安装包暂未发布，当前仅提供 Windows / macOS 安装包；Linux 用户请直接使用上方 Docker 镜像。

## 📦 二进制包
下方 Assets 提供 Windows / macOS 客户端压缩包；需要源码编译的请下载 `Source code`。
