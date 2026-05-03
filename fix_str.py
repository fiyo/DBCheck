with open('D:/DBCheck/web_templates/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# From the bytes output: the file contains \\\\'
# That's two backslash characters + one quote character
# In the file, we need to have: \\'  (one backslash + one quote)

# Let's do a direct byte replacement
# Current (4 chars in file): backslash, backslash, backslash, quote
# Target (2 chars in file): backslash, quote

# In Python, to match 3 backslashes + 1 quote, we need: \\\\\\\\\\' (8 backslashes + quote)
# But actually, let's just use the exact byte values

# From the repr output: b'\\\\\\' (4 backslashes + quote) in Python source
# This means: \\\\ = two backslashes in file, then \\' = one backslash + one quote
# Wait, that's 3 backslashes total in file...

# Let me just count the bytes: b'testDatasourceConnection(\\\\\'\' + ds.id'
# After the ( there are: \\ \\ \\ ' \\ \\ \\ '
# That's: 3 backslashes + quote + 3 backslashes + quote

# Wait, let me re-read the bytes output:
# b'testDatasourceConnection(\\\\\'\' + ds.id + \'\\\\\')">'
# Positions:
# (\\\\ = two backslashes
# \\' = one backslash + one quote
# \' = one backslash + one quote
# ...

# OK I give up counting. Let me just do a targeted replacement.
# I'll replace the specific wrong pattern with the correct one.

# From the raw output: 'testDatasourceConnection(\\\\\'\' + ds.id + \'\\\\\')"
# I need to change this to: 'testDatasourceConnection(\'' + ds.id + '\')"

# Let me use exact string replacement
old_test = "testDatasourceConnection(\\\\\\'\\' + ds.id + \\'\\\\\\')"
new_test = "testDatasourceConnection(\\'\\' + ds.id + \\'\\')"

old_delete = "deleteDatasource(\\\\\\'\\' + ds.id + \\'\\\\\\')"
new_delete = "deleteDatasource(\\'\\' + ds.id + \\'\\')"

print("old_test in content?", old_test in content)
print("old_delete in content?", old_delete in content)

# Let's do the replacement
content2 = content.replace(old_test, new_test)
content2 = content2.replace(old_delete, new_delete)

# Verify
print("After replacement, new_test in content2?", new_test in content2)

with open('D:/DBCheck/web_templates/index.html', 'w', encoding='utf-8') as f:
    f.write(content2)

print("Done!")
