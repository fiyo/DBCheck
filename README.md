# RaccoonX

🚀 **Open Source Intelligent Database Inspection & Health Analysis Platform**

![RaccoonX Logo](snapshot/dbcheck_logo_info.png)


RaccoonX is an **Apache License 2.0 licensed open-source database inspection and health analysis platform**.

It helps Database Administrators (DBAs), developers, and operation teams automatically inspect databases, discover potential risks, analyze performance problems, and generate standardized health inspection reports.

RaccoonX supports multiple relational databases, document databases, and KV databases.

Through automated inspection rules, system resource collection, AI-assisted diagnosis, and extensible plugins, RaccoonX helps teams build a more reliable and efficient database operation process.


> Third-party software names, logos, trademarks, badges, and related assets displayed in this project belong to their respective owners. Their appearance only indicates compatibility or support, and does not imply any affiliation or partnership.


> Website: [https://dbcheck.top](https://dbcheck.top) &nbsp;|&nbsp; 
> 
> Email: sdfiyon@gmail.com
> 
语言切换（Language switch）: [English](./README.md) | [中文](./README_zh.md)

[![Version](https://img.shields.io/badge/Version-v26.9.6-blue.svg)]()
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)]()
[![Open Source](https://img.shields.io/badge/Open%20Source-Yes-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![AI](https://img.shields.io/badge/AI-Ollama+OpenAI-orange.svg)]()
[![RAG](https://img.shields.io/badge/RAG-Knowledge_Base-red.svg)]()
[![WebUI](https://img.shields.io/badge/WebUI-Flask-success.svg)]()
[![WeChat](https://img.shields.io/badge/WeChat-sdougwx-brightgreen?logo=WeChat)]()
[![WebSite](https://img.shields.io/badge/Website-www.dbcheck.top-green.svg)](https://dbcheck.top)
[![Docker Pulls](https://img.shields.io/docker/pulls/jackge12345/dbcheck?style=flat-square&label=Docker%20Pulls&cacheSeconds=300)](https://hub.docker.com/r/jackge12345/dbcheck)
![Downloads](https://img.shields.io/github/downloads/fiyo/DBCheck/total?style=flat-square&label=Source+Downloads)

---

## 💝 Support RaccoonX

If RaccoonX helps with your database work, please consider supporting its continued development. Every contribution keeps this open-source project alive ❤️

<img src="snapshot/pay-en.png" alt="Sponsor QR Code" width="600" />

> Scan with WeChat / Alipay to sponsor · Please specify your name or nickname when sponsoring ❤️
>
> See the [full supporter list](#community-supporters) at the bottom of this page.

## 🦝 Brand Story

This platform was **originally named DBCheck**. We have completed a brand upgrade to **RaccoonX** — its Chinese name is **浣巡 (Huàn Xún)**, which stands for *"Raccoon Inspection"*: an intelligent inspection and health analysis platform.

**Why a raccoon?**

- **Raccoon** has a natural instinct to *explore, rummage, and uncover hidden problems* — exactly what a database inspector does.
- Raccoons are **nocturnal**, which fits DBAs who fight fires and inspect servers late at night.
- Raccoons are **smart, curious, and tool-savvy**, matching the positioning of an AI operations assistant.
- A raccoon feels **more friendly and approachable than a robot**, which suits open-source community spreading.

**What does the X stand for?**

- **eXplore** — explore
- **eXpert** — expert
- **eXtensible** — extensible

> Note: During this transition, the project repository, Docker image, and website domains still use the original **DBCheck** identifiers, and the internal code name remains `dbcheck`.

---

# Why RaccoonX?


Modern applications depend heavily on databases.

However, database operation and maintenance still often rely on:

- Manual inspections
- Personal experience
- Scattered monitoring tools
- Temporary troubleshooting


RaccoonX aims to provide an open-source, intelligent, and extensible database inspection platform.

It helps teams:

✅ Discover database risks earlier  
✅ Standardize database health checks  
✅ Reduce repetitive DBA work  
✅ Improve troubleshooting efficiency  
✅ Preserve operational knowledge through AI and RAG  


---

# ✨ Features

## 🗄️ Multi Database Support

RaccoonX supports more than 20 database systems:

- MySQL
- MariaDB
- PostgreSQL
- Oracle
- SQL Server
- DM8
- TiDB
- OceanBase
- KingbaseES
- YashanDB
- GBase
- HighGo
- MongoDB
- DB2
- Redis
- ClickHouse
- And more...


## 📋 Automated Database Inspection

Automatically collects:

- Database information
- Configuration parameters
- Performance metrics
- Security settings
- Storage information
- Session status
- Lock information
- Slow SQL
- Replication status


Generates professional inspection reports:

- Word reports
- Risk analysis
- Optimization suggestions
- Historical comparison


## 🤖 AI Assisted Diagnosis


RaccoonX integrates AI capabilities to help analyze inspection results.

Supported AI modes:

| Mode | Description |
|------|-------------|
| Ollama | Fully local AI deployment |
| OpenAI compatible API | Cloud AI services |
| Disabled | Traditional inspection mode |


AI can assist with:

- Risk explanation
- Root cause analysis
- Optimization suggestions
- Operation recommendations


## 🔍 Performance Analysis


Built-in analysis capabilities:

- Slow SQL analysis
- Execution plan analysis
- Lock diagnostics
- Index health analysis
- Connection analysis
- Resource bottleneck detection


## 📊 Historical Trend Analysis


RaccoonX stores inspection history and provides:

- Trend charts
- Before/after comparison
- Risk evolution tracking
- Database health changes


## 🔌 Plugin Architecture


RaccoonX provides an extensible plugin system.

Plugins can independently manage:

- Inspection rules
- Templates
- Baselines
- Database adapters


Developers can extend RaccoonX by creating custom plugins.


## 🖥️ Server Health Inspection


Database problems are often related to infrastructure.

RaccoonX can inspect:

- CPU
- Memory
- Disk
- Network
- Processes
- System resources


## 🌐 Web Interface


RaccoonX provides a modern Web UI:

- Database management
- Inspection execution
- Report viewing
- AI diagnosis
- Configuration management
- Historical analysis


## 🐳 Docker Ready


RaccoonX provides official Docker images.

No complicated environment preparation required.

---

# Supported Databases

RaccoonX supports more than 20 database systems:

| Database | Driver | Default Port | Notes |
|----------|--------|:---:|-------|
| MySQL | pymysql | 3306 | 5.6 / 5.7 / 8.0+ |
| MariaDB | pymysql (MySQL protocol) | 3306 | 10.3+ |
| PostgreSQL | psycopg2 | 5432 | 10+ |
| Oracle | oracledb (pure Python, no client needed) | 1521 | 11g R2 / 12c / 19c / 21c+ |
| Oracle (JDBC) | JDBC (JPype1 + ojdbc) | 1521 | 11g / 12c / 19c / 21c+，Complete migration of Oracle 11g inspection template |
| SQL Server | pyodbc + ODBC Driver 17 | 1433 | 2012+ |
| DM8 (Dameng) | dmpython | 5236 | Chinese domestic DB |
| TiDB | pymysql (MySQL protocol) | 4000 | 6.5+ |
| IvorySQL | psycopg2 (PG protocol) | 5333 | PG + Oracle dual-compatible |
| YashanDB | yashandb | 1688 | Oracle-compatible, Chinese domestic DB |
| KingbaseES | psycopg2 (PG protocol) | 54321 | Chinese domestic DB |
| GBase 8s | JDBC (jaydebeapi + JDK) | 9088 | Chinese domestic DB |
| UXDB (YouXuan) | uxdb_jdbc (JDBC) | 33060 | Chinese domestic DB, PostgreSQL-compatible |
| HGDB (HighGo) | hgdb_jdbc (JDBC, PG protocol) | 5866 | Chinese domestic DB, PostgreSQL-compatible (V9 = PG 14.20) |
| MongoDB | pymongo | 27017 | 4.0+ |
| DB2 (LUW) | JDBC (JPype1 + db2jcc4) | 50000 | 11.5+ / 12.x (LUW) |
| OceanBase (MySQL tenant) | pymysql (MySQL protocol) | 2881 | 4.x+; MySQL-compatible; Oracle tenant reserved |
| TDSQL-C MySQL | pymysql (MySQL protocol) | 3306 | Tencent Cloud cloud-native MySQL-compatible (TDSQL-C) |
| Redis | redis-py | 6379 | KV cache, 3.0+ (ACL from 6.0) |
| Redis Cluster | redis-py (RedisCluster) | 6379 | 16384 slots, seed-node auto-discovery |
| ClickHouse | clickhouse-jdbc (JPype1 + clickhouse-jdbc) | 8123 | Columnar OLAP, 21.8+ (single node / cluster) |

> **Note:** Oracle (JDBC) is an independent plugin based on JDBC (JPype) connections, providing the same inspection capabilities as Oracle native drivers, suitable for scenarios where Oracle clients cannot be installed.

---
## Quick Start
### Docker Quick Start (Recommended)

One command to get started — no dependencies required:
#### 1、docker images
```bash
# Docker Hub
docker pull jackge12345/dbcheck:latest
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/data/reports \
  -e LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/dmssl \
  --name dbcheck \
  jackge12345/dbcheck:latest

# GitHub Container Registry (China-friendly)
docker pull ghcr.io/fiyo/dbcheck:latest
docker run -d -p 5003:5003 \
  -v dbcheck_data:/app/data \
  -v dbcheck_reports:/app/data/reports \
  -e LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/dmssl \
  --name dbcheck \
  ghcr.io/fiyo/dbcheck:latest
```

#### docker-compose

```bash
curl -o deploy/docker-compose.yml https://raw.githubusercontent.com/fiyo/DBCheck/main/deploy/docker-compose.yml
docker compose -f deploy/docker-compose.yml up -d
```

> **GBase 8s Note**: The Docker image is pre-installed with JDK + JDBC driver. GBase data sources work out of the box — no extra configuration needed.

---

### Source Installation Quick Start

#### 1. Requirements

- Python 3.10+
- Database-specific Python drivers (see table above)

#### 2. Pull local model

Install Olama locally and use the following command to pull the model:

```bash
ollama pull qwen3:30b          # Pull diagnostic model (larger = better)
ollama pull nomic-embed-text    # Pull RAG embedding model (required for knowledge base)
```
#### 3. Clone the repository and Install dependencies
```bash
# Clone the repository
git clone https://github.com/fiyo/DBCheck.git
cd DBCheck

# Install dependencies
pip install -r deploy/requirements.txt

```
#### 4. Start Web UI
```
python web_ui.py
```


### Distribution Packaging

Package as a single executable using PyInstaller:

```bash
# Windows
rd /s /q build dist __pycache__
pyinstaller dbcheck.spec
cd dist
dbcheck.exe

# Linux
pyinstaller build/dbcheck_linux.spec
cd dist
./dbcheck
```
### Visit WebUI

Visit **http://localhost:5003**. Default credentials are `admin` / `admin123` (change your password in Account Center after first login).

---

## Core Features at a Glance

| Feature | Description |
|---------|-------------|
| 🗄️ Data Source Manager | Unified management of all database instances, with grouping, batch inspection, CSV import/export |
| 📋 Database Inspection | 21 database types covered, 330+ inspection rules, auto-generates Word reports |
| 🔌 Plugin System | Extensible plugin architecture with lifecycle management (install/uninstall), independent plugin data, plugin marketplace |
| 🔍 Deep Slow Query Analysis | Correlates execution plans, I/O patterns, lock waits; AI-assisted root cause analysis |
| 🔒 Lock Diagnostics | Blocking chain visualization, deadlock stats, long transaction detection, with executable fix scripts |
| 📊 Index Health Analysis | Detects missing indexes, redundant indexes, long-unused indexes |
| ⚙️ Config Baseline Check | Compare current vs. recommended values for key parameters across all databases |
| 📈 Historical Trend Analysis | Aggregate multi-round inspection data, trend line charts, before/after change comparison |
| 🤖 AI Smart Diagnostics | Local Ollama-based, analyzes inspection metrics and generates optimization suggestions |
| 💬 AI Chat Inspection | AI panel (bottom-right in Web UI), natural language inspection workflow |
| 📡 Real-time Monitoring | Homepage live collector (throughput, connections, latency, availability) + slow-query/active-connection heatmap |
| 🖥️ Server Inspection | CPU / memory / disk / network / process comprehensive check |
| 🔗 Shareable Links | One-click shareable report links, viewable without login |
| ⏰ Scheduled Tasks | Cron-based periodic inspections, auto email/Webhook notification on completion |
| 📚 RAG Knowledge Base | Upload ops documentation; AI retrieves relevant knowledge during diagnostics |
| 📊 AWR Report Analysis | Upload Oracle AWR HTML reports; auto-generates structured Word analysis report |
| 💿 DM8 Offline Storage Check | Inspect DM8 storage health offline (no running instance); scan data files and locate bad blocks (full-zero / constant-fill / truncated) |
| 📝 SQL Editor | Built-in Web UI SQL editor with syntax highlighting, result table, execution history |
| 🖥️ Remote Terminal | SSH-based, multi-tab, fullscreen mode |
| 🔀 Workflow Orchestration | Visual DAG canvas; chain specialists / hub / skills / output with conditional branches |
| 🛡️ SQL Audit | SQL review with risk scoring + controlled execution (dry-run default, snapshot rollback) |
| 🔌 MCP Toolbox | Expose inspection & Skills as MCP tools for external AI clients (Claude Desktop, etc.) |

---

## Advanced ability


### Collaborative Diagnosis Hub (Smart Diagnosis Center)
Hand a single goal plus one data source to a team of **eleven** specialized **diagnostic specialists** (one Coordinator + ten domain experts) who collaborate on a **shared context (blackboard)** and produce: anomalies found, root-cause inference, executable remediation plans, plus cost evaluation and tickets.

| Specialist | Domain | Responsibility |
|------------|--------|----------------|
| Coordinator | Orchestration | Understands the diagnosis goal, decides which specialists join and in what order (AI-driven or rule fallback) |
| Monitoring Sentinel | Monitor | Watches real host resources and fine-grained DB metrics; raises early warnings on CPU / IO / memory / connection / lock / replication anomalies |
| Capacity Analyst | Monitor | Models capacity headroom, growth trend, and resource saturation risk |
| Deep-Inspection Analyst | Inspection | Runs the inspection engine on the target in real time; extracts config / capacity / performance risks with severity |
| Baseline-Compare Specialist | Inspection | Compares current config against baselines / history and flags drift |
| Root-Cause Analyst | Root-Cause | Correlates monitoring anomalies with inspection risks, clusters and infers root cause, gives the remediation thread |
| Native-DB Expert | Root-Cause | Specialist knowledge for domestic databases (DM8 / HGDB / Kingbase / YashanDB / GBase / UXDB) |
| SQL-Governance Specialist | SQL | For slow / high-cost SQL, suggests rewrites, indexes, and change reviews |
| Index Advisor | SQL | Detects missing / redundant / unused indexes and proposes indexing plans for slow SQL |
| Lock-Wait Analyst | Lock | For lock waits / blocks, traces the holding session and wait chain, suggests unblocking actions |
| NL-Query Specialist | NL | Explores the target via natural-language queries to surface hidden anomalies |

- **Shared context (blackboard)** — all intermediate conclusions, findings, and plans live in one space; specialists read/write directly, no lossy relay.
- **Dynamic planning** — monitoring, deep-inspection, and root-cause are always on; SQL-governance and lock analysis join early when relevant phenomena appear.
- **Fault tolerance** — one specialist failing doesn't abort the diagnosis; the error is noted in context and collaboration continues.
- **Streaming collaboration** — the hub schedules specialists one by one and emits progress events; the Web UI shows "who is analyzing now" via SSE in real time.
- **Cost Optimizer** — ranks each remediation by cost / benefit / feasibility, recommends an *easy-before-hard* order, and flags whether a step needs a maintenance window or can be auto-executed.
- **Ticket closed-loop** — one-click create tickets tracked through `Pending/Processing/Resolved/Closed/Cancelled`, with execution feedback written back — a diagnosis → dispatch → fix → feedback loop.
- **Diagnosis history** — every collaborative diagnosis is persisted (local SQLite) with a diagnosis number (`diag_no`); filterable by data source, viewable in full, and one-click linkable to a ticket.

### eBPF Kernel-Level Host Collection
When the target Linux host has **Python3 + bcc + root**, eBPF yields kernel metrics user-space tools can't reach:
- **Block-device service-time percentiles (p50 / p95 / p99, µs)** via kprobes on `blk_account_io_start` / `blk_account_io_done` — more accurate than psutil's `await`, great at exposing long-tail jitter.
- **Per-process I/O attribution** — records pid / command at I/O start, correlates at completion; outputs Top I/O processes.
- **Per-process CPU attribution** — based on `sched:sched_switch`, computes on-CPU time; outputs Top CPU processes, distinguishing "truly busy" from "waiting on I/O".

Safety by design: **opt-in only** (default off, never injects eBPF into production by default), transparent `host_collector_source` tagging (`ebpf` / `psutil` / `unavailable`), full degrade-to-psutil on any failure, and a zero-dependency Shell (`/proc`) fallback when no Python / psutil exists.

### SSH Secure Host Collection
For hosts where you don't want an agent:
- **Agentless, no Python required** — a pure Shell (`/proc`) collection script is injected over SSH; the eBPF path engages only if Python3 + bcc is present.
- **Safety guards** — a global concurrency semaphore (4) limits total SSH; one lock per host (at most 1 connection at a time); `set_keepalive(15)`; bounded channel reads (`settimeout(12)`); a hard 8s timeout watchdog (SIGALRM + thread `os._exit`) so a stuck eBPF never hangs the session; transient errors retry with backoff (max 2), auth failures don't retry.
- **Credential safety** — instance passwords are **Fernet-encrypted at rest**, decrypted only at collection time; ciphertext is never sent to the remote or the DB as plaintext.

### Unified Observability View
**host resources (eBPF / psutil / SSH) + DB fine-grained metrics + inspection risks** on one analysis plane. In one collaborative diagnosis you can see both "disk p99 latency spiked" and "the slow SQL and lock waits in that window" — root cause becomes a connected evidence chain, not isolated numbers.

---

## Workflow Orchestration

Build reusable diagnostic playbooks on a visual **DAG canvas**. Drag nodes onto the canvas, wire them with directional edges (fixed top-in / bottom-out ports), and run the whole flow against a data source.

| Node | Role |
|------|------|
| Start / End | Flow boundaries (Start emits only, End receives only) |
| Specialist | Run one diagnostic specialist (e.g. Index Advisor, Lock-Wait Analyst) against the shared context |
| Hub | Trigger a full collaborative re-diagnosis (`DiagnosticHub.dispatch`) |
| Skill | Reuse a Skills / WriteGate action (e.g. execute SQL, apply index) |
| Function | Arbitrary `callable(ctx, args)` for data moves / condition injection |
| Output | Emit a result: show in UI, write a Markdown report, or send an email |

- **Conditional branches** — each step supports a `when(ctx)` predicate, so the flow adapts to what it finds (e.g. only run the Index-Advisor branch when slow SQL is present).
- **Persistence & re-run** — workflows are saved to SQLite (`workflow_store`), listed / run / viewed from the Web UI **Workflow Orchestration** page; the Reviewer audit chain is shown inline.
- **Reuses, never reinvents** — specialist abilities, WriteGate and the Reviewer all come from the existing intelligence modules.

## SQL Audit

A built-in, always-on module for reviewing and safely executing SQL against a managed instance.

- **Review** — submit SQL, get rule-based risk scoring (MVP1: MySQL parse + rules + score + report). Execution-plan analysis can be enabled per task.
- **Controlled execution (MVP3)** — safe by default:
  - **Dry-run** is the default; real execution requires `exec_enabled=1` plus a bound target instance.
  - DML runs inside a single transaction with a hard **max-affected-rows** cap; before any UPDATE/DELETE a SELECT snapshot is written to a backup table for rollback.
  - DDL is non-transactional, so only advisory rollback DDL is generated (never auto-run).
  - Every execution / rollback action is append-only logged (`sql_audit_executions` / `sql_audit_rollbacks`).
- **Write operations** from Workflow / Skills route through the SQL Audit page as tickets, closing the *review → approve → execute → feedback* loop.

## MCP Toolbox

Expose RaccoonX inspection and Skills as **MCP (Model Context Protocol)** tools so external AI clients (Claude Desktop, etc.) can drive database checks natively.

- A standalone **stdio MCP server** (`modules/mcp_server`) — Skills and MCP tools share one registry (`modules.mcp_server.registry`), so each capability is defined once and reused by both the multi-agent hub and the MCP server.
- **Chat2DB bridge** — `chat2db_bridge.py` connects to a Chat2DB MCP server over stdio to provide natural-language-to-SQL (`nl2sql`) without embedding any Chat2DB code (source-available license, bridge-only).
- Access is gated by the same risk metadata and WriteGate used elsewhere; unconfigured dependencies degrade gracefully to `Chat2DBUnavailable`.

---

## Community vs Professional — Core Capability Comparison

| Capability | Community | Professional |
|------------|:---------:|:-----------:|
| Multi-database inspection | ✅ | ✅ |
| Real-time monitoring + health dashboard | ✅ | ✅ |
| AI smart diagnostics | ✅ | ✅ |
| Plugin system | ✅ | ✅ |
| Enterprise RBAC | ✅ | ✅ |
| eBPF kernel-level host collection | ✅ | ✅ (opt-in) |
| SSH secure host collection | ✅ | ✅ |
| Collaborative diagnosis hub (11 specialists + shared context) | ✅ | ✅ |
| Remediation cost optimizer | ✅ | ✅ |
| Ticket closed-loop | ✅ | ✅ |
| Diagnosis history | ✅ | ✅ |
| Unified observability view | ✅ | ✅ |
| Workflow Orchestration | ✅ | ✅ |
| SQL Audit (review + controlled execution) | ✅ | ✅ |
| MCP Toolbox (external AI integration) | ✅ | ✅ |

---

## 🔌 Plugin System

RaccoonX v2.8.0 introduces a fully independent plugin architecture. Plugins can now manage their own lifecycle and data, enabling true extensibility.

### Key Features

| Feature | Description |
|---------|-------------|
| Plugin Lifecycle Management | `on_install()` and `on_uninstall()` methods for automatic data initialization and cleanup |
| Independent Plugin Data | Each plugin carries its own `template_data.json`, `baseline_data.json`, and rule engine files |
| Plugin Marketplace | Browse, install, uninstall, enable/disable plugins via Web UI |
| Clean Uninstall | Automatic cleanup of templates, baselines, and rules when uninstalling plugins |
| Plugin Configuration | Each plugin has its own `plugin.json` for metadata and configuration |

### Plugin Development

Plugins are independent Python packages with the following structure:

```
plugins/available/your_plugin/
├── plugin.json          # Plugin metadata
├── main_plugin.py      # Plugin class (inherit from InspectionPlugin)
├── template_data.json  # Inspection templates (optional)
├── baseline_data.json  # Baseline configurations (optional)
└── rules/             # Rule engine files (optional)
```

For detailed plugin development guide, see [Plugin Development Documentation](docs/plugin/).

### Built-in Plugins (v2.8.0)

| Plugin | Database | Description |
|--------|----------|-------------|
| MongoDB | MongoDB 4.0+ | Basic inspection (connection status, database stats, slow queries) |
| Oracle (JDBC) | Oracle 11g/12c/19c/21c+ | Complete Oracle 11g template migration (21 chapters, 58 queries, 11 baselines) |
| DB2 (JDBC) | DB2 LUW 11.5+ / 12.x | JDBC (JPype1 + db2jcc4) LUW inspection, 42 rules, system-catalog SQL |
| Redis | Redis 3.0+ | KV cache inspection: connection, version, memory, clients, persistence, performance, replication, keyspace, slow queries, config baseline |
| Redis Cluster | Redis Cluster | Cluster topology (CLUSTER INFO / NODES), slot distribution and node health on top of single-node capabilities |
| UXDB (JDBC) | UXDB 2.x | PostgreSQL-compatible Chinese domestic DB inspection plugin, 12 rules based on ux_catalog system catalog |
| HGDB (JDBC) | HGDB V9 | PostgreSQL-compatible (PG 14.20) Chinese domestic DB inspection plugin, 12 rules based on standard PG catalogs |
| TDSQL-C MySQL (Plugin) | TDSQL-C MySQL | Tencent Cloud MySQL-compatible inspection plugin; reuses MySQL collection engine and the 20-rule MySQL rule set |

> **Note:** Plugins are completely independent. Installing a plugin automatically initializes its data; uninstalling a plugin automatically cleans up all associated data.

---

## Database Inspection

### Inspection Coverage by Database

| Category | MySQL | PG | Oracle | Oracle (JDBC) | SQL Server | DM8 | TiDB | IvorySQL | YashanDB | KingbaseES | GBase 8s | MongoDB | HGDB | TDSQL-C |
| ---------- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Basic Info (version/instance/DB) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Sessions & Connections | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Memory & Cache | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Tablespaces | — | — | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — | ✅ | — | — | — |
| SGA / PGA Memory | — | — | ✅ | ✅ | — | ✅ | — | — | ✅ | — | — | — | — | — |
| Redo Logs | — | — | ✅ | ✅ | — | ✅ | — | ✅ | — | — | — | — | — | — |
| Archive & Backup | — | — | ✅ | ✅ | ✅ | ✅ | — | — | ✅ | — | — | — | — | — |
| Key Parameter Config | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Invalid Objects | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| User Security Audit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Top SQL / Slow Queries | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Replication / Data Guard | ✅ | ✅ | — | — | — | — | ✅ | ✅ | — | ✅ | — | ✅ | ✅ | ✅ |
| RAC Cluster | — | — | ✅ | ✅ | — | — | — | — | — | — | — | — | — | — |
| Lock & Blocking Detection | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Object Statistics | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | — | — | — |
| Partitioned Tables | — | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | — | — | — | — |
| Chunks / Disk Storage | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| Logical Logs / Checkpoints | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| Database Status & Stats | — | — | — | — | — | — | — | — | — | — | — | ✅ | — | — |

### Word Report Structure (Oracle Example)

| Chapter | Content |
|---------|---------|
| Cover | Database name, version, host info, inspector, timestamp |
| Ch. 1 | OS host info (CPU / memory / disk) |
| Ch. 2 | Database basic information |
| Ch. 3 | Tablespaces (with auto-extend info) |
| Ch. 4 | SGA / PGA memory analysis |
| Ch. 5 | Key parameter configuration |
| Ch. 6–19 | Undo / Redo / Archive / DG / RAC / ASM / Sessions / Performance / Security, etc. |
| Ch. 20 | Risks & Recommendations (with executable fix SQL) |
| Ch. 21 | AI Diagnostic Suggestions (Markdown rendered in Word) |
| Ch. 22 | Report Notes |

> Report structure varies slightly by database type; all chapters can be freely configured via the Web UI.

### DB2 LUW Inspection (JDBC)

IBM Db2 LUW (Linux/Unix/Windows) **11.5+ / 12.x** is supported through the JDBC plugin (`db2_jdbc`), connecting via **JPype1 + IBM `db2jcc4.jar`** (default port **50000**). It runs a data-driven inspection with **6 chapters** and **42 built-in rules**, all based on Db2 system catalog and monitor views (no legacy 9.7 catalog names).

| Dimension | Coverage |
|-----------|----------|
| Version & Instance | DB2 version, instance config (`dbm cfg`), database config (`db cfg`), member/partition topology |
| Tablespaces & Storage | Tablespace size, usage, auto-resize, container states |
| Buffer Pools | Buffer pool definitions, hit ratio, sizing suggestions |
| Sessions & Applications | Active applications, top consumers, connection saturation |
| Locks & Blocking | Lock waits, held locks, blocking chains, long transactions |
| Tables & Indexes | Table/row statistics, index RUNSTATS freshness, unused/redundant indexes |
| Top SQL | High-cost statements from the package cache |
| Activity Monitoring | Mon-get activity metrics, elapsed-time hotspots |
| Memory | `MON_GET` memory set, dbm memory distribution |

The generated Word report includes the unified **System Resource** chapter (CPU / memory / disk) plus a **Risks & Recommendations** chapter with one-click fix SQL, and an **AI Diagnostic Suggestions** chapter. A JDBC driver (`db2jcc4.jar`) plus JDK 8/11/17 is required — the Docker image ships both, so Db2 data sources work out of the box.

### Redis Inspection (Single Node & Cluster)

Redis **3.0+** is supported through two independent plugins — `redis` (single node) and `redis-cluster` — driven by **redis-py 8.x** (RESP2, encoding-safe). A single-node inspection collects **11 chapters** spanning memory, keyspace, persistence (RDB / AOF), clients, performance, security, replication, CPU, configuration baseline, and a slow-log summary; the cluster plugin adds **cluster topology** (CLUSTER INFO / NODES), the 16384-slot distribution, and node health on top of all single-node dimensions.

| Dimension | Single Node | Cluster |
|-----------|:---:|:---:|
| Memory & Keyspace | ✅ | ✅ |
| Persistence (RDB / AOF) | ✅ | ✅ |
| Clients & Connections | ✅ | ✅ |
| Performance & Slow Log | ✅ | ✅ |
| Replication | ✅ | ✅ (with failover) |
| Security (requirepass / ACL) | ✅ | ✅ |
| Cluster Topology (nodes / slots) | — | ✅ |
| Node Health & Failover | — | ✅ |

The Word report includes the unified **System Resource** chapter (CPU / memory / disk), a **Risks & Recommendations** chapter (one-click fix), and an **AI Diagnostic Suggestions** chapter. Seed nodes are auto-discovered for clusters; on Redis < 6.0 (no ACL) the username field is safely ignored with an on-screen note. A `redis >= 5.0` dependency is required (`pip install redis`).

> **Note:** OceanBase (MySQL tenant) reuses the MySQL inspection engine and rule set (port **2881**, pymysql). Oracle-tenant support is reserved for a future release.

---


### HGDB Inspection (JDBC)

HighGo **HGDB V9** (PostgreSQL 14.20 kernel) is supported through the JDBC plugin (`hgdb_jdbc`), connecting via the standard **PostgreSQL protocol** (driver `org.postgresql.Driver` + `postgresql-42.2.2.jar`, default port **5866**, default database **highgo**). It runs a data-driven inspection with **8 chapters** and **21 queries** based on standard PG system catalogs and views (`pg_settings`, `pg_stat_activity`, `pg_locks`, `pg_roles`, `pg_stat_user_tables`, `pg_hba_file_rules`, etc.), and ships **12 built-in rules** (`pro/rules/builtin/hgdb.yaml`) covering connection, memory, backup, lock, maintenance, security and system dimensions — all merged into the unified **Risks & Recommendations** chapter. The SQL Editor also fully supports HGDB (list databases / objects / run queries) via the psycopg2 path.

### TDSQL-C MySQL Inspection (Plugin)

**TDSQL-C MySQL** (Tencent Cloud database, 100% MySQL-compatible) is supported through a dedicated plugin (`tdsqlc_mysql`) that reuses the core **MySQL inspection engine** (`main_mysql.MySQLInspector`) and the **MySQL rule set** (`pro/rules/builtin/mysql.yaml`, now tagged with `tdsqlc_mysql`). It connects via **PyMySQL** (default port **3306**, default database **mysql**) and runs the identical MySQL data-driven inspection (8 chapters / 21 queries) plus the unified **Risks & Recommendations** chapter. The SQL Editor also fully supports TDSQL-C MySQL (list databases / objects / run queries) via the PyMySQL path.

## Intelligent Risk Analysis

Automatically detects potential risks across all database types. **Each risk item includes executable fix SQL with one-click execution support.**

### Risk Rule Statistics

| Database | Rules | Coverage |
|----------|:---:|----------|
| MySQL | 35+ | Connections, memory, disk, slow queries, locks, security, replication |
| PostgreSQL | 27+ | Connections, cache, performance, security, archive, dead tuples |
| Oracle | 20+ | Tablespace, TEMP, sessions, SGA, Redo, DG, ASM, security |
| Oracle (JDBC) | 20+ | Same as Oracle (complete Oracle 11g template migration) |
| SQL Server | 15+ | Connections, sessions, waits, locks, deadlocks, backup, memory |
| DM8 | 16+ | Tablespace, memory pools, sessions, transactions, backup, security |
| TiDB | 18+ | Connections, memory, disk, slow queries, locks, security, placement |
| IvorySQL | 27+ | Same as PostgreSQL |
| YashanDB | 15+ | Connections, memory, tablespace, locks, backup, security |
| KingbaseES | 19+ | Connections, cache, performance, security, archive, stats |
| GBase 8s | 6+ | Connections, dbspace, logs, memory, password policies |
| MongoDB | 10+ | Connections, memory, operations, replication, security |
| DB2 (LUW) | 42 | Tablespaces, buffer pools, locks, memory, config, top SQL, security |
| OceanBase | Reuse MySQL 35+ + OB 12 | Tenant, params, replication, resources, security |
| Redis | 12 | Security, memory, connections, persistence, replication, performance |
| Redis Cluster | 17 | 12 single-node + 5 cluster (slots / nodes / failover) |
| ClickHouse | 15 | Replication, memory, parts/merges, slow queries, config, disk |
| UXDB | 12 | Connections, shared memory, backup readiness, lock waits, dead tuples, password encryption, superusers, instance memory |

### One-Click Fix

Each risk card provides an "Execute Fix" button. Dangerous operations (DELETE / DROP / TRUNCATE) require secondary confirmation. All operations are logged.

---

## AI Smart Diagnostics

Based on local **Ollama** deployment — all inspection data stays offline, no internet required.

| Backend | Description | Use Case |
|---------|-------------|----------|
| `ollama` | Fully local, zero cost, data never leaves the machine | Intranet, high-security environments |
| `openai` | Cloud API (OpenAI / DeepSeek), requires internet | Environments allowing cloud APIs |
| `disabled` | Disable AI (default) | No AI functionality needed |

---

## Other Features

### SQL Editor

Built-in interactive SQL editor in Web UI, supporting all 21 database types with syntax highlighting, result tables, and friendly error messages.

### Homepage Live Monitoring

The homepage "📡 Real-time Monitoring" panel shows live ECharts charts per instance, auto-refreshed every 30s via flask-socketio push (introduced in v2.10.0):

- **Response Latency (ms)** — TCP round-trip time, available for all instance types.
- **Throughput (QPS / TPS)** — deep-collected counters (queries, transactions, batch requests, compilations, …) auto-differentiated into rates. Supported for MySQL/TiDB, PostgreSQL/PG/Kingbase, Oracle, DM8 and SQL Server.
- **Connections** — active/total sessions and running sessions.

**Connectivity profile for non-deep instances:** instance types that do not yet support deep collection (or whose deep collection is temporarily failing) no longer show empty charts. They display a **port-availability timeline** (reachable / unreachable over time) and a **connectivity diagnostic gauge** showing the availability percentage plus the real reason (auth failure, circuit-breaker cooldown, port unreachable, or "type not yet supported"), keeping the dashboard informative from TCP-level data alone.

### Slow Query & Connection Heatmap

Slow queries + active connections live monitoring with heatmap visualization, auto-refresh (5–60s adjustable), CSV export support.

### Remote Terminal

SSH-based, supports password/key authentication, multi-tab management, fullscreen mode.

### Server Inspection

Independent of database inspection. Covers CPU / memory / disk / network / services / processes, generating professional server inspection reports.

### Historical Trend Analysis

Multi-round inspection data is automatically aggregated. Web UI trend analysis page displays line charts with threshold lines. Before/after changes are highlighted with colored arrows.

### Scheduled Tasks & Notifications

Supports Cron expressions with quick presets (daily / weekdays / weekly / monthly). Auto-sends email (with Word report attachment) or Webhook (WeCom / DingTalk / custom JSON) notifications on completion.

### Disaster Recovery Backup

Built-in disaster recovery backup module powered by the MIT-licensed **autobackup** engine (vendored in-process — no Docker / sidecar required). Supports scheduled backups for **MySQL / MariaDB / PostgreSQL / files** with Cron scheduling, retention-day cleanup, and webhook notifications (DingTalk / WeCom / Feishu / email). Backup history, health scoring (freshness + success rate), and one-click restore points are available from the "Disaster recovery backup" page in the Web UI. Database passwords are encrypted at rest (Fernet) and masked in API responses.

### Shareable Links

One-click shareable links for reports, viewable without login. Permission isolation, automatic visit counting, instant deletion support.

### Configuration Baseline Management

Web UI visual editor for recommended values, thresholds, and compliance rules for key parameters across all databases. Currently supported:

- MySQL: 22 parameters (buffer pool, connections, binlog, etc.)
- PostgreSQL: 21 parameters (shared_buffers, work_mem, WAL, etc.)
- Oracle: 12 parameters (SGA/PGA, processes, undo, etc.)
- Oracle (JDBC): 12 parameters (same as Oracle)
- SQL Server: 6 parameters (memory, parallelism, backup compression, etc.)
- DM8: 7 parameters (memory target, sessions, buffer pool, etc.)
- TiDB: 9 parameters (buffer pool, connections, concurrency, etc.)
- YashanDB: 8 parameters (buffer pool, connections, logs, etc.)
- KingbaseES: 7 parameters (connections, buffers, vacuum, etc.)
- GBase 8s: 9 parameters (MAXCONNECTIONS, SHMVIRTSIZE, BUFFERS, LOGSMAX, etc.)
- MongoDB: 8 parameters (max connections, cache size, replication, etc.)
- ClickHouse: 9 parameters (max_memory_usage, max_server_memory_usage, max_concurrent_queries, background_pool_size, max_execution_time, max_rows_to_read, max_insert_block_size, max_partitions_per_insert_block, background_merges_mutations_concurrency_ratio)

### Inspection Chapter Management

Configuration-driven — each database type can independently add/delete/reorder/enable/disable inspection chapters. Word reports are generated dynamically.

### AWR Report Analysis

Upload Oracle AWR HTML reports; automatically parse key performance metrics and generate structured Word analysis reports with AI-assisted diagnostics.

### DM8 Offline Storage Check

Inspect DM8 storage health **without a running database instance** — directly scan the data file directory (`.DBF` files + `dm.ctl`). Supports both local directory and remote server via SSH.

- **Local & SSH remote modes** — point at a local path, or connect to a remote host over SSH (password / key) to scan its data files.
- **Block corruption analysis** — flags suspicious bad blocks using universal binary signals:
  - `ZERO_PAGE` — an entire page filled with `0x00`
  - `CONSTANT_FILL` — an entire page filled with a single byte (e.g. `0xFF`)
  - `TRUNCATED` — the trailing page is shorter than the page size (file truncated)
  - Each bad block is located by physical page number and file offset, and attributed to its tablespace (resolved from `dm.ctl`).
- **Word report + Web UI** — a structured Word report is generated with a dedicated *Block Corruption Analysis* chapter, and the same bad-block list is viewable directly in the Web UI. Reports are saved to the unified `reports/` directory.

### RAG Knowledge Base

Upload PDF / Word / Markdown / TXT documents for automatic vectorization. AI retrieves relevant knowledge during diagnostics for more precise suggestions.

### Multi-Language & Themes

- **9 languages supported**: 中文 (default), English, Traditional Chinese (繁體中文), Japanese (日本語), Korean (한국어), Spanish (Español), French (Français), German (Deutsch), Russian (Русский)
- Switch anytime via the Web UI language selector (top-right) or the CLI argument (`python -m entrypoints.cli --lang <code>`)
- UI text, menus, report templates, and AI diagnostic labels are all localized
- Dark / Light theme support with automatic preference saving

---

## REST API

API Key authentication, suitable for CI/CD and monitoring system integration.

```bash
# Health check
curl http://localhost:5003/api/v1/health

# Trigger inspection (synchronous)
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"db_type":"mysql","host":"192.168.1.100","port":3306,"user":"root","password":"****"}'

# Trigger inspection (async, returns task_id)
curl -X POST http://localhost:5003/api/v1/inspect \
  -H "X-API-Key: YOUR_KEY" -H "Content-Type: application/json" \
  -d '{"db_type":"oracle","host":"192.168.1.200","service_name":"ORCL","user":"system","password":"****","mode":"async"}'
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Health check |
| `/api/v1/inspect` | POST | Trigger inspection |
| `/api/v1/inspect/{task_id}` | GET | Query task result |
| `/api/v1/inspects` | GET | Recent task list |
| `/share/<share_id>` | GET | View shared report |

> Production environments should use nginx as a reverse proxy and rotate API keys regularly.

---



## Environment Quick Reference

| Database | Python Driver | Extra Dependencies |
|----------|---------------|-------------------|
| MySQL / TiDB | pymysql | — |
| PostgreSQL / IvorySQL / KingbaseES | psycopg2-binary | — |
| Oracle | oracledb (recommended) | No Instant Client needed |
| SQL Server | pyodbc | ODBC Driver 17 |
| DM8 | dmpython | DM8 client libraries |
| YashanDB | yashandb | — |
| **GBase 8s** | **jaydebeapi + JPype1** | **JDK 8/11/17 + JDBC driver jar** |
| **Oracle (JDBC)** | **jpype1 + ojdbc** | **JDK 8/11/17 + ojdbc6.jar/ojdbc8.jar** |
| **MongoDB** | **pymongo** | **—** |
| **DB2 (LUW)** | **jpype1 + db2jcc4** | **JDK 8/11/17 + db2jcc4.jar** |
| **OceanBase** | **pymysql** | **—** |
| **Redis / Redis Cluster** | **redis-py** | **—** |

---

## FAQ

**Q: Some sections appear empty or missing?**
A: The template auto-degrades with graceful fallback when rendering compatibility issues occur; critical data is never lost.

**Q: Connection failed?**
A: Verify remote access permissions, user privileges, and firewall port accessibility.

**Q: GBase 8s reports "Driver not found"?**
A: Ensure the JDBC driver jar is at `drivers/gbase/jdbc-3.5.1.jar` and JDK is installed. The Docker image includes both — no extra configuration needed.

**Q: AI diagnostics not working?**
A: Ensure Ollama is running (`ollama serve`) and the model is downloaded (`ollama pull qwen3:30b`).

**Q: Oracle ORA-01017 invalid username/password?**
A: For SYSDBA users, check the "SYSDBA" checkbox in Web UI, or enter `sys as sysdba` in CLI mode.

**Q: Risk recommendations are for reference only?**
A: Built-in thresholds are based on general best practices. Evaluate against your actual business requirements.

---

## On Reliability and Bugs

**RaccoonX is developed and maintained by one person in their spare time.** It is fully open source under Apache-2.0, and the Community Edition is free forever, with no charge of any kind.

The project covers 21 database types and 330+ inspection rules, across a wide range of major versions, parameter configurations, and privilege models. There is no dedicated QA team and no test pipeline behind it, so **zero defects across all scenarios cannot be guaranteed**. Main workflows are self-tested before each release, but a single test environment cannot reproduce every real-world production setup.

If you run into a problem, there are three reasonable paths:

- **Open an issue** — include the database type and version, the error message, and reproduction steps. This is by far the most effective form of feedback, and it usually gets a response.
- **Open a pull request** — the repository is open to everyone. Many of the databases and rules supported today started as contributions from users.
- **Use something else** — if your scenario demands guaranteed reliability, commercial inspection products offer dedicated teams, SLAs, and paid support. That is an entirely legitimate choice.

This is a free, open-source side project. It does not promise commercial-grade reliability guarantees — but any issue raised in good faith will be taken seriously.

> Under Apache-2.0, this software is provided "AS IS", without warranties or conditions of any kind, either express or implied. Please assess its suitability for your production environment and validate independently in critical scenarios.

---

## Acknowledgements

This project references the following works:

- [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git)
- [Zhh9126/SQL-SERVER-CHECK](https://github.com/Zhh9126/SQL-SERVER-CHECK.git)

## Support the Project

> ❤️ Thank you for supporting RaccoonX.
>
> RaccoonX is an open-source project licensed under Apache License 2.0.
> 
> You are free to use, modify, distribute, and contribute to this project according to the license terms.
> 
> If RaccoonX helps your work, your support is appreciated:
> 
> - ⭐ Star the GitHub repository
> - 🐛 Submit bug reports
> - 💡 Suggest new features
> - 🔧 Contribute code
> - 📢 Share the project with the community
> 
> Every contribution helps RaccoonX become better.
> 
> Thank you for being part of the RaccoonX community.

## Enterprise Services

>For organizations requiring professional database consulting, customized inspection rules, deployment assistance, training, or technical support, please contact the project maintainer.
>
>Available services include:
>
>- Enterprise deployment support
>- Custom inspection templates
>- Database health assessment
>- Performance optimization consulting
>- Technical training
>
>Contact:
>
>Website: https://dbcheck.top
>
>Email: sdfiyon@gmail.com

<img src="snapshot/pay-en.png" alt="QR Code" width="800" />

<img src="snapshot/dbcheck-badge-800w.png" alt="RaccoonX Supporter Badge" width="800" />

> Please specify your name or nickname when sponsoring ❤️

### Community Supporters

| Date | Name | ID |
|------|------|------|
| 2026-04-28 | 自由的风 | No.000001 |
| 2026-04-29 | 黄嵘 | No.000002 |
| 2026-05-04 | 张佰政 | No.000003 |
| 2026-06-02 | 残酷月光 | No.000004 |
| 2026-06-03 | 大树 | No.000005 |
| 2026-06-07 | 岳彩波（Adil0518） | No.000006 |
| 2026-06-17 | 轩 | No.000007 |
| 2026-06-18 | 卿云 | No.000008 |
| 2026-06-18 | yuanlnet | No.000009 |
| 2026-06-18 | 赵法威 | No.000010 |
| 2026-06-19 | 类延良 | No.000011 |
| 2026-06-19 | 渺渺兮予怀 | No.000012 |
| 2026-09-6 | leon | No.000013 |
---

> Author: [Jack Ge](https://github.com/fiyo) &nbsp;|&nbsp; Website: [https://dbcheck.top](https://dbcheck.top) &nbsp;|&nbsp; Email: sdfiyon@gmail.com
