#!/usr/bin/env bash
# DBCheck 远端宿主采集 —— 纯 Shell(/proc) 版，零依赖。
#
# 适用：Linux 目标机（有 /proc）。目标机无需安装 Python / psutil。
# 输出：stdout 单行 JSON（字段与 remote_host_collector.py 的 psutil 子集一致，
#       供中心机 _compute_host_rates 做跨快照差分）。错误一律走 stderr。
# 不适用：Windows 目标机（无 /proc），需改用 Python 采集器。
#
# 设计要点：
#   - CPU 利用率需两次采样差分，故脚本内 sleep 1 做 1 秒窗口；
#   - 磁盘计数器取 /proc/diskstats 累计值（自开机），交给中心机按 30s 快照差分；
#   - 仅聚合真实块设备（sd/vd/hd/nvme/xvd），排除 loop/ram/dm-/md-/sr 等。
set +e

if [ ! -r /proc/stat ]; then
  printf '{"host_collector_source":"unavailable"}\n'
  exit 0
fi

read_cpu() {
  # /proc/stat 首行 cpu：user nice system idle iowait irq softirq steal ...
  # 注意：保持 POSIX sh 兼容（不用 local / 不用 bash 专属语法），目标机若无 bash
  # 仅 sh(dash) 也能跑。函数内 set -- 不影响调用方（命令替换子 shell 中执行）。
  set -- $(grep '^cpu ' /proc/stat 2>/dev/null | head -1)
  shift
  echo "${1:-0} ${2:-0} ${3:-0} ${4:-0} ${5:-0} ${6:-0} ${7:-0} ${8:-0}"
}

read_disk() {
  # /proc/diskstats 字段：
  #  1 major 2 minor 3 name
  #  4 rd_compl 5 rd_merged 6 rd_sectors 7 rd_ms
  #  8 wr_compl 9 wr_merged 10 wr_sectors 11 wr_ms
  awk '
    $3 ~ /^(sd|vd|hd|xvd)[a-z]+$|^nvme[0-9]+n[0-9]+$/ {
      rdc+=$4; rds+=$6; rdm+=$7; wrc+=$8; wrs+=$10; wrm+=$11
    }
    END { printf "%d %d %d %d %d %d", rdc, rds*512, rdm, wrc, wrs*512, wrm }
  ' /proc/diskstats
}

# ── CPU：两次采样（间隔 1s）差分 ──
cpu1=$(read_cpu)
sleep 1
cpu2=$(read_cpu)

set -- $cpu1
u1=${1:-0}; n1=${2:-0}; s1=${3:-0}; i1=${4:-0}; io1=${5:-0}; ir1=${6:-0}; so1=${7:-0}; st1=${8:-0}
set -- $cpu2
u2=${1:-0}; n2=${2:-0}; s2=${3:-0}; i2=${4:-0}; io2=${5:-0}; ir2=${6:-0}; so2=${7:-0}; st2=${8:-0}

idle1=$((i1 + io1)); idle2=$((i2 + io2))
tot1=$((u1+n1+s1+i1+io1+ir1+so1+st1)); tot2=$((u2+n2+s2+i2+io2+ir2+so2+st2))
dtot=$((tot2 - tot1)); didle=$((idle2 - idle1)); diow=$((io2 - io1))

cpu_pct=0; iowait_pct=0
if [ "$dtot" -gt 0 ] 2>/dev/null; then
  cpu_pct=$(awk "BEGIN{printf \"%.1f\", (1 - $didle*1.0/$dtot)*100}")
  iowait_pct=$(awk "BEGIN{printf \"%.1f\", $diow*1.0/$dtot*100}")
fi

# ── CPU 核数 / 型号 ──
cpu_count=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)
cpu_count=${cpu_count:-0}
cpu_model=$(grep '^model name' /proc/cpuinfo 2>/dev/null | head -1 | sed 's/^model name[[:space:]]*:[[:space:]]*//' | tr -d '"' | tr -d '\\')
[ -z "$cpu_model" ] && cpu_model="unknown"

# ── 内存 / Swap（/proc/meminfo，单位 KB）──
mem_total=$(awk '/^MemTotal:/{print $2}' /proc/meminfo); mem_total=${mem_total:-0}
mem_avail=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo)
mem_free=$(awk '/^MemFree:/{print $2}' /proc/meminfo); mem_free=${mem_free:-0}
buffers=$(awk '/^Buffers:/{print $2}' /proc/meminfo); buffers=${buffers:-0}
cached=$(awk '/^Cached:/{print $2}' /proc/meminfo); cached=${cached:-0}
swap_total=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo); swap_total=${swap_total:-0}
swap_free=$(awk '/^SwapFree:/{print $2}' /proc/meminfo); swap_free=${swap_free:-0}

mem_total_b=$((mem_total * 1024))
if [ -n "$mem_avail" ]; then
  mem_avail_b=$((mem_avail * 1024))
else
  mem_avail_b=$(((mem_free + buffers + cached) * 1024))
fi
mem_used_b=$((mem_total_b - mem_avail_b))
mem_pct=0
if [ "$mem_total_b" -gt 0 ] 2>/dev/null; then
  mem_pct=$(awk "BEGIN{printf \"%.1f\", $mem_used_b*100.0/$mem_total_b}")
fi

swap_total_b=$((swap_total * 1024))
swap_used_b=$(((swap_total - swap_free) * 1024))
swap_pct=0
if [ "$swap_total_b" -gt 0 ] 2>/dev/null; then
  swap_pct=$(awk "BEGIN{printf \"%.1f\", $swap_used_b*100.0/$swap_total_b}")
fi

# ── 负载 ──
set -- $(awk '{print $1, $2, $3}' /proc/loadavg 2>/dev/null)
load1=${1:-0}; load5=${2:-0}; load15=${3:-0}

# ── 磁盘累计计数器（原样输出，中心机跨快照差分）──
set -- $(read_disk)
rdc=${1:-0}; rdb=${2:-0}; rdm=${3:-0}; wrc=${4:-0}; wrb=${5:-0}; wrm=${6:-0}

# ── 输出单行 JSON ──
printf '{"host_collector_source":"shell","host_cpu":%s,"host_cpu_iowait":%s,"host_cpu_count":%s,"host_cpu_model":%s,"host_mem_total":%s,"host_mem_used":%s,"host_mem_available":%s,"host_mem":%s,"host_swap_total":%s,"host_swap_used":%s,"host_swap":%s,"host_load1":%s,"host_load5":%s,"host_load15":%s,"host_disk_read_bytes":%s,"host_disk_write_bytes":%s,"host_disk_read_ms":%s,"host_disk_write_ms":%s,"host_disk_read_count":%s,"host_disk_write_count":%s}\n' \
  "$cpu_pct" "$iowait_pct" "$cpu_count" "\"$cpu_model\"" \
  "$mem_total_b" "$mem_used_b" "$mem_avail_b" "$mem_pct" \
  "$swap_total_b" "$swap_used_b" "$swap_pct" \
  "$load1" "$load5" "$load15" \
  "$rdb" "$wrb" "$rdm" "$wrm" "$rdc" "$wrc"
