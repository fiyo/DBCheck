# 在 Docker 中启用 DM8 达梦数据库支持

`dmpython` 是 DM8 的 Python 驱动，需要 DM8 客户端库才能正常运行。

## 方法一：运行时安装（推荐）

1. 启动容器：
```bash
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/reports \
  -v /path/to/dm8:/opt/dm8 \
  --name dbcheck \
  jackge12345/dbcheck:v2.5.3
```

2. 进入容器安装 `dmpython`：
```bash
docker exec -it dbcheck bash
pip install dmpython
```

3. 配置环境变量：
```bash
export LD_LIBRARY_PATH=/opt/dm8/bin:$LD_LIBRARY_PATH
```

## 方法二：自行构建镜像

1. 获取 DM8 客户端库（从 DM8 安装包）

2. 创建 `Dockerfile.dm8`：
```dockerfile
FROM jackge12345/dbcheck:v2.5.3

# 复制 DM8 客户端库
COPY dm8_client /opt/dm8

# 安装 dmpython
RUN pip install dmpython

# 配置环境变量
ENV LD_LIBRARY_PATH=/opt/dm8/bin:$LD_LIBRARY_PATH

CMD ["python", "web_ui.py"]
```

3. 构建：
```bash
docker build -f Dockerfile.dm8 -t jackge12345/dbcheck:dm8 .
```

## 方法三：使用官方 DM8 Docker 镜像

如果你的数据库在 Docker 里运行，可以直接使用 DM8 官方镜像。

---

**推荐**：使用方法一，在运行时挂载 DM8 客户端库并安装 `dmpython`。
