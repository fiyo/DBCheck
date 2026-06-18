# 插件开发说明

本文档为开发插件的说明文档。


## 发布你的插件

想把你的巡检规则、通知渠道或报告模板分享给社区？三步上架：

### 1. 开发插件

Fork [插件模板](https://github.com/fiyo/dbcheck-plugins)，在 `plugins/` 下建目录，写 `plugin.json` + `__init__.py`。

参考官方插件（`dbcheck-charset-audit`）源码：`__init__.py` 继承 `InspectionPlugin`，实现 `get_queries()` 和 `analyze()`。

### 2. 打包发布

在**你自己的 GitHub 仓库**创建 Release，执行以下命令将源码打包压缩为.zip文件，将以下代码中的 `my-plugin` 改为你的插件id，上传插件 zip：
```bash
powershell -Command "Compress-Archive -Path my-plugin -DestinationPath my-plugin-1.0.0.zip"
```
上传 zip 到 Release，记下下载链接。

### 3. 上架市场

Fork [fiyo/dbcheck-plugins](https://github.com/fiyo/dbcheck-plugins)，编辑 `registry.json`，在 `plugins` 数组末尾追加，其中的 `my-plugin` 改一个你的插件英文id：

```json
{
  "id": "my-plugin",
  "name": "我的插件",
  "version": "1.0.0",
  "author": "你的名字",
  "author_type": "community",
  "description": "插件描述",
  "download": "https://github.com/你的用户/你的仓库/releases/download/v1.0.0/my-plugin-1.0.0.zip",
  "category": "inspection",
  "keywords": ["keyword1"],
  "db_types": ["mysql"],
  "min_dbcheck_version": "2.6.0",
  "license": "MIT",
  "verified": false
}
```

提 PR → CI 自动验证 → 审核合并 → 🎉 插件出现在 DBCheck 插件市场！

| 标识 | 含义 |
|------|------|
| ✅ 官方 | DBCheck Team 维护 |
| ✅ 已验证 | 社区贡献，已通过审核 |
| 👤 社区 | 社区贡献，待审核 |

> 详见 [完整开发指南](https://github.com/fiyo/dbcheck-plugins/blob/main/DEVELOPER_GUIDE.md)
