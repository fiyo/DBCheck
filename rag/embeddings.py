"""
Embedding 接口 — 支持本地 Ollama 和 OpenAI 协议兼容的远程服务

安全约束：
- 本地 Ollama 仅支持 localhost
- 远程 OpenAI 协议需在 ai_config.json 中启用 online_enabled
"""

import json
import urllib.request
import urllib.error


class OllamaEmbedding:
    """使用 Ollama 本地 embedding 模型进行向量化"""

    def __init__(self, api_url: str = "http://localhost:11434",
                 model: str = "nomic-embed-text",
                 timeout: int = 30):
        self.api_url = api_url.rstrip('/')
        self.model = model
        self.timeout = timeout

    def embed_text(self, text: str) -> list[float]:
        """
        向量化单条文本，调用 Ollama /api/embeddings

        Args:
            text: 待向量化的文本

        Returns:
            768 维向量列表（nomic-embed-text 输出维度）

        Raises:
            RuntimeError: Ollama 连接失败或返回格式异常
        """
        url = f"{self.api_url}/api/embeddings"
        payload = json.dumps({
            "model": self.model,
            "prompt": text
        }).encode('utf-8')

        req = urllib.request.Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                # Ollama 成功时返回 {"embedding": [...]}
                # 失败时可能返回 {"error": "..."} 或非 200 状态码
                if 'embedding' in result:
                    return result['embedding']
                elif 'error' in result:
                    raise RuntimeError(f"Ollama 返回错误: {result['error']}")
                else:
                    raise RuntimeError(f"Ollama 返回格式异常: {result}")
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            try:
                err_json = json.loads(body)
                raise RuntimeError(f"Ollama HTTP {e.code}: {err_json.get('error', body)}")
            except json.JSONDecodeError:
                raise RuntimeError(f"Ollama HTTP {e.code}: {body[:200]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama 连接失败: {e}")

    def embed_batch(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        """
        批量向量化

        Args:
            texts: 文本列表
            batch_size: 每批大小（避免单次请求过长）

        Returns:
            向量列表，与输入文本一一对应
        """
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            for text in batch:
                try:
                    results.append(self.embed_text(text))
                except RuntimeError as e:
                    # 单条失败时返回零向量占位，避免中断整批
                    results.append([0.0] * dim)
        return results

    def get_dimension(self) -> int:
        """
        获取向量维度（通过实际请求一次空文本探测）

        Returns:
            向量维度，失败时返回默认值 768
        """
        try:
            vec = self.embed_text("dimension probe")
            return len(vec)
        except Exception:
            return 768  # nomic-embed-text 默认维度


class OpenAIEmbedding:
    """使用 OpenAI 协议兼容的远程 embedding API 进行向量化"""

    def __init__(self, api_url: str = "https://api.openai.com/v1",
                 api_key: str = "",
                 model: str = "text-embedding-3-small",
                 timeout: int = 30):
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed_text(self, text: str) -> list[float]:
        """
        向量化单条文本，调用 /v1/embeddings

        Args:
            text: 待向量化的文本

        Returns:
            向量列表

        Raises:
            RuntimeError: API 连接失败或返回格式异常
        """
        url = f"{self.api_url}/embeddings"
        payload = json.dumps({
            "model": self.model,
            "input": text
        }).encode('utf-8')

        req = urllib.request.Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')
        if self.api_key:
            req.add_header('Authorization', f'Bearer {self.api_key}')

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if 'data' in result and len(result['data']) > 0:
                    return result['data'][0].get('embedding', [])
                elif 'error' in result:
                    raise RuntimeError(f"Embedding API 返回错误: {result['error']}")
                else:
                    raise RuntimeError(f"Embedding API 返回格式异常")
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            try:
                err_json = json.loads(body)
                raise RuntimeError(f"Embedding API HTTP {e.code}: {err_json.get('error', {}).get('message', body)}")
            except json.JSONDecodeError:
                raise RuntimeError(f"Embedding API HTTP {e.code}: {body[:200]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Embedding API 连接失败: {e}")

    def embed_batch(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        """批量向量化"""
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            for text in batch:
                try:
                    results.append(self.embed_text(text))
                except RuntimeError:
                    dim = self.get_dimension()
                    results.append([0.0] * dim)
        return results

    def get_dimension(self) -> int:
        """
        获取向量维度（通过实际请求一次探测）

        Returns:
            向量维度，失败时返回默认值 1536
        """
        try:
            vec = self.embed_text("dimension probe")
            return len(vec)
        except Exception:
            return 1536  # text-embedding-3-small 默认维度
