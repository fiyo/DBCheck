with open('D:/DBCheck/web_templates/index.html', 'rb') as f:
    data = bytearray(f.read())

# Find testDatasourceConnection
idx1 = data.find(b'testDatasourceConnection')
print(f'testDatasourceConnection at: {idx1}')
chunk1 = data[idx1:idx1+60]
print(f'Current hex: {chunk1.hex()}')

# Current: (\' + ds.id + \')
# Hex: 285c2727202b2064732e6964202b20275c2729
# We want: (&quot; + ds.id + &quot;)
# Hex: 28 22 + ds.id + 22 29

# The pattern after function name:
# Current: 5c27 27 20 2b 20 64 73 2e 69 64 20 2b 20 27 27 5c 27 29
# = (\'' + ds.id + '\')
# We want: 22 + ds.id + 22 = (" + ds.id + ")
# = 28 22 20 2b 20 ds.id 20 2b 20 22 29

# Let's find and replace the specific pattern
# The broken part starts after "testDatasourceConnection("
# testDatasourceConnection is 24 chars, so ( is at idx+24

# Let's find the pattern 5c27 27 20 2b 20 (which is \'\' + )
offset = data.find(b"testDatasourceConnection")
print(f"Function name ends at: {offset + 24}")

# After ( at offset+24, we have: \'\' + ds.id + \'
# That's: 5c27 27 20 2b 20 ds.id 20 2b 20 5c27 27
# We want: " + ds.id + "
# That's: 22 20 2b 20 ds.id 20 2b 20 22

# Let me just use hex replacement
# Current pattern (from earlier analysis):
# 5c2727202b2064732e6964202b20275c2729
# We want:
# 22202b2064732e6964202b20222c   wait, &quot; is 6 chars: & # 3 9 ; = 26 51 63 33 39 3b

# Actually, let's use simpler approach: double quotes around ds.id
# The JS source will be: onclick="testDatasourceConnection(\"" + ds.id + "\")"
# In the file, this is: onclick="testDatasourceConnection(\"" + ds.id + "\")"
# Hex: 6f6e636c69636b3d22746573...  on...click="testDatasourceConnection("

# Current file pattern for onclick: onclick="testDatasourceConnection(\'' + ds.id + '\')"
# Hex around this area: ...5c2727...ds.id...5c2727...

# Let's target the exact hex:
# Current: 5c2727202b2064732e6964202b20275c2729
# Target: 5c2227202b2064732e6964202b20275c2229

old_hex = "5c2727202b2064732e6964202b20275c2729"
new_hex = "5c2227202b2064732e6964202b20275c2229"

old_bytes = bytes.fromhex(old_hex)
new_bytes = bytes.fromhex(new_hex)

print(f"\nSearching for old_hex pattern...")
pos = data.find(old_bytes)
print(f"Found at: {pos}")

if pos >= 0:
    # Replace
    data[pos:pos+len(old_bytes)] = new_bytes
    print("Replacement done!")
    
    # Verify
    chunk = data[pos:pos+len(new_bytes)]
    print(f"New hex: {chunk.hex()}")
    print(f"New decoded: {chunk.decode('utf-8', errors='replace')}")
    
    with open('D:/DBCheck/web_templates/index.html', 'wb') as f:
        f.write(data)
    print("\nFile written!")
else:
    print("Pattern not found!")
