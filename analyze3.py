# Let's start fresh - re-read the file and analyze more carefully
with open('D:/DBCheck/web_templates/index.html', 'rb') as f:
    content = bytearray(f.read())

# The broken content: testDatasourceConnection(\\'' + ds.id + \\'')
# Let's find it
idx = content.find(b'testDatasourceConnection')
print(f"Found at: {idx}")

# Read a larger chunk
chunk = content[idx:idx+100]
print(f"\nChunk bytes: {chunk.hex()}")
print(f"Chunk repr: {repr(chunk)}")

# Decode and find the pattern positions
text = chunk.decode('utf-8', errors='replace')
print(f"\nDecoded: {text}")

# In the decoded text, find the position of (\\'' + ds.id + \\')
# That should be at text[24:57] approximately
paren_start = text.find('(')
print(f"\n( at position: {paren_start}")
if paren_start >= 0:
    print(f"Text from ( : {text[paren_start:paren_start+50]}")
    print(f"Hex from ( : {chunk[paren_start:paren_start+50].hex()}")

# Let's be very precise. The pattern we want to replace is:
# From the opening ( to the closing )
# Current: (\\'' + ds.id + \\')
# Target: (\'' + ds.id + '\')

# Find all ( positions after testDatasourceConnection
for i in range(idx + 24, idx + 30):
    c = chr(content[i]) if i < len(content) else '?'
    h = hex(content[i]) if i < len(content) else '?'
    print(f"  Byte at {i}: {h} = '{c}'")
