#!/usr/bin/env python3
"""
patch_midi_lookup.py — non-consecutive MIDI port routing for winecoreaudio.so

Embeds a lookup table + trampolines in the binary's header gap so
MIDIGetSource(i) and MIDIGetDestination(i) use ~/.sounddiver_midi.conf
indices instead of 0, 1, 2, ...

Run after editing ~/.sounddiver_midi.conf. Restart SoundDiver each time.
"""
import struct, sys, os

SO   = "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/winecoreaudio.so"
CONF = os.path.expanduser("~/.sounddiver_midi.conf")

MAX_PORTS = 32   # table slots allocated (up to 32 ports supported)

# Call-site offsets (VM address == file offset for this .so)
OFF_NUM_DEST = 0x54ca   # mov eax, N  → N+1 after incl = num_dests
OFF_NUM_SRC  = 0x54d7   # mov eax, N  = num_srcs (no incl)
OFF_SRC_CALL = 0x56a3   # 8 bytes: movq %r12,%rdi + callq _MIDIGetSource
OFF_DST_CALL = 0x5831   # 8 bytes: movq %r15,%rdi + callq _MIDIGetDestination

PLT_SRC = 0x86b1        # PLT stub for _MIDIGetSource
PLT_DST = 0x869f        # PLT stub for _MIDIGetDestination

# Trampoline positions in header gap (fixed so callq offsets are stable)
TRAM_SRC  = 0x0bf8                         # right after LC_LOAD_WEAK_DYLIB
TRAM_SIZE = 20 + MAX_PORTS * 4             # 148 bytes per trampoline
TRAM_DST  = TRAM_SRC + TRAM_SIZE           # 0xc8c

# Original bytes at call sites (for first-time patch detection)
ORIG_SRC = bytes([0x4c,0x89,0xe7,0xe8,0x06,0x30,0x00,0x00])
ORIG_DST = bytes([0x4c,0x89,0xff,0xe8,0x66,0x2e,0x00,0x00])


def read_conf():
    """
    Returns (src_ports, dst_ports).
    Config lines: a single number (symmetric) or src=X,dst=Y (asymmetric).
    Asymmetric is needed for physical DIN synths on multi-port MIDI interfaces
    where CoreMIDI source and destination indices differ.
    """
    if not os.path.exists(CONF):
        print(f"[warn] {CONF} not found — using ports 0-7")
        d = list(range(8))
        return d, d
    src, dst = [], []
    with open(CONF) as f:
        for line in f:
            val = line.split('#')[0].strip()
            if not val:
                continue
            if 'src=' in val or 'dst=' in val:
                parts = dict(p.split('=') for p in val.replace(' ','').split(','))
                src.append(int(parts['src']))
                dst.append(int(parts['dst']))
            else:
                idx = int(val)
                src.append(idx)
                dst.append(idx)
    if not src:
        print("[warn] config empty — using ports 0-7")
        d = list(range(8))
        return d, d
    assert len(src) == len(dst)
    assert len(src) <= MAX_PORTS, f"Max {MAX_PORTS} ports supported"
    return src, dst


def rel32(from_addr, to_addr):
    """4-byte LE signed offset for a 5-byte callq/jmp."""
    return struct.pack('<i', to_addr - (from_addr + 5))


def make_trampoline(reg, table, plt_addr, tram_addr):
    """
    20-byte trampoline + MAX_PORTS*4 byte table.
    reg: 'r12' (sources loop) or 'r15' (destinations loop)
    Performs: rdi = table[reg]; tail-jmp to PLT stub
    """
    if reg == 'r12':
        push, pop = b'\x41\x54', b'\x41\x5c'
        sib = 0xa0   # [rax + r12*4]
    else:
        push, pop = b'\x41\x55', b'\x41\x5d'
        sib = 0xb8   # [rax + r15*4]

    # leaq table(%rip), %rax  — table is at tram_addr+20, leaq ends at tram_addr+9
    leaq = b'\x48\x8d\x05' + struct.pack('<i', (tram_addr + 20) - (tram_addr + 9))
    movl = bytes([0x42, 0x8b, 0x3c, sib])        # movl (%rax,reg,4), %edi
    jmp  = b'\xe9' + rel32(tram_addr + 15, plt_addr)

    code = push + leaq + movl + pop + jmp
    assert len(code) == 20

    tbl = b''.join(struct.pack('<I', p) for p in table)
    tbl += b'\x00' * (MAX_PORTS * 4 - len(tbl))  # pad to fixed size
    return code + tbl


def patch(src_ports, dst_ports):
    n = len(src_ports)
    assert len(dst_ports) == n
    print(f"Ports ({n}):")
    print(f"  src: {src_ports}")
    print(f"  dst: {dst_ports}")

    data = bytearray(open(SO, 'rb').read())

    cur_src = bytes(data[OFF_SRC_CALL:OFF_SRC_CALL+8])
    cur_dst = bytes(data[OFF_DST_CALL:OFF_DST_CALL+8])

    first_time_src = (cur_src == ORIG_SRC)
    first_time_dst = (cur_dst == ORIG_DST)

    for label, cur, orig, off in [
        ('src', cur_src, ORIG_SRC, OFF_SRC_CALL),
        ('dst', cur_dst, ORIG_DST, OFF_DST_CALL),
    ]:
        if cur == orig:
            print(f"  0x{off:x} ({label}): original ✓")
        elif cur[0] == 0xe8:
            print(f"  0x{off:x} ({label}): already patched — updating table")
        else:
            print(f"  0x{off:x} ({label}): unexpected bytes {cur.hex()} — abort")
            return False

    # Write trampolines with separate src/dst tables
    data[TRAM_SRC:TRAM_SRC+TRAM_SIZE] = make_trampoline('r12', src_ports, PLT_SRC, TRAM_SRC)
    data[TRAM_DST:TRAM_DST+TRAM_SIZE] = make_trampoline('r15', dst_ports, PLT_DST, TRAM_DST)
    print(f"  Trampolines at 0x{TRAM_SRC:x} (src) and 0x{TRAM_DST:x} (dst)")

    # num_dests = n → n+1 after incl (destinations loop runs n times)
    data[OFF_NUM_DEST:OFF_NUM_DEST+5] = b'\xb8' + struct.pack('<I', n)
    # num_srcs  = n  (sources loop runs n times)
    data[OFF_NUM_SRC:OFF_NUM_SRC+5]   = b'\xb8' + struct.pack('<I', n)
    print(f"  num_dests/srcs: {n}")

    # Patch call sites (only on first run; offsets don't change on re-run)
    if first_time_src:
        data[OFF_SRC_CALL:OFF_SRC_CALL+8] = b'\xe8' + rel32(OFF_SRC_CALL, TRAM_SRC) + b'\x90\x90\x90'
        print(f"  Patched 0x{OFF_SRC_CALL:x} → callq TRAM_SRC")
    if first_time_dst:
        data[OFF_DST_CALL:OFF_DST_CALL+8] = b'\xe8' + rel32(OFF_DST_CALL, TRAM_DST) + b'\x90\x90\x90'
        print(f"  Patched 0x{OFF_DST_CALL:x} → callq TRAM_DST")

    open(SO, 'wb').write(data)
    print("Done. Restart SoundDiver.")
    return True


if __name__ == '__main__':
    src, dst = read_conf()
    patch(src, dst)
