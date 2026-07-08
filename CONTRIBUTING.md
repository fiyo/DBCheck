# 贡献指南 / Contributing

感谢你参与 DBCheck 的贡献！ 🎉

## 提交流程 / How to Contribute

1. Fork 本仓库，并基于 `main` 分支创建特性分支（`feat/xxx`、`fix/xxx`）。
2. 保持改动聚焦：**一个 PR 解决一个问题**。
3. 提交信息清晰，建议遵循 Conventional Commits：
   `feat:` / `fix:` / `docs:` / `refactor:` / `chore:`。
4. 提交 PR 前，请确保本地可正常运行、无语法错误。

## 代码风格 / Code Style

- **后端（Python）**：遵循 PEP8；关键逻辑加必要注释。
  > 例外：**强制灰度逻辑**（`web_ui.py` 中纪念日灰度相关代码）**不要加注释**。
- **前端（原生 HTML/JS）**：集中在 `web_templates/index.html`。
  新增左侧菜单项时，请沿用**单色 SVG 线条图标**风格，**勿用 emoji 多色图标**。

## 🚫 安全红线（必读 / Hard Rules）

- **禁止在 issue / PR / 讨论中提交可执行文件、脚本或外部下载链接。**
  此类内容将被直接删除，情节严重者封号。
- 涉及**安全漏洞**，请走 `SECURITY.md` 中的私有上报通道，而非公开 issue。

## 行为准则 / Code of Conduct

请友善、专业地交流。恶意、骚扰或滥用行为将被处理。

## 联系方式 / Contact

- 邮箱：sdfiyon@gmail.com
- 官网：https://dbcheck.top
