# DBCheck v2.5.0 发版说明 — RAG 知识库

## 版本概要

**版本号：** v2.5.0  
**发版日期：** 2026-05-02  
**核心新增：** RAG（检索增强生成）知识库，支持上传数据库文档并向量化，AI 诊断时自动检索相关知识库内容

---

## 新增功能

### 1. RAG 知识库模块（`rag/`）

新增完整的 RAG 文档处理与向量检索流水线：

| 文件 | 功能 |
|------|------|
| `rag/__init__.py` | 包初始化 |
| `rag/document_processor.py` | 文档加载 + 语义分块 |
| `rag/embeddings.py` | Ollama Embedding API 调用（nomic-embed-text） |
| `rag/vector_store.py` | 向量存储（SQLite + 向量表）|
| `rag/manager.py` | RAG 管理器：文档 CRUD + Ollama 健康检查 |
| `rag/retriever.py` | 向量检索器：相似度搜索 + 上下文拼接 |

**支持的文档格式：** `.txt`、`.md`、`.pdf`、`.html`、`.htm`、`.docx`

**分块策略：**
- 按段落分割，支持中英文句子边界检测
- 默认分块大小 1000 字符，重叠 100 字符
- 适用于 `nomic-embed-text` 的 token 限制

### 2. Web UI 知识库页面

在 Web UI 中新增「RAG 知识库」导航页（📚 图标）：

- **上传文档：** 选择文件 + 适用数据库类型 + 标题，上传后自动分块并向量化
- **文档列表：** 显示已上传文档（标题、数据库类型、文件大小、分块数、状态）
- **删除文档：** 按钮删除，使用 toast 确认弹窗（替换原生 alert）
- **Ollama 状态检测：** 页面加载时自动检测 Ollama 连接状态

**新增 API 接口：**

| 接口 | 方法 | 功能 |
|-------|------|------|
| `/api/rag/documents` | GET | 列出所有已上传文档 |
| `/api/rag/documents` | POST | 上传文档并向量化 |
| `/api/rag/documents/<doc_id>` | DELETE | 删除文档 |
| `/api/rag/ollama-status` | GET | 检测 Ollama 连接状态 |

### 3. AI 诊断集成 RAG

AI 诊断时自动检索知识库相关内容，拼接为上下文注入 prompt：

```python
# analyzer.py 中新增
context = retriever.retrieve(query, db_type=db_type, top_k=3)
# context 注入 LLM prompt，提升诊断准确率
```

---

## Bug 修复

### RAG 模块
- 修复 `document_processor.py`：`os.path.path.getsize` → `os.path.getsize`（Windows 路径调用错误）
- 修复 `embeddings.py`：`embed_text()` 未处理 `HTTPError`，Ollama 返回非 200 时异常信息不清晰
- 修复 `embeddings.py`：`embed_batch()` 内冗余 `import numpy`，移除
- 修复 `manager.py`：`add_document()` / `delete_document()` 返回值解包错误（tuple → 正确解包）

### Web UI
- 修复 `web_ui.py`：`api_rag_upload_document` 中将 `tuple` 当 `dict` 使用（`result.get('ok')`）
- 修复 `web_ui.py`：`api_rag_delete_document` 中 `mgr.delete_document()` 返回值未正确解包
- 修复 `index.html`：删除按钮无点击事件绑定（补上事件委托）
- 修复 `index.html`：`tbody.addEventListener` 写在 `const tbody` 声明之前，导致 TDZ 错误
- 修复 `index.html`：事件监听器在 `loadRagDocuments()` 内重复绑定，导致点一次弹多个确认框（改为 `initApp()` 内只绑定一次）
- 修复 `index.html`：上传成功后分块数显示 `?`（改为从 message 字符串正则提取）
- 修复 `index.html`：上传 / 删除操作使用原生 `alert`，改为 `toastError` / `toastSuccess` / `toastConfirm`
- 修复 `index.html`：`toastConfirm` 使用 `id` 绑定按钮，多个确认框共存时会串（改为 `el.querySelector` 按父元素隔离）

### 其他
- 修复 `dbcheck.spec`：缺少 `rag/` 目录 datas 及 `rag.*` hidden imports，PyInstaller 打包时 RAG 模块丢失
- 修复 i18n 国际化：定时巡检 / 通知设置 / RAG 知识库三个页面英文版存在大量中文硬编码，新增约 72 个 i18n 翻译键并替换全部硬编码文本

---

## 依赖变更

**新增依赖（加入 `requirements.txt`）：**

```
sentence-transformers>=2.2.0   # 本地向量化（可选，当前用 Ollama）
PyPDF2>=3.0.0                # PDF 文档加载
python-docx>=0.8.11           # DOCX 文档加载
beautifulsoup4>=4.11.0        # HTML 文档加载
```

**Ollama 依赖（用户自行安装）：**

```bash
ollama pull nomic-embed-text   # Embedding 模型
ollama pull llama3              # 诊断 LLM（已有则无需重复）
```

---

## 升级指南

### 已有用户升级步骤

1. **拉取代码**
   ```bash
   git pull origin main
   ```

2. **安装新增 Python 依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **拉取 Ollama Embedding 模型**
   ```bash
   ollama pull nomic-embed-text
   ```

4. **重启 Web UI**
   ```bash
   python web_ui.py
   ```

5. **验证**：访问 Web UI → 点击「📚 RAG 知识库」→ 查看 Ollama 状态是否显示 ✅ 已连接

### 首次使用 RAG 知识库

1. 在 Ollama 中拉取 `nomic-embed-text`：
   ```bash
   ollama pull nomic-embed-text
   ```
2. 启动 Web UI，进入「RAG 知识库」页面
3. 上传数据库官方文档 / 运维手册（支持 PDF、Word、Markdown 等）
4. 执行 AI 诊断时，系统自动检索知识库相关内容并注入 prompt

---

## 已知问题

- [ ] RAG 检索结果暂无 UI 展示（诊断时用户看不到引用了哪些文档片段）
- [ ] 向量存储当前基于 SQLite，大规模文档（>1000 个文档）检索性能待优化
- [ ] 尚未支持文档版本管理（重复上传同一文档会产生重复向量）

---

## 贡献者

- sdfiyon@gmail.com

---

*DBCheck — 开源数据库健康检查工具*
