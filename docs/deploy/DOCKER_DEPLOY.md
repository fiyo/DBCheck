# DBCheck Docker 部署指南 (v2.7.0 with RBAC)

## 一、快速部署

### 1.1 使用 docker-compose（推荐）

```bash
# 克隆项目
git clone https://github.com/acdante-zhang/DBCheck.git
cd DBCheck
git checkout feature/user-management

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 1.2 使用 docker build + run

```bash
# 构建镜像
docker build -t acdante/dbcheck:latest .

# 运行容器
docker run -d \
  --name dbcheck \
  -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_pro_data:/app/pro_data \
  -v dbcheck_reports:/app/reports \
  acdante/dbcheck:latest

# 查看日志
docker logs -f dbcheck
```

### 1.3 访问服务

| 地址 | 说明 |
|------|------|
| http://localhost:5003 | DBCheck 主页面 |
| http://localhost:5003/um/login | RBAC 登录页面 |
| http://localhost:5003/um/admin | 系统管理后台 |

### 1.4 默认账户

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `admin` | `admin123` | 系统管理员 |
| `viewer` | `viewer123` | 只读用户 |
| `operator` | `operator123` | 运维人员 |
| `dbcheck` | `dbcheck` | 原系统管理员 |

## 二、数据持久化

容器使用三个 Docker Volume 持久化数据：

| Volume | 容器路径 | 内容 |
|--------|----------|------|
| `dbcheck_data` | `/app/data` | 巡检数据、SQLite 数据库 |
| `dbcheck_pro_data` | `/app/pro_data` | RBAC 用户数据库 |
| `dbcheck_reports` | `/app/reports` | 生成的巡检报告 |

如需备份，直接备份对应 Volume 即可：

```bash
# 备份
docker run --rm -v dbcheck_pro_data:/data -v $(pwd):/backup alpine tar czf /backup/pro_data_backup.tar.gz -C /data .

# 恢复
docker run --rm -v dbcheck_pro_data:/data -v $(pwd):/backup alpine tar xzf /backup/pro_data_backup.tar.gz -C /data
```

## 三、环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TZ` | `Asia/Shanghai` | 时区 |
| `FLASK_ENV` | `production` | Flask 运行模式 |
| `PYTHONUNBUFFERED` | `1` | Python 输出不缓冲 |

## 四、自定义端口

如需更改端口，修改 `docker-compose.yml`：

```yaml
ports:
  - "8080:5003"  # 将 5003 改为 8080
```

或 `docker run` 时：

```bash
docker run -d -p 8080:5003 ... acdante/dbcheck:latest
```

## 五、数据库驱动

Docker 镜像预装了以下数据库驱动：

| 数据库 | 驱动 | 状态 |
|--------|------|------|
| MySQL | pymysql | ✅ 内置 |
| TiDB | pymysql | ✅ 内置 |
| PostgreSQL | psycopg2-binary | ✅ 内置 |
| IvorySQL | psycopg2-binary | ✅ 内置 |
| Oracle | oracledb | ✅ 内置 |
| SQL Server | pyodbc | ✅ 内置 |
| DM8 (达梦) | dmpython | ✅ 内置 |
| Kingbase | psycopg2-binary | ✅ 内置 |
| GBase 8s | gbase8sdb + jaydebeapi | ✅ 内置 |

## 六、健康检查

容器内置健康检查端点：

```bash
curl http://localhost:5003/api/v1/health
# 返回: {"status": "ok"}
```

## 七、常见问题

**Q: 容器启动后无法访问？**
A: 等待约 30 秒初始化完成，查看日志 `docker logs dbcheck`

**Q: 如何重置 RBAC 数据？**
A: 进入容器执行：
```bash
docker exec -it dbcheck sh
rm -f /app/pro_data/um_rbac.db /app/pro_data/.rbac_seeded
python -m user_management.seed
exit
```

**Q: 忘记管理员密码？**
A: 删除 pro_data Volume 重新初始化（会丢失所有用户数据）：
```bash
docker-compose down -v
docker-compose up -d
```
