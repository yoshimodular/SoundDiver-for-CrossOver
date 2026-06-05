#!/usr/bin/env python3
"""
patch_winecoreaudio.py

Patches winecoreaudio.so to:
  1. Add LC_LOAD_WEAK_DYLIB → @loader_path/midi_router.dylib
  2. Restore the original callq instructions (undo the constant-return patch)

Run with --revert to fully undo all changes (uses .bak).
"""
import struct, sys, shutil, os

SO  = "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/winecoreaudio.so"
BAK = SO + ".bak"

LC_LOAD_WEAK_DYLIB = 0x80000018
DYLIB_NAME = b"@loader_path/midi_router.dylib\x00"

# Pad string to 8-byte boundary
pad = (8 - len(DYLIB_NAME) % 8) % 8
DYLIB_NAME_PADDED = DYLIB_NAME + b"\x00" * pad

# LC_LOAD_WEAK_DYLIB structure:
#   cmd (4), cmdsize (4), name_offset (4), timestamp (4),
#   current_version (4), compatibility_version (4)  = 24 bytes header
HEADER_SIZE = 24
CMD_SIZE    = HEADER_SIZE + len(DYLIB_NAME_PADDED)

# Original callq bytes (to restore)
ORIG_DEST = bytes([0xE8, 0xD6, 0x31, 0x00, 0x00])  # callq MIDIGetNumberOfDestinations
ORIG_SRC  = bytes([0xE8, 0xCF, 0x31, 0x00, 0x00])  # callq MIDIGetNumberOfSources
PATCH_DEST = bytes([0xB8, 0x07, 0x00, 0x00, 0x00]) # mov eax, 7  (current patch)
PATCH_SRC  = bytes([0xB8, 0x08, 0x00, 0x00, 0x00]) # mov eax, 8

OFFSET_DEST = 0x54ca
OFFSET_SRC  = 0x54d7


def read_header(data):
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = \
        struct.unpack_from('<IIIIIIII', data, 0)
    return ncmds, sizeofcmds


def has_midi_router(data):
    ncmds, _ = read_header(data)
    offset = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, offset)
        if cmd == LC_LOAD_WEAK_DYLIB:
            name_off = struct.unpack_from('<I', data, offset + 8)[0]
            name = data[offset + name_off: offset + cmdsize].split(b'\x00')[0]
            if b'midi_router' in name:
                return True
        offset += cmdsize
    return False


def apply(data):
    ncmds, sizeofcmds = read_header(data)
    gap_start = 32 + sizeofcmds
    gap_end   = 0x1580
    available = gap_end - gap_start
    print(f"Available gap: {available} bytes, need: {CMD_SIZE} bytes")
    assert available >= CMD_SIZE, "Not enough space in header!"

    if has_midi_router(data):
        print("LC_LOAD_WEAK_DYLIB already present — skipping load command injection")
    else:
        # Build the load command
        lc  = struct.pack('<II', LC_LOAD_WEAK_DYLIB, CMD_SIZE)
        lc += struct.pack('<I', HEADER_SIZE)   # name offset from start of cmd
        lc += struct.pack('<I', 0)             # timestamp
        lc += struct.pack('<I', 0x00010000)    # current_version 1.0
        lc += struct.pack('<I', 0x00010000)    # compat_version  1.0
        lc += DYLIB_NAME_PADDED

        data = bytearray(data)
        data[gap_start: gap_start + CMD_SIZE] = lc
        # Update ncmds and sizeofcmds in header
        struct.pack_into('<I', data, 16, ncmds + 1)
        struct.pack_into('<I', data, 20, sizeofcmds + CMD_SIZE)
        data = bytes(data)
        print(f"Injected LC_LOAD_WEAK_DYLIB at offset 0x{gap_start:x}")

    # Restore original callq instructions
    data = bytearray(data)
    cur_dest = bytes(data[OFFSET_DEST: OFFSET_DEST + 5])
    cur_src  = bytes(data[OFFSET_SRC:  OFFSET_SRC  + 5])

    if cur_dest == PATCH_DEST:
        data[OFFSET_DEST: OFFSET_DEST + 5] = ORIG_DEST
        print(f"Restored callq MIDIGetNumberOfDestinations at 0x{OFFSET_DEST:x}")
    elif cur_dest == ORIG_DEST:
        print(f"callq at 0x{OFFSET_DEST:x} already original")
    else:
        print(f"WARNING: unexpected bytes at 0x{OFFSET_DEST:x}: {cur_dest.hex()}")

    if cur_src == PATCH_SRC:
        data[OFFSET_SRC: OFFSET_SRC + 5] = ORIG_SRC
        print(f"Restored callq MIDIGetNumberOfSources at 0x{OFFSET_SRC:x}")
    elif cur_src == ORIG_SRC:
        print(f"callq at 0x{OFFSET_SRC:x} already original")
    else:
        print(f"WARNING: unexpected bytes at 0x{OFFSET_SRC:x}: {cur_src.hex()}")

    return bytes(data)


def revert():
    if not os.path.exists(BAK):
        print(f"ERROR: backup {BAK} not found")
        sys.exit(1)
    shutil.copy2(BAK, SO)
    print(f"Reverted {SO} from backup")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
        sys.exit(0)

    with open(SO, 'rb') as f:
        data = f.read()

    patched = apply(data)

    with open(SO, 'wb') as f:
        f.write(patched)
    print("Done.")
