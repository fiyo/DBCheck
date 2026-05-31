# Oracle Instant Client for Linux x64

请将 Oracle Instant Client Basic 解压到此目录。

## 下载

1. 前往 Oracle 官网下载页：
   https://www.oracle.com/database/technologies/instant-client/linux-x86-64-downloads.html
2. 下载 **Instant Client Basic Package** (ZIP)
3. 解压所有 `.so*` 文件到此文件夹

## 预期目录结构

```
linux_x64/
├── libclntsh.so.19.1
├── libnnz19.so
├── liboci.so.10.1
├── libociei.so
├── libons.so
└── README.md  (本文件)
```

**注意**：DBCheck 会自动检测此目录，无需手动配置 LD_LIBRARY_PATH。
