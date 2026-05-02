# DBCheck 官网部署指南

本文件夹包含 DBCheck 的静态网站，可直接部署到 GitHub Pages。

## 快速部署到 GitHub Pages

### 方法一：使用 docs 文件夹（推荐）

1. 将整个 `docs` 文件夹推送到 GitHub 仓库的 `docs` 分支
2. 在 GitHub 仓库 Settings → Pages → Source 中选择 `Branch: docs /`
3. 访问 `https://fiyo.github.io/DBCheck`

```bash
cd DBCheck
git checkout -b docs
git add docs/
git commit -m "Add GitHub Pages site"
git push origin docs
```

然后在 GitHub 仓库设置中：
- Settings → Pages → Source → 选择 `Branch: docs`
- 保存后等待几分钟即可访问

### 方法二：使用 gh-pages 分支

1. 在仓库根目录创建 `CNAME` 文件（可选，用于自定义域名）
2. 使用 GitHub Actions 自动部署

在 `.github/workflows/pages.yml` 中添加：

```yaml
name: GitHub Pages

on:
  push:
    branches: [main]

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Setup Pages
        uses: actions/configure-pages@v4
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: 'docs'
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

## 自定义域名（可选）

如果需要使用自定义域名：

1. 在 `docs` 文件夹中添加 `CNAME` 文件，内容为你的域名：
   ```
   db.yourdomain.com
   ```

2. 在你的域名 DNS 设置中添加：
   - CNAME 记录：`www` → `fiyo.github.io`
   - A 记录：`@` → `185.199.108.153` 等 GitHub Pages IP

## 本地预览

直接在浏览器中打开 `docs/index.html` 即可预览。

或者使用任意静态服务器：

```bash
# Python
python -m http.server 8000

# Node.js
npx serve

# PHP
php -S localhost:8000
```

然后访问 http://localhost:8000

## 网站功能

- 🌍 响应式设计，支持手机/平板/电脑
- ✨ 流畅动画效果（滚动动画、悬浮效果）
- 🌟 科技感深色主题
- 📱 支持中英文
- 🔗 GitHub 集成

## 版本管理

网站显示的版本号自动从项目根目录的 `version.py` 读取。

**更新版本号的步骤：**

1. 修改 `version.py` 中的 `__version__` 值：
   ```python
   __version__ = "v2.4.0"
   ```

2. 同步更新 `version.json`：
   ```json
   {
     "version": "v2.4.0",
     "lastUpdated": "2026-05-02"
   }
   ```

> **重要**：每次发布新版本前，请务必同时更新 `version.py` 和 `version.json`，否则网站显示的版本号不会更新。

## 技术栈

- 纯 HTML5 + CSS3 + Vanilla JavaScript
- 无需构建工具
- CDN 依赖：
  - Google Fonts (Inter, JetBrains Mono)
  - Font Awesome 6

## 维护

网站代码位于 `docs/index.html`，可直接编辑。

如需修改内容，搜索对应的中文文本即可。页面结构：
- `#features` - 功能特性
- `#databases` - 支持的数据库
- `#quickstart` - 快速上手
- `#architecture` - 工作原理
