"""
文档处理模块 — 加载多种格式文档并按语义分块

支持格式：
- .txt, .md  : 直接读取文本
- .pdf       : PyPDF2 提取文本
- .html, .htm: BeautifulSoup 去除标签
- .docx      : python-docx 提取段落
"""

import os
import re
import uuid


class DocumentProcessor:
    """文档加载、分块、预处理"""

    SUPPORTED_EXTENSIONS = {'.txt', '.md', '.pdf', '.html', '.htm', '.docx'}

    # nomic-embed-text 最大输入 token（估算：1 token ≈ 4 字符）
    DEFAULT_CHUNK_SIZE = 1000   # 字符数
    DEFAULT_CHUNK_OVERLAP = 100  # 重叠字符数

    def __init__(self, chunk_size: int = None, overlap: int = None):
        self.chunk_size = chunk_size or self.DEFAULT_CHUNK_SIZE
        self.overlap = overlap or self.DEFAULT_CHUNK_OVERLAP

    def load_document(self, file_path: str) -> str:
        """
        根据文件扩展名加载文档内容

        Args:
            file_path: 文档路径

        Returns:
            提取的纯文本内容

        Raises:
            ValueError: 不支持的文件类型
            RuntimeError: 文档处理失败
        """
        if not os.path.exists(file_path):
            raise ValueError(f"文件不存在: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()

        if ext not in self.SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文档类型: {ext}，支持: {self.SUPPORTED_EXTENSIONS}")

        if ext in ('.txt', '.md'):
            return self._load_text(file_path)
        elif ext == '.pdf':
            return self._load_pdf(file_path)
        elif ext in ('.html', '.htm'):
            return self._load_html(file_path)
        elif ext == '.docx':
            return self._load_docx(file_path)

    def _load_text(self, file_path: str) -> str:
        """加载纯文本或 Markdown 文件"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def _load_pdf(self, file_path: str) -> str:
        """加载 PDF 文档，提取所有页面的文本"""
        try:
            import PyPDF2
        except ImportError:
            raise RuntimeError(
                "处理 PDF 需要 PyPDF2，请运行: pip install PyPDF2"
            )

        try:
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                pages = []
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text.strip())
                return '\n\n'.join(pages)
        except Exception as e:
            raise RuntimeError(f"PDF 解析失败: {file_path}, 错误: {e}")

    def _load_html(self, file_path: str) -> str:
        """加载 HTML 文档，去除脚本、样式标签后提取正文"""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise RuntimeError(
                "处理 HTML 需要 beautifulsoup4，请运行: pip install beautifulsoup4"
            )

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')

            # 移除脚本和样式
            for tag in soup(['script', 'style', 'noscript', 'iframe', 'nav', 'footer', 'header']):
                tag.decompose()

            # 移除空标签
            text = soup.get_text(separator='\n', strip=True)
            # 合并多余空行
            text = re.sub(r'\n{3,}', '\n\n', text)
            return text
        except Exception as e:
            raise RuntimeError(f"HTML 解析失败: {file_path}, 错误: {e}")

    def _load_docx(self, file_path: str) -> str:
        """加载 DOCX 文档，提取所有段落文本"""
        try:
            import docx
        except ImportError:
            raise RuntimeError(
                "处理 DOCX 需要 python-docx，请运行: pip install python-docx"
            )

        try:
            doc = docx.Document(file_path)
            paragraphs = []
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    paragraphs.append(text)
            return '\n\n'.join(paragraphs)
        except Exception as e:
            raise RuntimeError(f"DOCX 解析失败: {file_path}, 错误: {e}")

    def split_text(self, text: str, chunk_size: int = None,
                   overlap: int = None) -> list[str]:
        """
        按段落分块，保留上下文重叠

        策略：
        1. 先按空行分段（保留段落边界）
        2. 按句子合并，保持块大小适中
        3. 块之间有 overlap 重叠，保持上下文连续性

        Args:
            text: 原始文本
            chunk_size: 每块最大字符数（默认 1000）
            overlap: 相邻块重叠字符数（默认 100）

        Returns:
            分块后的文本列表
        """
        chunk_size = chunk_size or self.chunk_size
        overlap = overlap or self.overlap

        # 预处理：规范化空白字符
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'[ \t]+', ' ', text)

        # 按空行分段（段落边界）
        raw_paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]
        if not paragraphs:
            return []

        # 合并段落形成块
        chunks = []
        current_chunk = []
        current_size = 0

        for para in paragraphs:
            para_len = len(para)

            # 单段落超长：按句子分割
            if para_len > chunk_size:
                # 先处理已有的 current_chunk
                if current_chunk:
                    chunks.append('\n'.join(current_chunk))
                    # 重叠：保留末尾段落
                    current_chunk = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[:]
                    current_size = sum(len(c) for c in current_chunk)

                # 按句子分割超长段落
                sentences = self._split_sentences(para)
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    sent_len = len(sent)
                    if current_size + sent_len > chunk_size and current_chunk:
                        chunks.append('\n'.join(current_chunk))
                        # 保留 overlap
                        overlap_texts = []
                        acc = 0
                        for p in reversed(current_chunk):
                            overlap_texts.insert(0, p)
                            acc += len(p)
                            if acc >= overlap:
                                break
                        current_chunk = overlap_texts
                        current_size = sum(len(c) for c in current_chunk)
                    current_chunk.append(sent)
                    current_size += sent_len
            else:
                if current_size + para_len > chunk_size and current_chunk:
                    chunks.append('\n'.join(current_chunk))
                    # 保留 overlap
                    overlap_texts = []
                    acc = 0
                    for p in reversed(current_chunk):
                        overlap_texts.insert(0, p)
                        acc += len(p)
                        if acc >= overlap:
                            break
                    current_chunk = overlap_texts
                    current_size = sum(len(c) for c in current_chunk)

                current_chunk.append(para)
                current_size += para_len

        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """
        按句子分割文本

        支持中英文标点：
        - 中文：。！？；
        - 英文：. ! ? ;
        """
        # 中英文句子分隔符
        pattern = r'(?<=[。！？；.!?])\s*'
        parts = re.split(pattern, text)
        # 过滤空部分（连续分隔符产生空串）
        return [p.strip() for p in parts if p.strip()]

    def process_document(self, file_path: str, db_type: str,
                          title: str = None,
                          chunk_size: int = None,
                          overlap: int = None) -> list[dict]:
        """
        完整处理流程：加载 → 分块 → 构造结果

        Args:
            file_path: 文档路径
            db_type: 数据库类型（mysql/pg/oracle/dm/sqlserver/tidb）
            title: 文档标题（None 则用文件名）
            chunk_size: 分块大小
            overlap: 重叠大小

        Returns:
            分块列表，每项结构：
            {
                'content': str,       # 文本块内容
                'metadata': {
                    'doc_id': str,   # 文档 UUID（同一文档内相同）
                    'db_type': str,
                    'chunk_index': int,
                    'source': str,
                    'title': str,
                    'chunk_size': int
                }
            }
        """
        doc_id = str(uuid.uuid4())
        doc_title = title or os.path.basename(file_path)

        # 加载文档
        text = self.load_document(file_path)
        if not text.strip():
            raise RuntimeError(f"文档内容为空: {file_path}")

        # 分块
        chunks = self.split_text(text, chunk_size, overlap)
        if not chunks:
            raise RuntimeError(f"文档分块失败: {file_path}")

        # 构造结果
        results = []
        for i, chunk_content in enumerate(chunks):
            results.append({
                'content': chunk_content,
                'metadata': {
                    'doc_id': doc_id,
                    'db_type': db_type,
                    'chunk_index': i,
                    'source': os.path.abspath(file_path),
                    'title': doc_title,
                    'chunk_size': len(chunk_content),
                }
            })

        return results

    def validate_file(self, file_path: str) -> tuple[bool, str]:
        """
        验证文件是否可处理

        Returns:
            (是否可处理, 错误信息)
        """
        if not os.path.exists(file_path):
            return False, "文件不存在"
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            return False, f"不支持的文件类型: {ext}"
        size = os.path.getsize(file_path)
        max_size = 100 * 1024 * 1024  # 100MB
        if size > max_size:
            return False, f"文件过大: {size / 1024 / 1024:.1f}MB（最大 100MB）"
        return True, "OK"
