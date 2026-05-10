# -*- coding: utf-8 -*-
#
# Copyright (c) 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# This file is part of DBCheck, an open-source database health inspection tool.
# DBCheck is released under the MIT License with Attribution Requirements.
# See LICENSE for full license text.
#

"""
DBCheck PDF 导出模块
====================
提供将 Word 巡检报告转换为 PDF 的能力。

支持两种转换方式：
1. LibreOffice headless（推荐，需要安装 LibreOffice）
2. python-docx2pdf（Windows 专用）
"""

import os
import sys
import subprocess
import shutil
import tempfile


def convert_docx_to_pdf(input_path, output_path=None, method='auto'):
    """
    将 DOCX 文件转换为 PDF。
    
    参数:
        input_path: DOCX 文件路径
        output_path: PDF 输出路径（可选，默认与输入文件同名，扩展名改为 .pdf）
        method: 转换方法
            - 'auto': 自动选择可用方法
            - 'libreoffice': 强制使用 LibreOffice
            - 'docx2pdf': 强制使用 docx2pdf（Windows）
    
    返回:
        (成功标志, 输出文件路径或错误信息)
    """
    if not os.path.exists(input_path):
        return False, f"输入文件不存在: {input_path}"
    
    if not input_path.lower().endswith('.docx'):
        return False, "输入文件必须是 .docx 格式"
    
    # 确定输出路径
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + '.pdf'
    
    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    
    # 方法1: LibreOffice headless
    if method in ('auto', 'libreoffice'):
        result = _convert_with_libreoffice(input_path, output_path)
        if result[0]:
            return result
    
    # 方法2: docx2pdf (Windows)
    if method in ('auto', 'docx2pdf'):
        if sys.platform == 'win32':
            result = _convert_with_docx2pdf(input_path, output_path)
            if result[0]:
                return result
    
    return False, "无法转换 PDF：未找到可用的转换工具。请安装 LibreOffice 或在 Windows 上安装 docx2pdf。"


def _find_libreoffice():
    """查找 LibreOffice 可执行文件路径"""
    # 常见安装位置
    possible_paths = []
    
    if sys.platform == 'win32':
        # Windows 常见路径
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
        
        possible_paths.extend([
            os.path.join(program_files, 'LibreOffice', 'program', 'soffice.exe'),
            os.path.join(program_files, 'LibreOffice', 'program', 'soffice'),
            os.path.join(program_files_x86, 'LibreOffice', 'program', 'soffice.exe'),
            'soffice.exe',  # PATH 中
            'soffice',      # PATH 中
        ])
    else:
        # Linux/macOS
        possible_paths.extend([
            '/usr/bin/soffice',
            '/usr/bin/libreoffice',
            '/opt/libreoffice/program/soffice',
            '/Applications/LibreOffice.app/Contents/MacOS/soffice',
            'soffice',
        ])
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
        # 检查 PATH 中的命令
        if os.path.basename(path) == path:
            try:
                result = subprocess.run(['where' if sys.platform == 'win32' else 'which', path],
                                       capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split('\n')[0]
            except Exception:
                pass
    
    return None


def _convert_with_libreoffice(input_path, output_path):
    """使用 LibreOffice headless 转换为 PDF"""
    soffice = _find_libreoffice()
    if not soffice:
        return False, "未找到 LibreOffice"
    
    try:
        # 创建临时目录用于输出
        output_dir = os.path.dirname(output_path) or os.getcwd()
        
        # LibreOffice headless 转换命令
        cmd = [
            soffice,
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', output_dir,
            input_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # 2分钟超时
            cwd=output_dir
        )
        
        if result.returncode != 0:
            return False, f"LibreOffice 转换失败: {result.stderr}"
        
        # LibreOffice 会在同目录生成同名 PDF
        expected_pdf = os.path.join(output_dir, os.path.splitext(os.path.basename(input_path))[0] + '.pdf')
        
        if os.path.exists(expected_pdf):
            # 如果输出路径不同，移动文件
            if expected_pdf != output_path:
                shutil.move(expected_pdf, output_path)
            return True, output_path
        
        # 检查是否有其他位置的 PDF 输出
        for f in os.listdir(output_dir):
            if f.endswith('.pdf') and os.path.getmtime(os.path.join(output_dir, f)) > os.path.getmtime(input_path):
                pdf_path = os.path.join(output_dir, f)
                if pdf_path != output_path:
                    shutil.move(pdf_path, output_path)
                return True, output_path
        
        return False, "LibreOffice 转换完成但未找到输出文件"
        
    except subprocess.TimeoutExpired:
        return False, "LibreOffice 转换超时（>2分钟）"
    except Exception as e:
        return False, f"LibreOffice 转换异常: {str(e)}"


def _convert_with_docx2pdf(input_path, output_path):
    """使用 docx2pdf 转换为 PDF（仅 Windows）"""
    if sys.platform != 'win32':
        return False, "docx2pdf 仅支持 Windows"
    
    try:
        from docx2pdf import convert
        
        # docx2pdf 直接转换
        convert(input_path, output_path)
        
        if os.path.exists(output_path):
            return True, output_path
        else:
            return False, "docx2pdf 转换完成但未找到输出文件"
            
    except ImportError:
        return False, "未安装 docx2pdf，请执行: pip install docx2pdf"
    except Exception as e:
        return False, f"docx2pdf 转换异常: {str(e)}"


def get_pdf_converter_info():
    """
    获取当前系统可用的 PDF 转换方式信息。
    
    返回:
        dict: {
            'libreoffice': bool,  # 是否可用
            'libreoffice_path': str or None,
            'docx2pdf': bool,     # 是否可用
            'recommended': str,    # 推荐方法
        }
    """
    info = {
        'libreoffice': False,
        'libreoffice_path': None,
        'docx2pdf': False,
        'recommended': None,
    }
    
    # 检查 LibreOffice
    lo_path = _find_libreoffice()
    if lo_path:
        info['libreoffice'] = True
        info['libreoffice_path'] = lo_path
        info['recommended'] = 'libreoffice'
    
    # 检查 docx2pdf
    if sys.platform == 'win32':
        try:
            from docx2pdf import convert
            info['docx2pdf'] = True
            if not info['recommended']:
                info['recommended'] = 'docx2pdf'
        except ImportError:
            pass
    
    return info


# ═══════════════════════════════════════════════════════
#  报告生成增强：直接生成 PDF 格式报告
# ═══════════════════════════════════════════════════════

def generate_config_baseline_pdf_report(config_report, output_path, db_type='mysql'):
    """
    生成配置基线报告的 PDF 文件。
    
    参数:
        config_report: 配置基线报告字典
        output_path: 输出 PDF 路径
        db_type: 数据库类型
    
    返回:
        (成功标志, 文件路径或错误信息)
    """
    try:
        from reportlab.lib.pagesizes import A4, letter
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    except ImportError:
        return False, "未安装 reportlab，请执行: pip install reportlab"
    
    try:
        # 创建 PDF 文档
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )
        
        # 样式
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#1a5490')
        )
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10,
            spaceBefore=15,
            textColor=colors.HexColor('#2e7d32')
        )
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=6
        )
        
        # 构建内容
        story = []
        
        # 标题
        story.append(Paragraph(f"{db_type.upper()} 配置基线与合规检查报告", title_style))
        story.append(Spacer(1, 10))
        
        # 汇总信息
        story.append(Paragraph("检查汇总", heading_style))
        summary_data = [
            ['数据库规模', f"{config_report.get('db_size_gb', 0):.2f} GB"],
            ['每秒查询数 (QPS)', str(config_report.get('qps', 0))],
            ['主机总内存', f"{config_report.get('total_memory_gb', 0):.2f} GB"],
            ['严重问题', str(config_report['summary'].get('critical_count', 0))],
            ['警告问题', str(config_report['summary'].get('warning_count', 0))],
            ['提示信息', str(config_report['summary'].get('info_count', 0))],
        ]
        summary_table = Table(summary_data, colWidths=[5*cm, 5*cm])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e3f2fd')),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 20))
        
        # 配置项详情
        if config_report.get('items'):
            story.append(Paragraph("配置项详情", heading_style))
            
            # 表头
            table_data = [['配置项', '当前值', '推荐值', '差距', '状态']]
            
            for item in config_report['items']:
                severity_text = {
                    'critical': '🔴 严重',
                    'warning': '🟡 警告',
                    'info': '🟢 正常'
                }.get(item.get('severity', 'info'), '')
                
                table_data.append([
                    item.get('param', ''),
                    item.get('current', ''),
                    item.get('recommended', ''),
                    f"{item.get('gap_pct', 0):.1f}%",
                    severity_text
                ])
            
            col_widths = [5*cm, 3*cm, 3*cm, 2*cm, 2.5*cm]
            detail_table = Table(table_data, colWidths=col_widths)
            
            # 样式
            style_commands = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5490')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('PADDING', (0, 0), (-1, -1), 4),
            ]
            
            # 根据严重程度设置行颜色
            for i, item in enumerate(config_report['items'], start=1):
                severity = item.get('severity', 'info')
                if severity == 'critical':
                    style_commands.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#ffebee')))
                elif severity == 'warning':
                    style_commands.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#fff8e1')))
                else:
                    style_commands.append(('BACKGROUND', (0, i), (-1, i), colors.white))
            
            detail_table.setStyle(TableStyle(style_commands))
            story.append(detail_table)
        
        # 说明
        story.append(Spacer(1, 20))
        story.append(Paragraph("说明", heading_style))
        notes = [
            "🔴 严重: 配置差距 > 50%，建议立即调整",
            "🟡 警告: 配置差距 > 20%，建议尽快调整", 
            "🟢 正常: 配置合理或差距在可接受范围内"
        ]
        for note in notes:
            story.append(Paragraph(note, normal_style))
        
        # 生成 PDF
        doc.build(story)
        return True, output_path
        
    except Exception as e:
        return False, f"生成 PDF 报告失败: {str(e)}"


def generate_index_health_pdf_report(index_report, output_path, db_type='mysql'):
    """
    生成索引健康分析报告的 PDF 文件。
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError:
        return False, "未安装 reportlab，请执行: pip install reportlab"
    
    try:
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm
        )
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            spaceAfter=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor('#1a5490')
        )
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            spaceAfter=10,
            spaceBefore=15,
            textColor=colors.HexColor('#2e7d32')
        )
        
        story = []
        
        # 标题
        story.append(Paragraph(f"{db_type.upper()} 索引健康分析报告", title_style))
        story.append(Spacer(1, 10))
        
        # 汇总信息
        story.append(Paragraph("索引统计", heading_style))
        summary_data = [
            ['总索引数', str(index_report['summary'].get('total_indexes', 0))],
            ['缺失索引', str(index_report['summary'].get('missing_count', 0))],
            ['冗余索引', str(index_report['summary'].get('redundant_count', 0))],
            ['未使用索引', str(index_report['summary'].get('unused_count', 0))],
            ['数据库大小', f"{index_report['summary'].get('db_size_gb', 0):.2f} GB"],
        ]
        summary_table = Table(summary_data, colWidths=[5*cm, 5*cm])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#e8f5e9')),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 15))
        
        # 缺失索引
        if index_report.get('missing_indexes'):
            story.append(Paragraph("缺失索引", heading_style))
            table_data = [['表', '列', 'SELECT次数', '建议']]
            for idx in index_report['missing_indexes'][:20]:
                table_data.append([
                    f"{idx.get('table_schema', '')}.{idx.get('table_name', '')}",
                    idx.get('column_name', ''),
                    str(idx.get('select_count', 'N/A')),
                    idx.get('recommendation', '')[:50]
                ])
            
            idx_table = Table(table_data, colWidths=[4*cm, 3*cm, 2.5*cm, 5.5*cm])
            idx_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff9800')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('PADDING', (0, 0), (-1, -1), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fff3e0')),
            ]))
            story.append(idx_table)
            story.append(Spacer(1, 10))
        
        # 冗余索引
        if index_report.get('redundant_indexes'):
            story.append(Paragraph("冗余索引", heading_style))
            table_data = [['表', '索引1', '索引2', '原因']]
            for idx in index_report['redundant_indexes'][:20]:
                table_data.append([
                    f"{idx.get('table_schema', '')}.{idx.get('table_name', '')}",
                    idx.get('index1', ''),
                    idx.get('index2', ''),
                    idx.get('reason', '')[:40]
                ])
            
            idx_table = Table(table_data, colWidths=[4*cm, 3*cm, 3*cm, 5*cm])
            idx_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e91e63')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('PADDING', (0, 0), (-1, -1), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fce4ec')),
            ]))
            story.append(idx_table)
            story.append(Spacer(1, 10))
        
        # 未使用索引
        if index_report.get('unused_indexes'):
            story.append(Paragraph("未使用索引", heading_style))
            table_data = [['表', '索引', '最后使用', '建议']]
            for idx in index_report['unused_indexes'][:20]:
                table_data.append([
                    f"{idx.get('table_schema', '')}.{idx.get('table_name', '')}",
                    idx.get('index_name', ''),
                    idx.get('last_used', '未知'),
                    idx.get('recommendation', '')[:40]
                ])
            
            idx_table = Table(table_data, colWidths=[4*cm, 3*cm, 3*cm, 5*cm])
            idx_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#9c27b0')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('PADDING', (0, 0), (-1, -1), 4),
                ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f3e5f5')),
            ]))
            story.append(idx_table)
        
        # 生成 PDF
        doc.build(story)
        return True, output_path
        
    except Exception as e:
        return False, f"生成 PDF 报告失败: {str(e)}"