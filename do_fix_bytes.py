with open('D:/DBCheck/web_templates/index.html', 'rb') as f:
    content = f.read()

# From the hex analysis:
# Current (broken) in file:
#   testDatasourceConnection(\\'' + ds.id + '\\')
# Bytes: 5c 5c 27 27 ... 5c 5c 27 27
#
# Correct (what we need):
#   testDatasourceConnection(\'' + ds.id + '\')
# Bytes: 5c 27 27 ... 5c 27 27
#
# So we need to replace:
#   5c 5c 27 27  with  5c 27 27
#   AND 5c 5c 27 27  with  5c 27 27 (same pattern appears twice)

old_test = b"testDatasourceConnection(\\\\'' + ds.id + \\\\')"
new_test = b"testDatasourceConnection(\\' + ds.id + \\')"

old_delete = b"deleteDatasource(\\\\'' + ds.id + \\\\')"
new_delete = b"deleteDatasource(\\' + ds.id + \\')"

print("old_test in content?", old_test in content)
print("old_delete in content?", old_delete in content)

# Do the replacements
content2 = content.replace(old_test, new_test)
content2 = content2.replace(old_delete, new_delete)

print("After replacement, new_test in content2?", new_test in content2)

# Verify by checking the new bytes
idx = content2.find(b'testDatasourceConnection')
chunk = content2[idx:idx+50]
print(f"\nNew chunk (hex): {chunk.hex()}")
print(f"New chunk (repr): {repr(chunk)}")

with open('D:/DBCheck/web_templates/index.html', 'wb') as f:
    f.write(content2)

print("\nDone!")
