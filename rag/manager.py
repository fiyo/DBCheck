"""
RAG 文档管理器 — 管理文档的完整生命周期

功能：
- add_document(): 上传文档 → 加载 → 分块 → 向量化 → 存储
- delete_document(): 删除文档（元数据和向量）
- list_documents(): 列出已上传文档
- update_document(): 更新文档（删除旧版 + 重新导入）
"""

import os
import sqlite3
import json

from .vector_store import VectorStore
from .document_processor import DocumentProcessor
from .embeddings import OllamaEmbedding, OpenAIEmbedding


class RAGManager:
    """
    RAG 文档管理器

    整合 DocumentProcessor（文档处理）、OllamaEmbedding（向量化）
    和 VectorStore（向量存储），提供端到端的文档管理能力。

    Args:
        db_path: SQLite 数据库路径（默认 'history.db'）
        api_url: Ollama API 地址
        embedding_model: Embedding 模型名
    """

    DB_TYPES = {'mysql', 'pg', 'oracle', 'dm', 'sqlserver', 'tidb'}

    def __init__(self, db_path: str = "history.db",
                 api_url: str = "http://localhost:11434",
                 embedding_model: str = "nomic-embed-text",
                 config_path: str = "dbc_config.json"):
        self.db_path = db_path
        self.vector_store = VectorStore(db_path)
        self.processor = DocumentProcessor()
        self.api_url = api_url
        self.embedding_model = embedding_model
        self.config_path = config_path
        self.embedding = None
        self._ensure_embedding()

    def _ensure_embedding(self):
        """
        根据 dbc_config.json 的 ai 字段确保使用正确的 Embedding 后端
        每次需要 embedding 前调用，自动跟随 AI 配置切换
        """
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            ai_cfg = cfg.get('ai', {})
        except Exception:
            ai_cfg = {}

        backend = ai_cfg.get('backend', 'ollama')
        online_enabled = ai_cfg.get('online_enabled', False)
        rag_cfg = ai_cfg.get('rag', {})
        rag_model = rag_cfg.get('embedding_model', self.embedding_model)

        use_openai = (backend == 'openai' and online_enabled)

        # 已是正确的后端，跳过重建
        if self.embedding is not None:
            if use_openai and isinstance(self.embedding, OpenAIEmbedding):
                return
            if not use_openai and isinstance(self.embedding, OllamaEmbedding):
                return

        if use_openai:
            self.embedding = OpenAIEmbedding(
                api_url=cfg.get('online_api_url', 'https://api.openai.com/v1'),
                api_key=cfg.get('api_key', '') or cfg.get('online_api_key', ''),
                model=rag_model or 'text-embedding-3-small'
            )
        else:
            self.embedding = OllamaEmbedding(
                api_url=self.api_url,
                model=rag_model or 'nomic-embed-text'
            )

    def add_document(self, file_path: str, db_type: str,
                     title: str = None) -> tuple[bool, str]:
        """
        添加文档：加载 → 分块 → 向量化 → 存储

        Args:
            file_path: 文档文件路径
            db_type: 数据库类型（必须在 DB_TYPES 中）
            title: 文档标题（None 则用文件名）

        Returns:
            (成功?, 消息)

        Raises:
            ValueError: db_type 不合法或文件验证失败
            RuntimeError: 向量化失败或处理异常
        """
        # 确保使用正确的 embedding 后端
        self._ensure_embedding()

        # 验证 db_type
        if db_type.lower() not in self.DB_TYPES:
            return False, (f"无效的数据库类型: {db_type}，"
                           f"支持: {', '.join(sorted(self.DB_TYPES))}")

        db_type = db_type.lower()

        # 验证文件
        ok, msg = self.processor.validate_file(file_path)
        if not ok:
            return False, msg

        # 处理文档
        try:
            chunks = self.processor.process_document(file_path, db_type, title)
        except Exception as e:
            return False, f"文档处理失败: {e}"

        if not chunks:
            return False, "文档分块结果为空"

        # 向量化
        texts = [c['content'] for c in chunks]
        try:
            embeddings = self.embedding.embed_batch(texts)
        except Exception as e:
            return False, f"向量化失败: {e}"

        if not embeddings or len(embeddings) != len(chunks):
            return False, f"向量化结果数量({len(embeddings)})与分块数量({len(chunks)})不匹配"

        # 存储到向量库
        try:
            self.vector_store.add_documents(chunks, embeddings)
        except Exception as e:
            return False, f"向量存储失败: {e}"

        # 存储元数据
        doc_id = chunks[0]['metadata']['doc_id']
        doc_title = title or chunks[0]['metadata']['title']
        file_size = os.path.getsize(file_path)

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO rag_documents
                (doc_id, db_type, title, file_path, file_size, chunk_count, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
            """, (doc_id, db_type, doc_title, os.path.abspath(file_path),
                  file_size, len(chunks)))
            conn.commit()
        finally:
            conn.close()

        return True, (f"文档「{doc_title}」添加成功，"
                      f"共 {len(chunks)} 个分块，已导入向量库")

    def delete_document(self, doc_id: str) -> tuple[bool, str]:
        """
        删除文档（元数据和向量）

        Args:
            doc_id: 文档 UUID

        Returns:
            (成功?, 消息)
        """
        # 删除向量
        chunk_count = self.vector_store.delete_by_doc_id(doc_id)

        # 删除元数据
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                "SELECT title FROM rag_documents WHERE doc_id = ?", (doc_id,)
            )
            row = cur.fetchone()
            doc_title = row[0] if row else doc_id

            conn.execute("DELETE FROM rag_documents WHERE doc_id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()

        if chunk_count == 0 and not row:
            return False, f"未找到文档: {doc_id}"

        return True, f"文档「{doc_title}」已删除（{chunk_count} 个向量块）"

    def list_documents(self, db_type: str = None) -> list[dict]:
        """
        列出文档

        Args:
            db_type: 过滤特定数据库类型（None 表示全部）

        Returns:
            文档列表，每项含 id, doc_id, db_type, title, file_size, chunk_count, created_at
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if db_type and db_type.lower() in self.DB_TYPES:
                rows = conn.execute("""
                    SELECT id, doc_id, db_type, title, file_path, file_size,
                           chunk_count, status, created_at
                    FROM rag_documents
                    WHERE db_type = ? AND status = 'active'
                    ORDER BY created_at DESC
                """, (db_type.lower(),)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, doc_id, db_type, title, file_path, file_size,
                           chunk_count, status, created_at
                    FROM rag_documents
                    WHERE status = 'active'
                    ORDER BY created_at DESC
                """).fetchall()

            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """
        获取知识库统计信息

        Returns:
            {
                'total_documents': int,
                'total_chunks': int,
                'by_db_type': {'mysql': n, ...},
                'embedding_model': str,
            }
        """
        self._ensure_embedding()
        vs_stats = self.vector_store.get_collection_stats()

        conn = sqlite3.connect(self.db_path)
        try:
            total_docs = conn.execute(
                "SELECT COUNT(*) FROM rag_documents WHERE status = 'active'"
            ).fetchone()[0]
        finally:
            conn.close()

        return {
            'total_documents': total_docs,
            'total_chunks': vs_stats['total_chunks'],
            'by_db_type': vs_stats['by_db_type'],
            'embedding_model': self.embedding.model,
        }

    def check_embedding_connection(self) -> tuple[bool, str]:
        """
        检查当前 Embedding 后端连接状态（自动跟随 AI 配置）

        Returns:
            (连接正常?, 状态消息)
        """
        self._ensure_embedding()
        backend_name = self.embedding.__class__.__name__.replace('Embedding', '')
        try:
            dim = self.embedding.get_dimension()
            return True, f"{backend_name} 连接正常（模型: {self.embedding.model}, 维度: {dim}）"
        except Exception as e:
            return False, f"{backend_name} 连接失败: {e}"

    def check_ollama_connection(self) -> tuple[bool, str]:
        """向后兼容：调用 check_embedding_connection()"""
        return self.check_embedding_connection()
