# DBCheck - Database Inspection Tool

>DBCheck is an automated database health inspection tool that supports **MySQL**, **PostgreSQL**, **Oracle**, and **Dameng DM8**. It generates standardized Microsoft Word inspection reports, helping DBAs and operations staff quickly assess database health status and identify potential risks.


[![Version](https://img.shields.io/badge/version-2.3.3-blue.svg)]()
[![MySQL](https://img.shields.io/badge/database-MySQL-blue.svg)]()
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-gray.svg)]()
[![Oracle](https://img.shields.io/badge/Oracle-red.svg)]()
[![DM](https://img.shields.io/badge/DM-yellow.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

> Language: [English](./README_en.md) | [中文](./README.md)

## 🌍 Multi-Language Support

DBCheck supports **Chinese (default)** and **English**. All interface text updates automatically when you switch languages.

### CLI Language Switch

```bash
python main.py                    # Default: Chinese
python main.py --lang en         # Switch to English
python main.py --lang zh         # Switch to Chinese (explicit)
```

> The Web UI also has a 🌐 toggle button in the top-right corner. Clicking it switches between Chinese and English. The setting is automatically saved and will be loaded on the next Web UI startup.

### Language Reference

| Parameter | Language | Notes |
|-----------|----------|-------|
| `--lang zh` | Chinese | Default language |
| `--lang en` | English | English interface |
| (not specified) | Chinese | Uses the last saved language; falls back to Chinese if no record exists |

> **Note**: The `--lang` parameter only takes effect for the current session and does not overwrite any saved language setting. Switching language in the Web UI persists to `dbc_config.json` and loads automatically on the next startup.

## AI-Assisted - Detect and Resolve Issues

### AI-Powered Intelligent Diagnosis

Leveraging a fully offline, local **Ollama** deployment, DBCheck analyzes inspection metrics (connection counts, cache hit ratios, slow queries, security risks, etc.) and automatically generates structured optimization recommendations. AI insights are rendered as a dedicated chapter in the report — Markdown content is automatically styled into Word format (bold, code blocks, lists, numbered headings), ready to share with your team or leadership.

| Backend | Characteristics | Use Case |
|---------|----------------|----------|
| `ollama` | Local-only, zero cost, no network dependency | Air-gapped environments, high data-security requirements |
| `disabled` | AI disabled (default) | Offline environments / AI not required |

> **Security Notice**: AI diagnosis is strictly limited to local Ollama (localhost:11434). Inspection data is never sent to any third-party service. This is enforced at the code level — even if the configuration file is tampered with to use a remote address, the system will automatically fall back to the disabled state.

### Risk Detection and Recommendations

Each risk is presented as a card: **Risk Level (High/Medium/Low) → Issue Description → Remediation SQL (copy-paste ready) → Priority & Owner**. The report automatically aggregates all findings so you can see every pending item at a glance.

| Dimension | MySQL | PostgreSQL | Oracle | DM8 |
|-----------|:-----:|:----------:|:------:|:---:|
| Connection Resources | ✅ | ✅ | ✅ | ✅ |
| Cache Performance | ✅ | ✅ | ✅ | ✅ |
| Query Efficiency | ✅ | ✅ | ✅ | ✅ |
| Logs and Alerts | ✅ | ✅ | ✅ | ✅ |
| Security Audit | ✅ | ✅ | ✅ | ✅ |
| Replication / DG | ✅ | ✅ | — | — |
| Configuration Tuning | ✅ | ✅ | ✅ | ✅ |
| Tablespaces | — | — | ✅ | ✅ |
| SGA / PGA Memory | — | — | ✅ | ✅ |
| Redo Logs | — | — | ✅ | ✅ |
| Backup and Archiving | — | — | ✅ | ✅ |
| RAC Cluster | — | — | ✅ | — |
| ASM Disk Groups | — | — | ✅ | — |
| Undo Management | — | — | ✅ | ✅ |
| Data Guard | — | — | ✅ | — |
| DM8-Specific Views | — | — | — | ✅ |

---

## Four Core Capabilities

| Capability | Description |
|-----------|-------------|
| 📊 Historical Trend Analysis | Automatically aggregates data from multiple inspection runs on the same database, generates metric trend line charts, and compares against previous results to surface changes |
| 🤖 AI-Powered Diagnosis | Calls local Ollama based on inspection metrics to generate personalized optimization recommendations |
| 🔍 80+ Enhanced Rules | Full-dimensional risk detection across all four databases (MySQL 18+, PG 16+, Oracle 20+, DM8 16+) |
| 🦞 OpenClaw Skill | Trigger a full inspection with one natural-language command — zero manual操作 required |

---

## Five Ways to Use DBCheck

| Method | Description |
|--------|-------------|
| 🖥️ Command-Line | `python main.py` — terminal interaction, ideal for CLI-familiar users |
| 🌐 Web UI | `python web_ui.py` — browser-based GUI with trend charts and AI configuration |
| 🤖 OpenClaw Skill | Tell your AI assistant "inspect the Oracle Database" — fully automated |
| 📦 Packaged Distribution | PyInstaller bundles everything into a single executable for team distribution |

---

## Features

### Database Inspection

| Dimension | MySQL | PostgreSQL | Oracle | DM8 |
|-----------|:-----:|:----------:|:------:|:---:|
| Basic Info (version / instance / database) | ✅ | ✅ | ✅ | ✅ |
| Session and Connection Status | ✅ | ✅ | ✅ | ✅ |
| Memory and Cache Configuration | ✅ | ✅ | ✅ | ✅ |
| Tablespace Usage | — | — | ✅ | ✅ |
| SGA / PGA Memory Analysis | — | — | ✅ | ✅ |
| Redo Log Status | — | — | ✅ | ✅ |
| Archiving and Backup Checks | — | — | ✅ | ✅ |
| Key Parameter Configuration | ✅ | ✅ | ✅ | ✅ |
| Invalid Object Detection | ✅ | ✅ | ✅ | ✅ |
| User Security Audit | ✅ | ✅ | ✅ | ✅ |
| Top SQL / Slow Queries | ✅ | ✅ | ✅ | ✅ |
| Master-Slave Replication / Data Guard | ✅ | ✅ | — | — |
| RAC Cluster Information | — | — | ✅ | — |
| ASM Disk Groups | — | — | ✅ | — |
| Undo Tablespace Management | — | — | ✅ | ✅ |
| Recycle Bin / Flashback Recovery Area | — | — | ✅ | ✅ |
| Profile Password Policy | — | — | ✅ | — |
| Top Wait Events | — | — | ✅ | ✅ |
| Stale Statistics Detection | — | — | ✅ | ✅ |
| Partitioned Table Information | — | — | ✅ | ✅ |
| Datafile Status | — | — | ✅ | ✅ |
| DM8 Buffer Pool Details | — | — | — | ✅ |

### System Resource Monitoring

- **CPU**: utilization, core count, clock speed
- **Memory**: total, used, available, utilization rate
- **Disk**: capacity and utilization per mount point
- **Collection**: local collection or SSH remote collection (password/key auth supported, default port 22); Dameng DM8 supports SSH collection with automatic fallback to local collector on failure

### Intelligent Risk Analysis

Automatically detects potential database risks — **each risk includes an executable remediation SQL** ready to copy and run:

#### MySQL (18+ rules)

| Dimension | Example Rules |
|-----------|--------------|
| Connections | Usage >90% = critical / >80% = warning |
| Memory | InnoDB buffer pool too small (< 60% of data size) |
| Disk | Usage >85% = warning / >95% = critical |
| Queries | Long-running SQL (>60s), slow query log disabled |
| Locks | High lock wait ratio |
| Security | Empty password users, root@% exposure, non-UTF8 charset |
| Replication | Master-slave lag >30s, replication errors |
| Other | binlog disabled, query cache remnants, excessive aborted connections |

#### PostgreSQL (16+ rules)

| Dimension | Example Rules |
|-----------|--------------|
| Connections | Near limit, too many superusers |
| Cache | Low hit ratio (<80%), undersized shared_buffers |
| Performance | Large accumulation of dead tuples, long-running SQL |
| Security | Overly permissive public schema permissions |
| Archiving | Archiving mode disabled |
| Other | Disk / memory / CPU resource alerts |

#### Oracle (20+ rules)

| Dimension | Example Rules |
|-----------|--------------|
| Tablespace | Usage >90% (including autoextend calculation) |
| TEMP | Temp tablespace usage too high |
| Sessions | Near limit / process overflow / lock blocking |
| Memory | SGA too large relative to physical memory |
| Redo | Redo log group issues / frequent switches |
| Backup | Archiving disabled / missing RMAN backups |
| DG | MRP not running / protection mode too low |
| ASM | Disk group space insufficient / offline disks |
| FRA | Flashback Recovery Area usage too high |
| Objects | Too many invalid objects / stale statistics |
| Security | Permissive Profile password policy / auditing disabled |
| Other | open_cursors too small / recycle bin bloat / datafiles offline |

#### DM8 (16+ rules)

| Dimension | Example Rules |
|-----------|--------------|
| Tablespace | Usage >90% (including autoextend calculation) |
| Memory | Pool misconfigurations (KEEP/RECYCLE/FAST/NORMAL/ROLL) |
| Sessions | Near connection limit / long-running sessions |
| Transactions | Blocking transaction detection / waits |
| Backup | Missing backup sets / backup timeouts |
| Parameters | Key parameters (INSTANCE_MODE, COMPATIBLE_VERSION, etc.) |
| Security | Empty passwords / overly broad permissions / auditing disabled |
| Objects | Invalid objects / stale statistics / partitioned table info |
| Archiving | Archiving disabled / log accumulation |

### Historical Trend Analysis

> Run multiple inspections on the same database, and DBCheck automatically aggregates the data to generate trend charts — spotting gradual changes before they become incidents.

- After each inspection, key metrics (memory utilization, connections, QPS, CPU, etc.) are written to local `history.json`
- Data is aggregated per database (IP + port + type), retaining up to 30 historical records
- The Web UI provides a **trend analysis page** with line charts and threshold lines
- Side-by-side comparison with the previous run: changes shown with colored arrows (↑ deteriorating / ↓ improving)

### AI-Powered Diagnosis

> Leveraging inspection data, DBCheck calls a local Ollama LLM to generate personalized optimization recommendations — evolving from "problem detection" to "problem resolution".

Comparison of Intelligent Analysis vs. AI Diagnosis:

| | Intelligent Analysis | AI Diagnosis |
|---|---|---|
| Principle | Fixed rules, deterministic offline judgment | Local LLM inference, personalized output |
| Speed | Milliseconds | Depends on model response time |
| Output | Deterministic conclusions + remediation SQL | Natural language recommendations, Markdown auto-rendered to Word |
| Invocation | Runs automatically on every inspection | On-demand (can be disabled) |

**AI Backend Configuration (Web UI Settings):**

| Parameter | Description |
|-----------|-------------|
| Backend Type | `ollama` or `disabled` |
| API Address | Default `http://localhost:11434` (localhost only) |
| Model Name | e.g. `qwen3:30b`, `qwen3:8b`, `llama3`, etc. |
| Timeout | Default 600 seconds (LLM cold start can be slow) |

> For security reasons, any non-localhost API address is automatically rejected by the code to prevent data leakage.

---

## Environment Requirements

- **Operating System**: Linux / macOS / Windows
- **Python**: 3.6+
- **General Dependencies**: pymysql, psycopg2-binary, python-docx, docxtpl, paramiko, psutil, openpyxl, pandas, flask, flask_socketio
- **Oracle Dependencies**: `oracledb` (recommended, pure Python, no Instant Client needed) or `cx_Oracle` (requires Oracle Instant Client)
- **DM8 Dependencies**: `dmpython` (pip install dmpython)
- **MySQL Privileges**: Read-only access to information_schema, performance_schema, and mysql databases
- **PostgreSQL Privileges**: Read-only access to pg_stat_* series views and pg_roles
- **Oracle Privileges**: Read-only access to v$* and dba_* views; SYSDBA privileged connections supported (Web UI checkbox for one-click enablement)
- **DM8 Privileges**: Read-only access to V$* system views and DBA_* admin views; default port 5236; connecting user equals Schema (no `database` parameter needed)
- **SSH (optional)**: Used for remote system resource collection (MySQL / PostgreSQL / Oracle / DM8); default port 22; DM8 SSH falls back to local collector on failure

### Installing Dependencies

```bash
pip install pymysql psycopg2-binary paramiko=4.0.0 openpyxl docxtpl python-docx pandas psutil flask oracledb dmpython flask_socketio
```

> DM8 Driver Notes:
> - `dmpython`: Pure Python driver provided by Dameng (pip install dmpython), recommended
> - Connection parameters: host + port (default 5236) + username (no `database` parameter — the user is the Schema)
> - DM8's V$ view column names differ significantly from Oracle; the tool includes targeted adaptations for DM8

> Oracle Driver Notes:
> - `oracledb`: Pure Python implementation, no Instant Client required, recommended
> - `cx_Oracle`: Requires downloading [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client.html) and configuring environment variables

---

## Quick Start

```bash
python main.py
```

The main menu offers seven options:

```
==================================================
  🗄️  Database Automation Inspector  v2.3  Main Menu
==================================================
    🐬  1 │ MySQL           MySQL Health Inspection & Report
    🐘  2 │ PostgreSQL      PostgreSQL Health Inspection & Report
    🔴  3 │ Oracle          Oracle Deep Health Inspection (20+ Checks)
    🟡  4 │ DM8             Dameng DM8 Health Inspection & Report
    📋  5 │ Batch Template  Generate Batch Inspection Excel Template
    🌐  6 │ Launch Web UI   Browser-based GUI
        7 │ Exit
==================================================
```

1. Enter **1–3** to enter the inspection menu for the corresponding database type
2. Enter **4** to select a template type to generate (MySQL / PostgreSQL / Oracle / DM8)
3. Enter **5** to launch the Web UI
4. Enter **6** to exit

#### Single Instance Inspection (Oracle as Example)

1. Select **3** to enter the Oracle inspection menu
2. Select **1** for single-instance inspection
3. Fill in as prompted:
   - Inspection name
   - Database IP / port (default 1521) / service name or SID
   - Username (SYSDBA supported — Web UI checkbox, CLI accepts `sys as sysdba` syntax) / password
   - SSH info (optional, default port 22, used for system resource collection)
4. The tool runs 42 SQL checks → collects system info → runs intelligent risk analysis → AI diagnosis (optional)
5. A Word inspection report is generated

#### Batch Inspection

1. Generate the corresponding Excel batch inspection template via option **4**
2. Fill in connection information for multiple database instances in the template
3. Select **2** for batch inspection — the program automatically runs through all instances

### Web UI

Start the web service and visit **http://localhost:5003** in your browser to perform all inspections via the GUI.

```bash
python web_ui.py
```

**Web UI Workflow:**

| Step | Function |
|:---:|---------|
| 1 | Select database type (🐬 MySQL / 🐘 PostgreSQL / 🔴 Oracle / 🟡 DM8) |
| 2 | Fill in connection info — Oracle requires service name/SID; DM8 does not need a database name |
| 3 | Online connection testing (SYSDBA privileged verification via checkbox) |
| 4 | Configure SSH for system resource collection (optional, default port 22; DM8 supports SSH with auto-fallback) |
| 5 | Inspector name (default: dbcheck) |
| 6 | Confirm and execute with one click — real-time log streaming (SSE) |
| 7 | Upon completion, preview intelligent analysis + AI diagnosis results online |
| 8 | 📊 Historical trend analysis: view metric trends across multiple inspection runs |
| 9 | 🤖 AI diagnosis settings: configure local Ollama parameters (address / model / timeout) |
| 10 | Download the Word report and browse historical reports |

### OpenClaw Skill

DBCheck is published as an OpenClaw Skill on [ClawHub](https://clawhub.ai/skills/dbcheck). Once installed in your AI assistant, you can trigger inspections via natural language — no CLI or Web UI needed.

#### Installation

Run in your OpenClaw client:

```bash
clawhub install dbcheck
```

#### Usage

After installation, simply tell your AI assistant what you need, for example:

> "Inspect the Oracle Database at IP localhost, username sys as sysdba"

The AI assistant will load the Skill, ask for missing information step by step (port, service name, inspector name, etc.), then invoke the inspection script to generate a Word report.

#### Supported Commands

| Example Command | Description |
|---------------|-------------|
| Help me inspect a MySQL Database | Single-instance MySQL inspection |
| Help me inspect a PostgreSQL Database | Single-instance PG inspection |
| Help me inspect an Oracle Database | Single-instance Oracle inspection |
| Inspect Oracle at localhost | Quick inspection targeting a specific IP |
| Generate a database inspection report | Trigger the full inspection workflow |

#### Skill File Structure

```
dbcheck/skill/dbcheck/
├── SKILL.md               # Skill documentation
├── security.md            # Security notes
└── scripts/
    ├── run_inspection.py       # Non-interactive entry point
    ├── main_mysql.py           # MySQL inspection logic
    ├── main_pg.py              # PostgreSQL inspection logic
    ├── main_oracle_full.py     # Oracle inspection logic (20+ checks)
    ├── main_dm.py              # Dameng DM8 inspection logic
    ├── analyzer.py             # Intelligent risk analysis engine
    └── main.py                 # Unified menu entry
```

> **Security Notice**: Skill credentials are used only to establish local connections and are never sent to any third party. AI diagnosis uses local Ollama exclusively.

---

## Packaging and Distribution

Use the PyInstaller configuration file `dbcheck.spec` to bundle everything into a single executable containing all dependencies, templates, and project modules:

```bash
cd D:\DBCheck

# Clean old build (Windows)
rd /s /q build dist __pycache__ 2>nul

# Package
pyinstaller dbcheck.spec
```

> On Linux/macOS, use `rm -rf build dist __pycache__` to clean.

Run the packaged version:

```bash
cd dist
dbcheck.exe         # Windows
./dbcheck           # Linux/macOS
```

Double-click to run the full-featured program with all database drivers, Word templates, and Web UI templates included — no Python environment installation required.

---

## Report Structure

The generated Word report contains the following chapters (Oracle inspection report example):

| Chapter | Content (Oracle Inspection) |
|---------|----------------------------|
| Cover | Database name, server address, version, hostname, uptime, inspector, platform, report timestamp |
| Chapter 1 | OS Host Information (CPU / Memory / Disk) |
| Chapter 2 | Database Basic Information (version / instance name / database name) |
| Chapter 3 | Tablespaces (permanent + temporary, including autoextend) |
| Chapter 4 | SGA / PGA Memory Analysis |
| Chapter 5 | Key Parameter Configuration |
| Chapter 6 | Undo Tablespace Management |
| Chapter 7 | Redo Logs |
| Chapter 8 | Archiving and Backup |
| Chapter 9 | Data Guard Status |
| Chapter 10 | RAC Cluster Information |
| Chapter 11 | ASM Disk Groups |
| Chapter 12 | Sessions and Connections (including Top 5 Wait Events) |
| Chapter 13 | Performance Metrics (including AWR snapshot analysis) |
| Chapter 14 | Alert Log Analysis |
| Chapter 15 | Users and Security |
| Chapter 16 | Invalid Objects and Statistics |
| Chapter 17 | Partitioned Table Information |
| Chapter 18 | FRA Flashback Recovery Area |
| Chapter 19 | Recycle Bin |
| Chapter 20 | Risks and Recommendations (intelligent analysis details + remediation SQL quick reference) |
| Chapter 21 | AI Diagnosis Recommendations (Markdown auto-rendered to Word with numbered headings, code blocks, lists) |
| Chapter 22 | Report Notes |

> Report structure varies slightly by database type, but all include the six core modules: cover, basic information, performance analysis, risk recommendations, AI diagnosis, and report notes.

---

## FAQ

### General

1. **Some content is empty or missing**
   When template rendering encounters compatibility issues, the program automatically switches to a fallback rendering mode and still produces a complete report with all key data — usage is unaffected.

2. **Connection failure**
   Verify that the database allows remote access, the user has sufficient privileges, and the firewall permits the relevant port.

3. **SSH collection failure**
   Confirm the SSH service is running (default port 22) and authentication credentials are correct. Some stripped-down Linux distributions lack commands like `lscpu`, causing CPU information to show "not obtained" — this is normal.

4. **AI diagnosis not working**
   - Confirm a valid configuration is saved in Web UI → AI Diagnosis Settings
   - Ensure Ollama is running: `ollama serve`
   - Ensure the model is downloaded: `ollama pull qwen3:30b` (larger models recommended; cold start is slow)

5. **Risk recommendations are for reference only**
   Built-in thresholds are based on general best practices. Evaluate them in the context of your actual workload.

### Oracle-Specific

6. **ORA-01017 invalid username/password**
   - For SYSDBA access: check the "SYSDBA" box in Web UI; in CLI, enter `sys as sysdba` (full format) — the tool automatically parses and uses the correct privileged mode
   - Verify the password is correct (case-sensitive)

7. **ORA-00904 / ORA-00942 invalid identifier**
   Some advanced views/columns may not exist in certain Oracle versions (e.g., 11g vs 19c). The tool handles compatibility gracefully; incompatible items are marked with ⚠️ and skipped without affecting the overall inspection.

8. **Do I need to install an Oracle client?**
   - Using `oracledb` driver (recommended): No — pure Python implementation
   - Using `cx_Oracle` driver: Yes — download [Oracle Instant Client](https://www.oracle.com/database/technologies/instant-client.html)

9. **Oracle version support**
   Supports **11g R2, 12c, 19c, 21c** and above. SQL templates are cross-version compatible.

### DM8-Specific

10. **Connection failure (returned a result with an exception set)**
    - dmPython uses lazy connections — a successful connection object creation does not mean the connection is actually established; a probe SQL must be executed via cursor to confirm
    - The tool includes built-in auto-probe logic. If it still fails, check: correct port (default 5236), correct user password, and whether the server allows access from your IP

11. **"Invalid column name" error**
    - DM8's V$ view column names differ significantly from Oracle; the tool has been adapted for DM8实测 column names. If errors persist, please send a screenshot so we can add support.

12. **SSH collection not available**
    - Limited by the Dameng server's OpenSSH version (port 2022), SSH collection is temporarily disabled. System resource information will use the local collector. Local and Dameng server information being inconsistent is expected.

13. **"Server hostname/platform" in the report shows local machine info**
    - A known limitation when SSH collection is disabled; Dameng server system info collection depends on the SSH channel and will be addressed in a future version.

---

## Screenshots

![Home](snapshot/webui0.png)

![Step 1: Select Database Type](snapshot/webui1.png)
*Fig. 1: Select database type (MySQL 🐬 / PostgreSQL 🐘 / Oracle 🔴 / DM8 🟡)*

![Step 2: Fill in Connection Info](snapshot/webui2.png)
*Fig. 2: Fill in database connection information*

![Step 3: Test Connection Online](snapshot/webui3.png)
*Fig. 3: Online connection testing*

![Step 4: SSH Configuration](snapshot/webui5.png)
*Fig. 4: SSH configuration (optional, default port 22)*

![Step 5: Inspector Name](snapshot/webui6.png)
*Fig. 5: Inspector name configuration (default: dbcheck)*

![Step 6: Confirm Inspection Info](snapshot/webui7.png)
*Fig. 6: Confirm inspection information*

![Step 7: Run Inspection](snapshot/webui8.png)
*Fig. 7: One-click inspection with real-time log streaming*

![Report Download](snapshot/webui9.png)
*Fig. 8: Download Word report directly after inspection*

![Historical Reports](snapshot/webui10.png)
*Fig. 9: Historical report list, browsable by name, size, and time*

![Historical Trend Analysis](snapshot/webui12.png)
*Fig. 10: Historical trend analysis*

![AI Diagnosis Configuration](snapshot/webui13.png)
*Fig. 11: AI diagnosis configuration — fully local, no API key needed, data never leaves your machine*

![ClawHub dbcheck Skill](snapshot/skill0.png)
*Fig. 12: dbcheck published on ClawHub*

![QClaw](snapshot/skill1.png)
*Fig. 13: Using dbcheck in QClaw and other OpenClaw-compatible applications*

![Reports](snapshot/report.png)
*Fig. 14: AI diagnosis report (Markdown auto-rendered to Word format)*

---

## Acknowledgments

Special thanks to [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) for their foundational work!

> DBCheck is an improvement on [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git), adding PostgreSQL, Oracle, and DM8 support on top of the original MySQL functionality.

Some features are still being actively developed and improved. Contributions and feedback are welcome!

---

## Support the Project

DBCheck has undergone extensive iteration and real-world testing to reach its current state. If this tool has been helpful to you, consider supporting the project's continued development:

<img src="snapshot/pay.png" alt="PayPal donation QR code" width="500" />

> When donating, please include your name or nickname so we know who supports us ❤️
