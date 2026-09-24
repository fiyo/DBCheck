"""Verify cryptography kdf modules are packed inside a PyInstaller onefile exe (Issue #57)."""
import struct, sys, zlib

path = r"D:\DBCheck\dist\RaccoonX-Windows-x86_64-v26.9.24.1\dbcheck.exe"
MAGIC = b"MEI\014\013\012\013\016"

with open(path, "rb") as f:
    data = f.read()

pos = data.rfind(MAGIC)
if pos < 0:
    sys.exit("MEI cookie not found")
# PyInstaller cookie: magic(8) lengthofPackage(u32) toc(u32) tocLen(u32) pyver(u32) pylibname(64)
magic, lengthofPackage, toc, tocLen, pyver = struct.unpack("!8sIIII", data[pos:pos+24])
pkg_start = len(data) - lengthofPackage  # cookie sits at archive end
toc_start = pkg_start + toc

entries = []
p = toc_start
end = toc_start + tocLen
while p < end:
    (entry_size,) = struct.unpack("!i", data[p:p+4])
    name_len = entry_size - 18
    entry_pos, cmprsdDataSize, uncmprsdDataSize, cmprsFlag, typeCmprsData = struct.unpack(
        "!IIIBc", data[p+4:p+18])
    name = data[p+18:p+entry_size].rstrip(b"\0").decode("utf-8", "replace")
    entries.append((name, cmprsFlag, typeCmprsData, cmprsdDataSize, uncmprsdDataSize))
    p += entry_size

print(f"total archive entries: {len(entries)}")

# Locate PYZ archive entry
pyz = [e for e in entries if e[0] == "pyz" or e[0].endswith(".pyz")]
print("pyz entries:", [(n, t) for n, _, t, _, _ in pyz])
entry = pyz[0]
p = toc_start
while p < end:
    (entry_size,) = struct.unpack("!i", data[p:p+4])
    entry_pos, cs, us, cf, tp = struct.unpack("!IIIBc", data[p+4:p+18])
    ename = data[p+18:p+entry_size].rstrip(b"\0").decode("utf-8", "replace")
    if ename == entry[0]:
        break
    p += entry_size

blob = data[pkg_start + entry_pos: pkg_start + entry_pos + cs]
if cf:
    blob = zlib.decompress(blob)

# PYZ header: magic(4) pymagic(4) tocpos(u32 BE)
assert blob[:4] == b"PYZ\0", blob[:4]
(tocpos,) = struct.unpack("!I", blob[8:12])
toc_data = blob[tocpos:]
import marshal
toc_list = marshal.loads(toc_data)
mods = sorted(n for (n, *_) in toc_list)
print(f"PYZ modules: {len(mods)}")

kdf = [m for m in mods if ".kdf" in m]
print("kdf modules:", kdf)
cryptography_mods = [m for m in mods if m.startswith("cryptography")]
print(f"cryptography modules total: {len(cryptography_mods)}")
expected = [
    "cryptography.hazmat.primitives.kdf.pbkdf2",
    "cryptography.hazmat.primitives.kdf.scrypt",
    "cryptography.hazmat.primitives.kdf.hkdf",
    "cryptography.hazmat.primitives.kdf.x963kdf",
    "cryptography.hazmat.primitives.kdk.concatkdf",
    "cryptography.hazmat.primitives.kdk.hkdf",
]
missing = [m for m in expected if m not in mods and m not in kdf]
print("expected-missing:", missing)
print("RESULT:", "PASS" if kdf else "FAIL — kdf still absent")
