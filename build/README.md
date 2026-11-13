# DBCheck Build Guide / DBCheck 打包指南

## System Requirements / 系统要求

| Component | Minimum | Notes |
|-----------|---------|-------|
| Python    | 3.10+   | 3.12 recommended |
| pip       | latest  | |
| PyInstaller | latest | Build tool |

---

## Windows Build / Windows 打包

### Prerequisites / 前置条件
1. Install Python 3.10+ (add to PATH)
2. Open CMD or PowerShell

### Steps / 步骤
```cmd
cd D:\DBCheck
build\build_windows.bat
```

The script will:
1. Check Python version (>= 3.10)
2. Install dependencies from `requirements.txt`
3. Run PyInstaller with `build/dbcheck_windows.spec`
4. Create release package `dist/DBCheck-Windows-x86_64.zip`

### Output / 输出
```
dist/DBCheck-Windows-x86_64.zip
```
Extract and run `start.bat` or `dbcheck.exe`.

---

## Linux Build / Linux 打包 (glibc >= 2.28, x86_64)

### Prerequisites / 前置条件

**IMPORTANT: binaries are bound to the glibc of the build host.**
The official CI builds inside `quay.io/pypa/manylinux_2_28_x86_64`, so the
published package runs on RHEL 8 / AlmaLinux 8 / 银河麒麟 V10 SP3 / 统信 UOS /
Ubuntu 20.04+ — any x86_64 distro with glibc >= 2.28. Building locally on a
newer glibc (e.g. Ubuntu 22.04, glibc 2.35) produces a binary that fails on
glibc 2.28 with `GLIBC_2.34 not found`. Use the container for portable builds.

**重要：产物与构建机的 glibc 绑定。**
官方 CI 在 `manylinux_2_28_x86_64` 容器内构建，产物可运行于 glibc >= 2.28 的
x86_64 系统（RHEL 8 / 银河麒麟 V10 SP3 / 统信 UOS 等）。若在更高版本 glibc 的机器上
本地打包，产物在麒麟 V10 SP3 上会报 `GLIBC_2.34 not found`。
arm64 服务器与 glibc 2.17 的旧系统请使用 Docker 多架构镜像。

#### Install Python 3.10+ / 安装 Python 3.10+
```bash
# RHEL 8 / 麒麟 V10 SP3
sudo dnf install -y python3.11 python3.11-devel gcc gcc-c++ make

# Debian / Ubuntu
sudo apt install -y python3.11 python3.11-venv python3.11-dev gcc g++ make
```

### Steps / 步骤
```bash
cd /path/to/DBCheck
chmod +x build/build_linux.sh
bash build/build_linux.sh
```

The script will:
1. Check Python version (>= 3.10)
2. Create a virtual environment
3. Install dependencies from `deploy/requirements.txt`
4. Run PyInstaller with `build/dbcheck_linux.spec`
5. Create release package `dist/RaccoonX-Linux-x86_64-<version>.tar.gz`

### Output / 输出
```
dist/RaccoonX-Linux-x86_64-v26.11.13.0.tar.gz
```

### Deploy / 部署
```bash
ldd --version | head -1          # 确认 glibc >= 2.28
tar xzvf RaccoonX-Linux-x86_64-v26.11.13.0.tar.gz
cd RaccoonX-Linux
bash start.sh
# or: ./dbcheck
```

Access: `http://<server-ip>:5003`

---

## Spec Files / 配置文件

| File | Platform | Notes |
|------|----------|-------|
| `dbcheck_windows.spec` | Windows | `win_no_prefer_redirects=True`, `psutil._pswindows` |
| `dbcheck_linux.spec` | Linux | `psutil._linux`, UPX disabled |

---

## Troubleshooting / 常见问题

### "Python version too low"
Install Python 3.10+. DBCheck uses PEP 604 union syntax (`dict | None`) which requires 3.10+.

### "Module not found" errors
Run `pip install -r requirements.txt` first, then re-run the build script.

### "Permission denied" on Linux
Run `chmod +x build/build_linux.sh` before executing.

### Build directory cleaned / 构建目录被清理
The build scripts do NOT clean the `build/` directory (where spec files live).
They only clean `dist/`, `__pycache__`, and temporary build artifacts.
