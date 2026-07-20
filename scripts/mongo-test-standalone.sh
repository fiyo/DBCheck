#!/usr/bin/env bash
# =============================================================================
# MongoDB 测试实例启动脚本（用于端到端验证 DBCheck MongoDB 巡检）
#
# 用法：
#   PASSWORD='YourStr0ngPwd' ./scripts/mongo-test-standalone.sh
#   或直接 ./scripts/mongo-test-standalone.sh   （使用脚本内置默认密码）
#
# 说明：
#   - 拉取并运行官方 mongo:7 镜像（支持 6/7+，契合本次巡检重构目标）
#   - 通过 MONGO_INITDB_ROOT_USERNAME / MONGO_INITDB_ROOT_PASSWORD
#     两个环境变量在 admin 库创建一个 root 账号（这就是“密码怎么设置”）
#   - Mongo 7 默认认证机制为 SCRAM-SHA-256，DBCheck 对应填 auth_mechanism=SCRAM-SHA-256
# =============================================================================
set -euo pipefail

CONTAINER="${MONGO_CONTAINER:-mongo-test}"
IMAGE="${MONGO_IMAGE:-mongo:7}"
PORT="${MONGO_PORT:-27017}"
USER="${MONGO_USER:-admin}"
PASSWORD="${PASSWORD:-DBCheck@2026}"

echo "==> 启动 MongoDB 测试实例 ($IMAGE)，容器名 = $CONTAINER"
# 若已存在同名容器则先清理，保证可重复运行
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

docker run -d \
  --name "$CONTAINER" \
  -p "$PORT:27017" \
  -e "MONGO_INITDB_ROOT_USERNAME=$USER" \
  -e "MONGO_INITDB_ROOT_PASSWORD=$PASSWORD" \
  "$IMAGE"

echo "==> 等待实例就绪 (Waiting for connections) ..."
for _ in $(seq 1 30); do
  if docker logs "$CONTAINER" 2>&1 | grep -q "Waiting for connections"; then
    echo "==> MongoDB 已就绪 ✅"
    break
  fi
  sleep 1
done

echo
echo "=================================================================="
echo " MongoDB 测试实例已启动"
echo "   镜像      : $IMAGE"
echo "   地址      : localhost:$PORT"
echo "   用户名    : $USER"
echo "   密码      : $PASSWORD"
echo "   认证库    : admin"
echo "   认证机制  : SCRAM-SHA-256 (Mongo 7 默认)"
echo "------------------------------------------------------------------"
echo " 在 DBCheck Web UI 添加 MongoDB 数据源时填写："
echo "   host           = localhost"
echo "   port           = $PORT"
echo "   user           = $USER"
echo "   password       = $PASSWORD"
echo "   auth_source    = admin"
echo "   auth_mechanism = SCRAM-SHA-256"
echo "=================================================================="
echo
echo " 查看日志 : docker logs -f $CONTAINER"
echo " 停止实例 : docker rm -f $CONTAINER"
