# Oracle Instant Client for Windows x64

请将 Oracle Instant Client Basic 解压到此目录。

## 下载

1. 前往 Oracle 官网下载页：
   https://www.oracle.com/database/technologies/instant-client/winx64-64-downloads.html
2. 下载 **Instant Client Basic Package** (ZIP)
3. 解压所有 `.dll` 文件和 `sdk/` 目录到此文件夹

## 预期目录结构

```
windows_x64/
├── oci.dll
├── oraocci19.dll
├── orannzsbb19.dll
├── oraociei19.dll
├── oraons.dll
├── sdk/
│   ├── include/
│   └── lib/
└── README.md  (本文件)
```

**注意**：DBCheck 会自动检测此目录，无需手动配置环境变量。
