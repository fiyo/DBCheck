# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
Oracle AWR HTML 报告解析器 v2.1
支持三种 AWR 格式:
  1. 现代 CSS 风格: <h2>/<h3> 标题, class="awrbg" 表头
  2. 经典 font 风格: <font size="5/4/2"> 标题, bgcolor 表头
  3. Report Summary <p /> 子标题 (Load Profile, Instance Efficiency 等)
零外部依赖，仅使用 Python 标准库 html.parser。
"""

import re
import os
from html.parser import HTMLParser
from typing import Dict, List, Any, Optional, Tuple


# ════════════════════════════════════════════════════════════════
#  章节识别: h3/p 标题关键词 -> (一级字段, 二级子字段或None)
#  注意: 更具体的关键词放在前面，避免短关键词先匹配
# ════════════════════════════════════════════════════════════════
H3_KEYWORDS: Dict[str, Tuple[str, Optional[str]]] = {
    # ── Report Summary 子章节 (也用于 <p /> 标题匹配) ──
    'load profile': ('load_profile', None),
    'instance efficiency percentages': ('instance_efficiency', None),
    'top 10 foreground events by total wait time': ('fg_wait_events', 'top10'),
    'wait classes by total wait time': ('fg_wait_events', 'wait_classes'),
    'host cpu': ('host_cpu', None),
    'instance cpu': ('instance_cpu', None),
    'io profile': ('io_profile', None),
    'memory statistics': ('memory_stats', None),
    'cache sizes': ('cache_sizes', None),
    'shared pool statistics': ('shared_pool_stats', None),

    # ── Wait Events Statistics (h2 #21) ──
    'time model statistics': ('time_model', None),
    'operating system statistics - detail': ('os_stats', 'detail'),
    'operating system statistics': ('os_stats', None),
    'foreground wait class': ('fg_wait_events', 'wait_class'),
    'foreground wait events': ('fg_wait_events', 'events'),
    'background wait events': ('bg_wait_events', 'events'),
    'background wait class': ('bg_wait_events', 'wait_class'),
    'wait event histogram': ('wait_histogram', None),
    'service statistics': ('service_stats', 'service'),
    'service wait class stats': ('service_stats', 'wait_class'),
    'top 10 channel waits': ('service_stats', 'channel'),
    'top process types by wait class': ('process_types', 'wait_class'),
    'top process types by cpu used': ('process_types', 'cpu'),
    'module statistics': ('service_stats', 'module'),
    'action statistics': ('service_stats', 'action'),

    # ── SQL Statistics (h2 #22) ──
    'sql ordered by elapsed time': ('top_sql', 'elapsed'),
    'sql ordered by cpu time': ('top_sql', 'cpu'),
    'sql ordered by user i/o wait time': ('top_sql', 'user_io'),
    'sql ordered by gets': ('top_sql', 'gets'),
    'sql ordered by reads': ('top_sql', 'reads'),
    'sql ordered by physical reads (unoptimized)': ('top_sql', 'physical_reads_unopt'),
    'sql ordered by executions': ('top_sql', 'executions'),
    'sql ordered by parse calls': ('top_sql', 'parse_calls'),
    'sql ordered by sharable mem': ('top_sql', 'sharable_mem'),
    'sql ordered by version count': ('top_sql', 'version_count'),
    'sql ordered by buffer gets': ('top_sql', 'buffer_gets'),
    'sql ordered by physical reads': ('top_sql', 'physical_reads'),
    'sql ordered by logical reads': ('top_sql', 'logical_reads'),
    'sql with plan changes': ('sql_plan_changes', None),
    'complete list of sql text': ('sql_text', None),

    # ── Instance Activity Statistics (h2 #23) ──
    'key instance activity stats': ('instance_activity', 'key'),
    'instance activity stats - absolute values': ('instance_activity', 'absolute'),
    'instance activity stats - thread activity': ('instance_activity', 'thread'),
    'instance activity stats': ('instance_activity', 'stats'),
    'instance activity statistics': ('instance_activity', None),

    # ── IO Stats (h2 #24) ──
    'iostat by function/filetype summary': ('file_io', 'function_filetype'),
    'iostat by function summary': ('file_io', 'function'),
    'iostat by filetype summary': ('file_io', 'filetype'),
    'file io statistics': ('file_io', 'file'),
    'tablespace io stats': ('tablespace_io', None),

    # ── Buffer Pool (h2 #25) ──
    'buffer pool statistics': ('buffer_pool', None),
    'checkpoint activity': ('buffer_pool', 'checkpoint'),
    'instance recovery stats': ('buffer_pool', 'recovery'),
    'mttr advisory': ('advisory', 'mttr'),
    'buffer pool advisory': ('advisory', 'buffer_pool'),

    # ── Segment Statistics ──
    'segments by physical read requests': ('segment_stats', 'physical_read_requests'),
    'segments by direct physical reads': ('segment_stats', 'direct_physical_reads'),
    'segments by physical write requests': ('segment_stats', 'physical_write_requests'),
    'segments by direct physical writes': ('segment_stats', 'direct_physical_writes'),
    'segments by table scans': ('segment_stats', 'table_scans'),
    'segments by db blocks changes': ('segment_stats', 'db_blocks_changes'),
    'segments by row lock waits': ('segment_stats', 'row_lock_waits'),
    'segments by itl waits': ('segment_stats', 'itl_waits'),
    'segments by buffer busy waits': ('segment_stats', 'buffer_busy_waits'),
    'segments by optimized reads': ('segment_stats', 'optimized_reads'),
    'segments by unoptimized reads': ('segment_stats', 'unoptimized_reads'),
    'segment statistics by buffer gets': ('segment_stats', 'buffer_gets'),
    'segments by buffer gets': ('segment_stats', 'buffer_gets'),
    'segment statistics by physical reads': ('segment_stats', 'physical_reads'),
    'segments by physical reads': ('segment_stats', 'physical_reads'),
    'segment statistics by logical reads': ('segment_stats', 'logical_reads'),
    'segments by logical reads': ('segment_stats', 'logical_reads'),
    'segments by physical writes': ('segment_stats', 'physical_writes'),

    # ── Advisory (h2 #26) ──
    'undo advisory': ('advisory', 'undo'),
    'optimizer memory advisory': ('advisory', 'optimizer'),
    'sga target advisory': ('advisory', 'sga_target'),
    'pga memory advisory': ('advisory', 'pga_target'),
    'pga aggr target histogram': ('advisory', 'pga_histogram'),
    'pga aggr target stats': ('advisory', 'pga_stats'),
    'pga aggr summary': ('advisory', 'pga_summary'),
    'shared pool advisory': ('advisory', 'shared_pool'),
    'streams pool advisory': ('advisory', 'streams_pool'),
    'java pool advisory': ('advisory', 'java_pool'),

    # ── Wait Statistics (h2 #27) ──
    'buffer wait statistics': ('wait_stats', 'buffer'),
    'enqueue activity': ('wait_stats', 'enqueue'),
    'wait statistic names': ('wait_stats', 'names'),
    'wait statistics': ('wait_stats', 'stats'),

    # ── Undo Statistics (h2 #28) ──
    'undo and rollback related statistics': ('undo_stats', None),
    'undo statistics': ('undo_stats', None),
    'undo segment stats': ('undo_stats', 'segments'),
    'undo segment statistics': ('undo_stats', 'segments'),
    'undo segment summary': ('undo_stats', 'summary'),
    'redistribution of undo block classes': ('undo_stats', 'block_classes'),

    # ── Latch Statistics (h2 #29) ──
    'latch statistics': ('latch_stats', 'latch'),
    'latch activity': ('latch_stats', 'activity'),
    'latch sleep breakdown': ('latch_stats', 'sleep'),
    'latch miss sources': ('latch_stats', 'misses'),
    'mutex sleep summary': ('latch_stats', 'mutex'),
    'parent latch statistics': ('latch_stats', 'parent'),
    'child latch statistics': ('latch_stats', 'child'),

    # ── Dictionary Cache ──
    'dictionary cache stats': ('dictionary_cache', None),
    'dictionary cache statistics': ('dictionary_cache', None),

    # ── Library Cache ──
    'library cache activity': ('library_cache', None),
    'library cache statistics': ('library_cache', None),
    'object statistics by pin/reload': ('object_stats', 'pin_reload'),
    'objects with most pin/reload': ('object_stats', 'pin_reload'),

    # ── Memory Statistics (h2 #33) ──
    'memory dynamic components': ('memory_stats', 'dynamic'),
    'memory resize operations summary': ('memory_stats', 'resize_summary'),
    'memory resize ops': ('memory_stats', 'resize_ops'),
    'process memory summary': ('memory_stats', 'process'),
    'sga memory summary': ('sga_memory', 'summary'),
    'sga breakdown difference by pool and name': ('sga_memory', 'breakdown'),

    # ── Initialization Parameters (h2 #36) ──
    'parameters modified by this container': ('init_params', 'this'),
    'parameters modified by other containers': ('init_params', 'other'),
    'initialization parameters': ('init_params', None),

    # ── ASH (h2 #42) ──
    'active session history': ('ash', None),
    'ash top activity': ('ash', 'top_activity'),
    'ash load profile': ('ash', 'load_profile'),
    'ash wait class': ('ash', 'wait_class'),
    'ash service stats': ('ash', 'service'),
    'top sql with top events': ('ash', 'top_sql_events'),
    'top sql with top row sources': ('ash', 'top_sql_rows'),
    'top sessions': ('ash', 'sessions'),
    'top blocking sessions': ('ash', 'blocking'),
    'top pl/sql procedures': ('ash', 'plsql'),
    'top events': ('ash', 'events'),
    'top db objects': ('ash', 'objects'),
    'activity over time': ('ash', 'time'),

    # ── ADDM ──
    'addm findings': ('addm', 'findings'),
    'addm reports': ('addm', 'reports'),
}

# h2 标题关键词 -> 一级字段
H2_KEYWORDS: Dict[str, str] = {
    'wait events statistics': 'wait_section',
    'sql statistics': 'sql_section',
    'instance activity statistics': 'instance_activity_section',
    'io stats': 'io_section',
    'buffer pool statistics': 'buffer_pool_section',
    'advisory statistics': 'advisory_section',
    'wait statistics': 'wait_stats_section',
    'undo statistics': 'undo_section',
    'latch statistics': 'latch_section',
    'segment statistics': 'segment_section',
    'in-memory segment statistics': 'inmemory_section',
    'dictionary cache statistics': 'dictionary_cache_section',
    'library cache statistics': 'library_cache_section',
    'memory statistics': 'memory_section',
    'initialization parameters': 'init_params_section',
    'active session history': 'ash_section',
    'addm task': 'addm_section',
    'report summary': 'summary_section',
    'main report': '',
}


class AWRParser(HTMLParser):
    """Oracle AWR HTML 报告解析器 v2.1"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.awr_data = self._empty_data()
        self.current_field: Optional[Tuple[str, Optional[str]]] = None
        self.current_h2_section: str = ''
        self.in_table = False
        self.table_depth = 0
        self.in_tr = False
        self.in_th = False
        self.in_td = False
        self.current_cell = ''
        self.current_row: List[str] = []
        self.table_headers: List[str] = []
        self.table_rows: List[List[str]] = []
        self.row_is_header = True
        self.font_stack: List[str] = []
        self.current_text = ''
        self.capturing_heading = False
        self.heading_size = None
        self.found_first_h2 = False
        self.in_thead = False
        # void <p /> 标题捕获状态
        self.pending_p_void = False
        self.seen_void_p = False

    @staticmethod
    def _empty_data() -> Dict[str, Any]:
        return {
            'metadata': {},
            'load_profile': [],
            'instance_efficiency': [],
            'io_profile': [],
            'memory_stats': [],
            'cache_sizes': [],
            'shared_pool_stats': [],
            'host_cpu': [],
            'instance_cpu': [],
            'time_model': [],
            'os_stats': [],
            'fg_wait_events': [],
            'bg_wait_events': [],
            'wait_histogram': [],
            'service_stats': [],
            'process_types': [],
            'top_sql': {
                'elapsed': [], 'cpu': [], 'executions': [],
                'logical_reads': [], 'physical_reads': [],
                'parse_calls': [], 'sharable_mem': [], 'buffer_gets': [],
                'gets': [], 'reads': [], 'user_io': [],
                'physical_reads_unopt': [], 'version_count': []
            },
            'sql_plan_changes': [],
            'sql_text': [],
            'instance_activity': [],
            'file_io': [],
            'tablespace_io': [],
            'buffer_pool': [],
            'segment_stats': {
                'buffer_gets': [], 'physical_reads': [], 'logical_reads': [],
                'physical_writes': [], 'physical_read_requests': [],
                'physical_write_requests': [], 'direct_physical_reads': [],
                'direct_physical_writes': [], 'table_scans': [],
                'db_blocks_changes': [], 'row_lock_waits': [],
                'itl_waits': [], 'buffer_busy_waits': [],
                'optimized_reads': [], 'unoptimized_reads': []
            },
            'advisory': [],
            'wait_stats': [],
            'undo_stats': [],
            'redo_stats': [],
            'latch_stats': [],
            'dictionary_cache': [],
            'library_cache': [],
            'object_stats': [],
            'init_params': [],
            'ash': [],
            'addm': [],
            'cache_fusion': [],
            'sga_memory': [],
            'extra_sections': [],
        }

    def _unescape(self, text: str) -> str:
        replacements = {
            '&lt;': '<', '&gt;': '>', '&amp;': '&',
            '&quot;': '"', '&#39;': "'", '&nbsp;': ' ',
            '&#160;': ' ',
        }
        for k, v in replacements.items():
            text = text.replace(k, v)
        return text.strip()

    # ─── HTML 标签处理 ─────────────────────────────────────────

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        """处理自闭合标签 (如 <p />)。这是 <p /> 子标题捕获的关键。"""
        tag = tag.lower()
        if tag == 'p' and not self.in_table:
            if self.capturing_heading and self.seen_void_p:
                # 第二个 <p /> — 处理标题文本
                self._process_heading('p', self.current_text.strip())
                self.capturing_heading = False
                self.current_text = ''
                self.seen_void_p = False
            elif not self.in_table:
                # 第一个 <p /> — 准备捕获标题
                self.pending_p_void = True
                self.capturing_heading = True
                self.heading_size = 'p'
                self.current_text = ''
                self.seen_void_p = True

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        d = dict(attrs)
        tag = tag.lower()

        # 如果遇到非标题标签且正在捕获标题，结束标题
        # 但排除 <a>/<span>（可能在 <h3> 内部）和避免中断 void-p 捕获
        if self.capturing_heading and not self.seen_void_p \
                and tag not in ('h1', 'h2', 'h3', 'p', 'font', 'a', 'span'):
            self._end_heading_if_open()

        # 新非 void 标题标签覆盖 pending_p_void
        if self.pending_p_void and tag in ('h2', 'h3', 'p'):
            self.pending_p_void = False

        # ── h2 / h3 / p 标题 ──
        if tag in ('h2', 'h3', 'p'):
            self._end_heading_if_open()
            self.capturing_heading = True
            self.heading_size = tag
            self.current_text = ''
            self.pending_p_void = False
            self.seen_void_p = False
            return

        # ── font 标签 (经典风格) ──
        if tag == 'font':
            size = d.get('size', '')
            self.font_stack.append(size)
            if size in ('5', '4', '2') and not self.in_table:
                self._end_heading_if_open()
                self.capturing_heading = True
                self.heading_size = size
                self.current_text = ''
                self.pending_p_void = False
                self.seen_void_p = False
            return

        # ── 表格 (支持嵌套，只处理最外层) ──
        if tag == 'table':
            self.table_depth += 1
            if self.table_depth == 1:
                self.in_table = True
                self.table_headers = []
                self.table_rows = []
                self.current_row = []
                self.row_is_header = True
            return

        if tag == 'thead' and self.in_table and self.table_depth == 1:
            self.in_thead = True
            self.row_is_header = True
        elif tag == 'tbody' and self.in_table and self.table_depth == 1:
            self.in_thead = False
            self.row_is_header = False
        elif tag == 'tr' and self.in_table and self.table_depth == 1:
            self.in_tr = True
            self.current_row = []
        elif tag == 'th' and self.in_table and self.table_depth == 1:
            self.in_th = True
            self.current_cell = ''
        elif tag == 'td' and self.in_table and self.table_depth == 1:
            self.in_td = True
            self.current_cell = ''

    def handle_data(self, data: str):
        text = self._unescape(data)
        if not text.strip():
            return
        if self.capturing_heading:
            self.current_text += text
        if (self.in_th or self.in_td) and self.in_table and self.table_depth == 1:
            self.current_cell += text

    def handle_endtag(self, tag: str):
        tag = tag.lower()

        # ── 标题结束 ──
        if tag in ('h2', 'h3', 'p') and self.capturing_heading:
            self.capturing_heading = False
            self._process_heading(self.heading_size, self.current_text.strip())
            self.current_text = ''
            self.pending_p_void = False
            self.seen_void_p = False
            return

        if tag == 'font' and self.font_stack:
            self.font_stack.pop()
            if self.capturing_heading:
                self.capturing_heading = False
                self._process_heading(self.heading_size, self.current_text.strip())
                self.current_text = ''

        # ── 表格结束 ──
        if tag == 'table':
            if self.table_depth > 0:
                self.table_depth -= 1
            if self.table_depth == 0 and self.in_table:
                self.in_table = False
                self.in_thead = False
                self._process_table()
            return

        if tag == 'th' and self.in_table and self.table_depth == 1:
            self.in_th = False
            self.current_row.append(self.current_cell.strip())
            self.current_cell = ''
        elif tag == 'td' and self.in_table and self.table_depth == 1:
            self.in_td = False
            self.current_row.append(self.current_cell.strip())
            self.current_cell = ''
        elif tag == 'tr' and self.in_table and self.table_depth == 1:
            self.in_tr = False
            if self.current_row:
                if self.row_is_header and not self.table_headers:
                    self.table_headers = self.current_row
                else:
                    self.table_rows.append(self.current_row)
                self.current_row = []

    # ─── 内部处理逻辑 ──────────────────────────────────────────

    def _end_heading_if_open(self):
        if self.capturing_heading and self.current_text.strip():
            # 如果是 void-p 捕获（<p />text<p />），不要在这里处理
            # 等第二个 <p /> 来处理
            if not self.seen_void_p:
                self._process_heading(self.heading_size, self.current_text.strip())
        self.capturing_heading = False
        self.current_text = ''
        self.seen_void_p = False
        self.pending_p_void = False

    def _match_heading_keywords(self, text_lower: str):
        """匹配标题关键词，返回 (field, subsection) 或 None。"""
        for kw, (field, sub) in H3_KEYWORDS.items():
            if kw in text_lower:
                return (field, sub)
        return None

    def _process_heading(self, size: str, text: str):
        if not text:
            return

        text_lower = text.lower().strip()

        # h2 标题 — 识别主章节
        if size == 'h2':
            self.found_first_h2 = True
            for kw, section in H2_KEYWORDS.items():
                if kw in text_lower:
                    self.current_h2_section = section
                    self.current_field = None
                    return
            return

        # h3 或 p 标题 — 识别子章节/数据字段
        if size in ('h3', 'p'):
            matched = self._match_heading_keywords(text_lower)
            if matched:
                self.current_field = matched
            return

        # font size 标题 (经典风格)
        if size == '5':
            self.found_first_h2 = True
        if size in ('5', '4', '2'):
            matched = self._match_heading_keywords(text_lower)
            if matched:
                self.current_field = matched

    def _process_table(self):
        if not self.table_headers and not self.table_rows:
            return
        if not self.table_headers and self.table_rows:
            self.table_headers = self.table_rows[0]
            self.table_rows = self.table_rows[1:]

        tables_data = {
            'headers': self.table_headers[:],
            'rows': [row[:] for row in self.table_rows]
        }

        if self.current_field is None:
            self.awr_data['extra_sections'].append({
                'section': self.current_h2_section,
                'headers': tables_data['headers'],
                'rows': tables_data['rows'],
                'is_metadata_area': not self.found_first_h2,
            })
            return

        field, subsection = self.current_field
        data = self.awr_data

        if field in ('top_sql', 'segment_stats'):
            if subsection and subsection in data[field]:
                data[field][subsection].append(tables_data)
        elif field in ('fg_wait_events', 'bg_wait_events', 'service_stats',
                        'process_types', 'undo_stats', 'latch_stats',
                        'memory_stats', 'ash', 'advisory', 'wait_stats',
                        'instance_activity', 'file_io', 'sga_memory', 'addm'):
            existing = data.get(field, [])
            if isinstance(existing, dict):
                key = subsection or f"table_{len(existing)}"
                if key not in existing:
                    existing[key] = []
                existing[key].append(tables_data)
            elif isinstance(existing, list):
                existing.append(tables_data)
        else:
            existing = data.get(field, [])
            if isinstance(existing, list):
                existing.append(tables_data)

    # ─── 元数据提取（解析完成后调用） ───────────────────────────

    def _extract_metadata_from_extra(self):
        """从 extra_sections 中提取元数据（AWR 报告顶部的 DB/Instance/Snap 表格）"""
        meta = self.awr_data['metadata']
        to_remove = []

        for idx, item in enumerate(self.awr_data['extra_sections']):
            if not item.get('is_metadata_area', False):
                continue  # 跳过非元数据区域，不中断后续扫描

            headers = item.get('headers', [])
            rows = item.get('rows', [])
            if not rows:
                continue

            # 构建所有单元格的扁平搜索，用于更灵活地识别表格
            all_cells = []
            for row in rows:
                for cell in row:
                    if cell and cell.strip():
                        all_cells.append(cell.strip())

            first_row = rows[0]
            first_cols = [c.strip() for c in first_row]
            hdr_labels = [h.strip() for h in headers] if headers else []

            # DB Name table — 通过 headers 列标签识别
            if 'DB Name' in hdr_labels:
                hdr_map = {h.strip(): i for i, h in enumerate(headers)}
                for row in rows:
                    for col_name, key in [
                        ('DB Name', 'db_name'), ('Unique Name', 'unique_name'),
                        ('Role', 'role'), ('Edition', 'edition'),
                        ('Release', 'db_version'), ('RAC', 'rac'), ('CDB', 'cdb'),
                    ]:
                        if col_name in hdr_map and key not in meta:
                            idx_col = hdr_map[col_name]
                            if idx_col < len(row):
                                meta[key] = row[idx_col].strip()
                to_remove.append(idx)
                continue

            # Instance table — 通过 headers 列标签识别
            if 'Instance' in hdr_labels and 'Startup Time' in hdr_labels:
                hdr_map = {h.strip(): i for i, h in enumerate(headers)}
                for row in rows:
                    for col_name, key in [('Instance', 'instance'), ('Inst Num', 'inst_num'),
                                           ('Startup Time', 'startup_time')]:
                        if col_name in hdr_map and key not in meta:
                            idx_col = hdr_map[col_name]
                            if idx_col < len(row):
                                meta[key] = row[idx_col].strip()
                to_remove.append(idx)
                continue

            # Host table — 通过 headers 列标签识别
            if 'Host Name' in hdr_labels:
                hdr_map = {h.strip(): i for i, h in enumerate(headers)}
                for row in rows:
                    for col_name, key in [('Host Name', 'hostname'), ('Platform', 'platform'),
                                           ('CPUs', 'cpus'), ('Memory (GB)', 'memory_gb')]:
                        if col_name in hdr_map and key not in meta:
                            idx_col = hdr_map[col_name]
                            if idx_col < len(row):
                                meta[key] = row[idx_col].strip()
                to_remove.append(idx)
                continue

            # Snap table — 更灵活的检测：在任何单元格中出现关键标识
            has_snap_indicators = any(kw in ' '.join(all_cells).upper() for kw in [
                'BEGIN SNAP', 'END SNAP', 'SNAP ID', 'SNAP TIME', 'ELAPSED'
            ])
            if has_snap_indicators or 'Snap Id' in first_cols or 'Snap Time' in first_cols or 'Snap ID' in first_cols:
                # 尝试多种表结构来提取 Snap 信息
                all_text = ' '.join(all_cells)

                # 方法1: 逐行扫描，查找包含关键标签的行
                for row in rows:
                    row_text = ' '.join(row).upper()
                    row_label = row[0].strip() if row else ''
                    row_label_upper = row_label.upper()

                    # 查找 "Begin Snap" / "END SNAP" / "BEGIN SNAP" 等
                    if ('BEGIN SNAP' in row_label_upper or 'END SNAP' in row_label_upper
                            or 'BEGIN' in row_label_upper or 'END' in row_label_upper):
                        # 扫描此行所有单元格找数字快照ID
                        for cell in row:
                            cell_stripped = cell.strip()
                            if cell_stripped.isdigit() and len(cell_stripped) > 3:
                                if 'BEGIN' in row_label_upper and 'snap_begin' not in meta:
                                    meta['snap_begin'] = cell_stripped
                                elif 'END' in row_label_upper and 'snap_end' not in meta:
                                    meta['snap_end'] = cell_stripped
                                break

                    if 'ELAPSED' in row_label_upper and 'elapsed' not in meta:
                        # Elapsed 可能在不同列
                        for cell in row[1:]:
                            val = cell.strip()
                            if val and not val.isdigit():
                                meta['elapsed'] = val
                                break

                    if 'DB TIME' in row_label_upper and 'db_time' not in meta:
                        for cell in row[1:]:
                            val = cell.strip()
                            if val:
                                meta['db_time'] = val
                                break

                # 方法2: 如果方法1没找到，尝试在所有单元格中搜索
                if 'snap_begin' not in meta or 'snap_end' not in meta:
                    text_upper = ' '.join(all_cells).upper()
                    # 查找 "BEGIN SNAP" 后面的数字
                    import re
                    begin_match = re.search(r'BEGIN[\s_]*SNAP.*?(\d+)', text_upper)
                    if begin_match and 'snap_begin' not in meta:
                        meta['snap_begin'] = begin_match.group(1)
                    end_match = re.search(r'END[\s_]*SNAP.*?(\d+)', text_upper)
                    if end_match and 'snap_end' not in meta:
                        meta['snap_end'] = end_match.group(1)

                if 'snap_begin' in meta and 'snap_end' in meta:
                    meta['snap_range'] = f"{meta['snap_begin']} - {meta['snap_end']}"
                to_remove.append(idx)
                continue

        for idx in reversed(to_remove):
            self.awr_data['extra_sections'].pop(idx)

    # ─── 公开接口 ──────────────────────────────────────────────

    def parse_file(self, filepath: str) -> Dict[str, Any]:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        self.feed(content)
        self._extract_metadata_from_extra()
        return self.awr_data

    def parse_string(self, html_content: str) -> Dict[str, Any]:
        self.feed(html_content)
        self._extract_metadata_from_extra()
        return self.awr_data


def parse_awr_report(filepath: str) -> Dict[str, Any]:
    parser = AWRParser()
    return parser.parse_file(filepath)


if __name__ == '__main__':
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python awr_parser.py <awr_report.html>")
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    print(f"Parsing AWR report: {filepath}")
    result = parse_awr_report(filepath)

    meta = result.get('metadata', {})
    print(f"\n=== AWR Report Metadata ===")
    print(f"DB Name: {meta.get('db_name', 'N/A')}")
    print(f"Instance: {meta.get('instance', 'N/A')}")
    print(f"Snap Range: {meta.get('snap_range', 'N/A')}")
    print(f"Elapsed: {meta.get('elapsed', 'N/A')}")
    print(f"DB Version: {meta.get('db_version', 'N/A')}")
    print(f"RAC: {meta.get('rac', 'N/A')}")
    print(f"Hostname: {meta.get('hostname', 'N/A')}")

    print(f"\n=== Data per section ===")
    for key, value in result.items():
        if key in ('metadata', 'extra_sections'):
            continue
        total_rows = 0
        if isinstance(value, dict):
            for v in value.values():
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and 'rows' in item:
                            total_rows += len(item['rows'])
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and 'rows' in item:
                    total_rows += len(item['rows'])
        if total_rows > 0:
            print(f"  {key}: {total_rows} rows")
        elif value:
            print(f"  {key}: (non-table data)")

    extra = result.get('extra_sections', [])
    if extra:
        print(f"\n  extra_sections: {len(extra)} unrecognised tables")
        for e in extra[:15]:
            hdr = e.get('headers', [None])[0] if e.get('headers') else '?'
            print(f"    [{e.get('section','?')}] {len(e.get('rows',[]))} rows: {hdr}")

    output_file = filepath.replace('.html', '.parsed.json').replace('.htm', '.parsed.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nParsed data saved to: {output_file}")
