with open('D:/DBCheck/web_templates/index.html', 'rb') as f:
    content = f.read()

# Let's search for the exact byte sequence
# From the hex output: 5c 5c 27 27 (two backslashes + two quotes)
# After + ds.id + : 5c 5c 27 27 again (two backslashes + two quotes)

# Let's find ALL occurrences of this pattern
search = b'\\\'\\\' + ds.id'
pos = content.find(search)
print(f"Found '\\\\\\'\\\\' + ds.id' at: {pos}")
if pos > 0:
    print(f"Context: {content[pos-20:pos+40].hex()}")
    print(f"Context repr: {repr(content[pos-20:pos+40])}")

# Also find the test pattern
search2 = b'testDatasourceConnection'
positions = []
start = 0
while True:
    pos = content.find(search2, start)
    if pos == -1:
        break
    positions.append(pos)
    start = pos + 1

print(f"\nFound testDatasourceConnection at {len(positions)} positions: {positions}")
for p in positions[:5]:
    chunk = content[p:p+60]
    print(f"\n  At {p}: {chunk.hex()}")
    print(f"  {repr(chunk)}")
