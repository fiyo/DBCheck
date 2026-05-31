# Oracle Instant Client for macOS

请将 Oracle Instant Client Basic 解压到此目录。

## 下载

1. 前往 Oracle 官网下载页：
   https://www.oracle.com/database/technologies/instant-client/macos-arm64-downloads.html
   或 x64 版本：
   https://www.oracle.com/database/technologies/instant-client/macos-intel-x64-downloads.html
2. 下载 **Instant Client Basic Package** (DMG)
3. 挂载 DMG，复制所有 `.dylib*` 文件到此文件夹

## 预期目录结构

```
darwin_x64/
├── libclntsh.dylib@
├── libclntsh.dylib.19.1
├── libnnz19.dylib
├── libociei.dylib
├── libons.dylib
└── README.md  (本文件)
```

**注意**：DBCheck 会自动检测此目录，无需手动配置 DYLD_LIBRARY_PATH。
