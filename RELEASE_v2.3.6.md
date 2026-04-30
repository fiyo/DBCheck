# DBCheck v2.3.6 - Release Notes

## New Features

### Slow Query Deep Analysis
- Added `slow_query_analyzer.py` core module, supporting slow query deep analysis for all database types
- Supported databases: MySQL, PostgreSQL, Oracle, DM8, SQL Server, TiDB
- Slow query analysis is automatically executed after AI diagnostics in the `checkdb()` flow
- Added new risk rules in `analyzer.py` (MySQL Rule 17+, PG Rule 11+)

### Database History Metrics Storage Upgrade
- Migrated history data from `history.json` to SQLite (`db_history.py` with `SQLiteHistoryManager`)
- Improved query performance, supporting larger-scale historical data
- Old `history.json` file can be safely deleted (no longer read)

---

## Improvements

### Report Style Optimization

#### TiDB Inspection Report (main_tidb.py)
- Tables in Chapters 1-6 now auto-fit to 100% width (`autofit = True`)
- Unified table header style: blue background (`#336699`) + white bold text + centered alignment
- Added `_style_header(table)` function, consistently applied to all 6 tables
- Fixed issue where `_add_config_table` called a non-existent `_set_table_header` method

#### DM8 Inspection Report (main_dm.py)
- Chapter 17 format standardized: `## Key Focus`, `## Optimization Suggestions`, `## Overall Evaluation` are now Level-2 headings with auto-numbered suffixes (17.1 / 17.2 / 17.3)
- List items (`- Problem`, `- Root Cause`, `- Fix`) now have the `-` prefix removed and text bolded
- Markdown bold (`**Wait Event Top 5**`) now correctly renders as bold in Word

### i18n Internationalization Fixes
- Fixed `col3` / `fix_sql` translation mapping (full-width Chinese punctuation matching)
- Fixed missing `_t` method in MySQL / PG `WordTemplateGenerator` classes
- Fixed missing `_t` method in `main_pg.py` `getData` class
- Fixed f-string exception parsing bug (`f"message: {e}"` errors when exception message contains `{}`; changed to `%s` format)

### Packaging Configuration (dbcheck.spec)
- Added `i18n/` directory to `datas` (previously only in `hiddenimports`, causing missing translation files at runtime)
- Added to `hiddenimports`: `pdf_export`, `index_health`, `config_baseline`
- Deleted obsolete `main.spec` (hardcoded Linux `project_root`, MySQL-only, severely outdated)

---

## Bug Fixes

### TiDB (main_tidb.py)
- Fixed broken indentation when globally replacing `table.autofit = True` (12-space indent used instead of 8-space, affecting 14 locations from lines 917-1395)
- Fix synced to `skill/dbcheck/scripts/main_tidb.py`

### DM8 (main_dm.py)
- Fixed SyntaxError: `unterminated string literal` (newline character written into string at line 1442)

### f-string Exception Parsing
- Changed all `f"message: {e}"` to `%s` format, fixing errors when exception messages contain `{}`
- Affected files: `analyzer.py`, `main_mysql.py`, `main_pg.py`, etc.

---

## Others

- Added `.gitattributes` to unify line endings as LF, eliminating diff noise caused by Windows/Linux mixed usage
- Synced latest changes to `skill/dbcheck/scripts/`: `version.py`, `desensitize.py`
- `web_ui.py` Flask app SSE push for task progress (existing feature, no changes in this release)
- AI diagnostics continue to support local Ollama only (`localhost:11434`), complying with security requirements

---

## Upgrade Notes

1. **Delete old files**: `history.json` in the project root can be safely deleted (replaced by SQLite)
2. **Regenerate Word template**: If report styles are not applied correctly, delete `templates/wordtemplates_v2.0.docx` to force regeneration
3. **PyInstaller packaging**: Please use `dbcheck.spec` (do NOT use the now-deleted `main.spec`)

---

*DBCheck Team | 2026-04-30*
