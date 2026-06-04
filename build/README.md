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

## Linux Build / Linux 打包 (CentOS 7.9)

### Prerequisites / 前置条件

**IMPORTANT: You must build on the target OS.**
Windows cannot cross-compile Linux binaries.

**重要：必须在目标系统上打包。**
Windows 无法交叉编译 Linux 二进制文件。

#### Install Python 3.10+ on CentOS 7.9 / 安装 Python 3.10+
```bash
# Add IUS repository
sudo yum install -y https://repo.ius.io/ius-release-el7.rpm
sudo yum install -y epel-release

# Install Python 3.10
sudo yum install -y python310 python310-pip python310-devel

# Install build tools
sudo yum install -y gcc gcc-c++ make
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
3. Install dependencies from `requirements.txt`
4. Run PyInstaller with `build/dbcheck_linux.spec`
5. Create release package `dist/DBCheck-Linux-x86_64.tar.gz`

### Output / 输出
```
dist/DBCheck-Linux-x86_64.tar.gz
```

### Deploy / 部署
```bash
tar xzvf DBCheck-Linux-x86_64.tar.gz
cd DBCheck-Linux
bash start.sh
# or: ./dbcheck
```

Access: `http://<server-ip>:5179`

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
