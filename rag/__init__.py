"""
DBCheck RAG 知识库模块

向量存储：SQLite JSON 列 + numpy 余弦相似度（无需额外向量库依赖）
Embedding：Ollama /api/embeddings（本地、离线、安全）
"""

from .embeddings import OllamaEmbedding
from .vector_store import VectorStore
from .document_processor import DocumentProcessor
from .retriever import RAGRetriever
from .manager import RAGManager

__all__ = [
    'OllamaEmbedding',
    'VectorStore',
    'DocumentProcessor',
    'RAGRetriever',
    'RAGManager',
]
