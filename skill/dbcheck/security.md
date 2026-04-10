# DBCheck Security Review Notes

This document explains the legitimate purpose of patterns detected by ClawHub Security Scanner in this skill.

## Pattern 1: `subprocess` calls

**Where**: `scripts/main_mysql.py` and `scripts/main_pg.py`

**Detected patterns**:
- `subprocess.Popen(["wmic", ...])` — Windows disk info collection
- `subprocess.Popen(["df", "-h"])` — Linux disk info collection
- `subprocess.Popen(["lscpu"])` — Linux CPU info collection

**Legitimate purpose**: This skill performs **database health inspection**. System resource data (CPU, memory, disk) is a standard part of any database health check report. The subprocess calls invoke standard OS utilities (`wmic`, `df`, `lscpu`) to collect this data — the same commands any DBA would run manually.

**No network communication, no remote execution, no privilege escalation.**

---

## Pattern 2: `base64` encoding/decoding

**Where**: `scripts/main_mysql.py` and `scripts/main_pg.py` — `simple_encrypt.py` utility class

**Detected patterns**:
- `base64.b64encode(encrypted).decode('utf-8')`
- `base64.b64decode(token.encode('utf-8'))`

**Legitimate purpose**: DBCheck supports saving database credentials in config files. To avoid storing passwords in plain text, a simple symmetric encryption is used: XOR + SHA-256 key derivation + Base64 encoding. This is a standard and transparent practice for local configuration files.

**This is not used for obfuscation, not used for network communication, not used to hide malicious payloads.**

---

## Data Flow

```
User provides credentials (via Agent prompt)
    ↓
run_inspection.py creates getData / saveDoc instances
    ↓
getData inspects: database config, session stats, indexes, etc.
    ↓
saveDoc generates Word report locally
    ↓
Report saved to <scripts_dir>/reports/
```

**No data leaves the user's machine except the Word report file.**

---

## Comparison with ClawHavoc Malicious Patterns

| ClawHavoc Attack Pattern | DBCheck Reality |
|---------------------------|-----------------|
| `curl ... | bash` (remote code download) | None — all code is bundled locally |
| Reads `~/.ssh/`, `~/.aws/credentials` | No — only connects to user-specified DB |
| Sends data to remote C2 server | No — no network exfiltration whatsoever |
| Modifies startup items / crontab | No — read-only health inspection |
| Uses `eval`/`exec` on obfuscated strings | No — Base64 used only for local password encryption in config |

---

## Contact

If you believe this skill was incorrectly flagged, please contact the author:
- GitHub: https://github.com/Zhh9126/MySQLDBCHECK
- ClawHub: fiyo

This skill is MIT-licensed, fully open-source, and performs only what it claims to do.
