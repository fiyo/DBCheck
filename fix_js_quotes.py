import os

file_path = 'D:/DBCheck/web_templates/index.html'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Fix: in the file, the JS code has:  onclick="testDatasourceConnection(' + ds.id + ')"
# This produces broken HTML: onclick="testDatasourceConnection(abc123)"  (missing quotes around string arg)
# Fix: change to:  onclick="testDatasourceConnection("\\'" + ds.id + "\\')"

# Pattern 1: testDatasourceConnection in loadDataSources()
old1 = "testDatasourceConnection(' + ds.id + ')"
new1 = "testDatasourceConnection(\\'" + ds.id + "\\')"
count1 = content.count(old1)
print(f'Fix 1: found {count1} occurrences of unquoted ds.id in testDatasourceConnection')
content = content.replace(old1, new1)

# Pattern 2: deleteDatasource in loadDataSources()
old2 = "deleteDatasource(' + ds.id + ')"
new2 = "deleteDatasource(\\'" + ds.id + "\\')"
count2 = content.count(old2)
print(f'Fix 2: found {count2} occurrences of unquoted ds.id in deleteDatasource')
content = content.replace(old2, new2)

# Pattern 3: toggleRule in loadRules()
old3 = r"toggleRule(\'" + r.id + \')"  
# Actually, let me search for the literal text:  toggleRule(\\'' + r.id + '\\')
old3 = "toggleRule(\\'" + r.id + "\\')"
new3 = "toggleRule(\\'" + r.id + "\\')"
# Wait, these are the same... Let me think again.

# In the file, the JS code has:  onclick="toggleRule(\\'' + r.id + '\\')"
# That's already correct! The \\' becomes ' in the HTML attribute.
# Let me check if there's a broken version...

# Actually, looking at my original add_js_functions.py code:
# 'onclick="toggleRule(\\'' + r.id + '\\')"'
# In Python string: '...toggleRule(\\'' + r.id + '\\')..."
# The Python string literal contains:  toggleRule(\' + r.id + \')
# Which in the file is:  onclick="toggleRule(\' + r.id + \')"
# When JS runs, this produces: onclick="toggleRule(abc123)" -- BROKEN (no quotes)

# So the fix for toggleRule is the same as the others:
old3 = "toggleRule(' + r.id + ')"
new3 = "toggleRule(\\'" + r.id + "\\')"
count3 = content.count(old3)
print(f'Fix 3: found {count3} occurrences of unquoted r.id in toggleRule')
content = content.replace(old3, new3)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Done: fixed onclick quoting issues')
