"""
RAG 检索器 — 将向量检索结果格式化为 Prompt 上下文

核心方法：
- retrieve_for_diagnosis(): 为 AI 诊断场景构建检索查询并格式化结果
- format_rag_context(): 将检索结果格式化为 LLM 可读的文本
"""

from .vector_store import VectorStore
from .embeddings import OllamaEmbedding


class RAGRetriever:
    """RAG 检索器，整合查询构建和结果格式化"""

    def __init__(self, vector_store: VectorStore, embedding_model: OllamaEmbedding = None):
        self.vector_store = vector_store
        self.embedding_model = embedding_model or OllamaEmbedding()

    def retrieve_for_diagnosis(self, db_type: str, metrics: dict,
                               issues: list, top_k: int = 3) -> str:
        """
        为数据库诊断场景检索相关文档片段

        检索策略：
        1. 从 issues 提取风险关键词（col1/col2/col3）
        2. 从 metrics 提取异常指标名称
        3. 构建多路查询: ["{db_type} {关键词}", "{db_type} {指标名}", ...]
        4. 合并去重各查询结果，取 TopK

        Args:
            db_type: 数据库类型（mysql/pg/oracle/dm/sqlserver/tidb）
            metrics: 巡检指标字典
            issues: 风险项列表
            top_k: 返回结果数量

        Returns:
            格式化的 RAG 上下文文本（供注入 Prompt），空字符串表示无结果
        """
        # 构建检索查询
        query_texts = self._build_diagnosis_queries(db_type, metrics, issues)
        if not query_texts:
            return ''

        # 多路检索，收集所有结果
        all_results = []
        seen_ids = set()

        for query_text in query_texts[:3]:  # 最多 3 个查询
            try:
                query_emb = self.embedding_model.embed_text(query_text)
                results = self.vector_store.search(
                    query_emb, db_type=db_type, top_k=top_k
                )
                for res in results:
                    key = f"{res['doc_id']}_{res['chunk_index']}"
                    if key not in seen_ids:
                        seen_ids.add(key)
                        all_results.append(res)
            except Exception:
                # 单次查询失败不影响整体
                continue

        if not all_results:
            return ''

        # 按相似度排序取 TopK
        all_results.sort(key=lambda x: x['score'], reverse=True)
        top_results = all_results[:top_k]

        return self.format_rag_context(top_results, lang='zh')

    def _build_diagnosis_queries(self, db_type: str, metrics: dict,
                                  issues: list) -> list[str]:
        """
        从诊断上下文构建检索查询列表

        生成 2~5 个查询，覆盖不同角度：
        - 数据库类型 + 风险类型
        - 数据库类型 + 具体指标
        - 数据库类型 + SQL 语句/错误信息
        """
        queries = []

        # 映射中文 db_type 到英文（便于匹配英文文档）
        type_map = {
            'mysql': 'MySQL',
            'pg': 'PostgreSQL',
            'oracle': 'Oracle',
            'sqlserver': 'SQL Server',
            'tidb': 'TiDB',
            'dm': 'DM8',
        }
        db_name = type_map.get(db_type.lower(), db_type.upper())

        # 从 issues 提取关键词
        risk_keywords = []
        for issue in issues[:5]:
            col1 = issue.get('col1', '')
            col3 = issue.get('col3', '')
            if col1:
                risk_keywords.append(col1[:30])
            if col3:
                # 提取 col3 中的关键名词（去掉句号后的第一句）
                first_sent = col3.split('。')[0].split('.')[0].strip()
                if len(first_sent) < 50:
                    risk_keywords.append(first_sent[:40])

        # 从 metrics 提取异常指标
        metric_keywords = []
        metric_keys = [
            'slow_query_count', 'avg_lock_time', 'cache_hit_ratio',
            'connection_usage', 'disk_usage_max', 'cpu_usage', 'mem_usage',
            'wait_events_top5', 'blocked_sessions', 'long_running_transactions',
            'replication_lag', 'binlog_size', 'undo_tablespace_size',
        ]
        for key in metric_keys:
            if key in metrics and metrics[key] not in (None, 'N/A', '', 0, 0.0):
                label = key.replace('_', ' ')
                metric_keywords.append(label)

        # 组合查询
        if risk_keywords:
            queries.append(f"{db_name} {risk_keywords[0]}")
        if metric_keywords:
            queries.append(f"{db_name} {metric_keywords[0]} performance tuning")

        # 添加通用诊断查询
        queries.append(f"{db_name} database health check best practices")

        # 组合长查询
        if risk_keywords and metric_keywords:
            queries.append(f"{db_name} {risk_keywords[0]} {metric_keywords[0]}")

        # 去重
        seen = set()
        unique_queries = []
        for q in queries:
            if q.lower() not in seen:
                seen.add(q.lower())
                unique_queries.append(q)

        return unique_queries

    def format_rag_context(self, results: list[dict], lang: str = 'zh') -> str:
        """
        将检索结果格式化为 Prompt 可用的上下文

        Args:
            results: vector_store.search() 返回的结果列表
            lang: 'zh' 或 'en'

        Returns:
            格式化的上下文文本
        """
        if not results:
            return ''

        if lang == 'zh':
            header = ["## 参考文档知识库\n"]
            header.append(f"（检索到 {len(results)} 条相关文档片段，请优先参考）\n")
        else:
            header = ["## Reference Documentation Knowledge Base\n"]
            header.append(f"(Found {len(results)} relevant fragments, please refer first)\n\n")

        lines = []
        for i, res in enumerate(results, 1):
            title = res.get('title', '未知来源')
            source = res.get('source', '')
            db_type = res.get('db_type', '')
            score = res.get('score', 0)
            content = res.get('content', '')

            # 截断每块内容（避免 Prompt 过长，单块最多 300 字）
            if len(content) > 300:
                content = content[:300] + '...'

            lines.append(f"### 片段 {i}｜{title}｜相关度 {score:.2f}｜{db_type}")
            if lang == 'zh':
                lines.append(f"来源: {source or '未知'}")
            else:
                lines.append(f"Source: {source or 'Unknown'}")
            lines.append(content)
            lines.append("")  # 空行分隔

        return '\n'.join(header + lines)

    def test_retrieve(self, query_text: str, db_type: str = None,
                      top_k: int = 5) -> list[dict]:
        """
        测试检索（供 Web UI 调用）

        Returns:
            检索结果列表（未格式化）
        """
        try:
            emb = self.embedding_model.embed_text(query_text)
            return self.vector_store.search(emb, db_type=db_type, top_k=top_k)
        except Exception as e:
            raise RuntimeError(f"检索失败: {e}")
