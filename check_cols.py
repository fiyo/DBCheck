# -*- coding: utf-8 -*-
import oracledb

conn = oracledb.connect(user='sys', password='oracle', dsn='192.168.42.220:1521/hbgdb', mode=oracledb.SYSDBA)
cur = conn.cursor()

# 1. dba_temp_free_space: 用 FREE_SPACE（该视图没有 BYTES 列）
cur.execute("SELECT tablespace_name, SUM(free_space)/1024/1024 free_mb FROM dba_temp_free_space GROUP BY tablespace_name")
print('dba_temp_free_space:', cur.fetchall())

# 2. dba_free_space: 用 BYTES
cur.execute("SELECT tablespace_name, SUM(bytes)/1024/1024 used_mb FROM dba_free_space GROUP BY tablespace_name")
print('dba_free_space:', len(cur.fetchall()), 'rows')

# 3. CONTENTS 是 Oracle 关键字，需要双引号包裹
cur.execute('SELECT tablespace_name, status FROM dba_tablespaces WHERE contents = \'PERMANENT\'')
print('dba_tablespaces contents=PERMANENT:', len(cur.fetchall()), 'rows')

# 4. dba_segments 用 BYTES
cur.execute("SELECT tablespace_name, SUM(bytes)/1024/1024 used_mb FROM dba_segments GROUP BY tablespace_name")
print('dba_segments:', len(cur.fetchall()), 'rows')

# 5. dba_data_files 用 BYTES
cur.execute("SELECT tablespace_name, SUM(bytes)/1024/1024 curr_mb FROM dba_data_files GROUP BY tablespace_name")
print('dba_data_files:', len(cur.fetchall()), 'rows')

cur.close()
conn.close()
print('ALL OK')
