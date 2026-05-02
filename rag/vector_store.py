"""
向量存储 — SQLite JSON 列 + numpy 余弦相似度

架构：
- SQLite history.db 中新建 rag_embeddings 表，embedding 列存 JSON 数组
- 检索时加载所有向量到内存，用 numpy 计算余弦相似度，取 TopK
- 轻量实现，无需额外向量库依赖，适合中小规模知识库（<10000 条向量）
"""

import json
import sqlite3
import os
import math

import numpy as np


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """计算两个向量的余弦相似度"""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


class VectorStore:
    """
    基于 SQLite + numpy 的向量存储

    表结构（history.db）：
        rag_embeddings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content     TEXT NOT NULL,
            source      TEXT,
            title       TEXT,
            db_type     TEXT NOT NULL,
            embedding    TEXT NOT NULL,   -- JSON 数组，存储 768 维向量
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(doc_id, chunk_index)
        )

        rag_documents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT UNIQUE NOT NULL,
            db_type     TEXT NOT NULL,
            title       TEXT NOT NULL,
            file_path   TEXT,
            file_size   INTEGER,
            chunk_count INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'active',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """

    COLLECTION_NAME = "dbcheck_rag"

    def __init__(self, db_path: str = "history.db"):
        self.db_path = db_path
        self._init_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """初始化 SQLite 表"""
        conn = self._get_conn()
        try:
            # 文档元数据表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT UNIQUE NOT NULL,
                    db_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_path TEXT,
                    file_size INTEGER,
                    chunk_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # 向量存储表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT,
                    title TEXT,
                    db_type TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(doc_id, chunk_index)
                )
            """)
            # 索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_documents_db_type ON rag_documents(db_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_embeddings_db_type ON rag_embeddings(db_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_embeddings_doc_id ON rag_embeddings(doc_id)")
            conn.commit()
        finally:
            conn.close()

    def add_documents(self, chunks: list[dict], embeddings: list[list[float]]) -> list[int]:
        """
        添加文档块和对应向量

        Args:
            chunks: process_document() 返回的分块列表，每项含 content 和 metadata
            embeddings: 对应的向量列表，与 chunks 一一对应

        Returns:
            新插入记录的 id 列表

        Raises:
            ValueError: chunks 和 embeddings 数量不匹配
        """
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks 数量({len(chunks)}) 与 embeddings 数量({len(embeddings)}) 不匹配")

        conn = self._get_conn()
        try:
            ids = []
            for chunk, emb in zip(chunks, embeddings):
                meta = chunk['metadata']
                emb_json = json.dumps(emb)

                cur = conn.execute(
                    """INSERT OR REPLACE INTO rag_embeddings
                       (doc_id, chunk_index, content, source, title, db_type, embedding)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        meta['doc_id'],
                        meta['chunk_index'],
                        chunk['content'],
                        meta.get('source', ''),
                        meta.get('title', ''),
                        meta['db_type'],
                        emb_json,
                    )
                )
                ids.append(cur.lastrowid)

            conn.commit()
            return ids
        finally:
            conn.close()

    def search(self, query_embedding: list[float],
               db_type: str = None,
               top_k: int = 5) -> list[dict]:
        """
        向量检索，返回格式化的结果列表

        Args:
            query_embedding: 查询向量（list[float]）
            db_type: 限定数据库类型（如 'mysql'），None 表示不限定
            top_k: 返回结果数量

        Returns:
            格式化的结果列表：
            [{
                'doc_id': str,
                'chunk_index': int,
                'content': str,
                'source': str,
                'title': str,
                'db_type': str,
                'score': float  # 余弦相似度，0~1
            }, ...]
        """
        query_vec = np.array(query_embedding, dtype=np.float32)

        conn = self._get_conn()
        try:
            # 构建查询 SQL
            if db_type:
                rows = conn.execute(
                    """SELECT doc_id, chunk_index, content, source, title, db_type, embedding
                       FROM rag_embeddings WHERE db_type = ?""",
                    (db_type,)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT doc_id, chunk_index, content, source, title, db_type, embedding
                       FROM rag_embeddings"""
                ).fetchall()
        finally:
            conn.close()

        # 计算相似度并排序
        scored = []
        for row in rows:
            emb = np.array(json.loads(row['embedding']), dtype=np.float32)
            score = _cosine_similarity(query_vec, emb)
            scored.append({
                'doc_id': row['doc_id'],
                'chunk_index': row['chunk_index'],
                'content': row['content'],
                'source': row['source'] or '',
                'title': row['title'] or '',
                'db_type': row['db_type'],
                'score': score,
            })

        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_k]

    def delete_by_doc_id(self, doc_id: str) -> int:
        """
        删除指定文档的所有向量块

        Returns:
            删除的记录数量
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM rag_embeddings WHERE doc_id = ?", (doc_id,)
            )
            count = cur.fetchone()[0]
            conn.execute("DELETE FROM rag_embeddings WHERE doc_id = ?", (doc_id,))
            conn.commit()
            return count
        finally:
            conn.close()

    def list_doc_ids(self) -> list[str]:
        """列出所有文档 ID（去重）"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT doc_id FROM rag_embeddings"
            ).fetchall()
            return [r['doc_id'] for r in rows]
        finally:
            conn.close()

    def get_collection_stats(self) -> dict:
        """获取集合统计信息"""
        conn = self._get_conn()
        try:
            total_chunks = conn.execute(
                "SELECT COUNT(*) FROM rag_embeddings"
            ).fetchone()[0]
            total_docs = conn.execute(
                "SELECT COUNT(*) FROM rag_documents WHERE status = 'active'"
            ).fetchone()[0]
            by_db_type = {}
            rows = conn.execute(
                """SELECT db_type, COUNT(*) as cnt FROM rag_embeddings
                   GROUP BY db_type"""
            ).fetchall()
            for r in rows:
                by_db_type[r['db_type']] = r['cnt']
            return {
                'total_chunks': total_chunks,
                'total_documents': total_docs,
                'by_db_type': by_db_type,
            }
        finally:
            conn.close()
