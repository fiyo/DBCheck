"""
DBCheck RAG 知识库模块

向量存储：SQLite JSON 列 + numpy 余弦相似度（无需额外向量库依赖）
Embedding：Ollama / OpenAI 协议兼容的远程 API
"""

from .embeddings import OllamaEmbedding, OpenAIEmbedding
from .vector_store import VectorStore
from .document_processor import DocumentProcessor
from .retriever import RAGRetriever
from .manager import RAGManager

__all__ = [
    'OllamaEmbedding',
    'OpenAIEmbedding',
    'VectorStore',
    'DocumentProcessor',
    'RAGRetriever',
    'RAGManager',
]
