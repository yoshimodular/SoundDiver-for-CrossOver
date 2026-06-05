# SoundDiver for CrossOver

Running **Emagic SoundDiver 3.0.x** on modern macOS via **CrossOver/Wine**, including fixes for the MIDI port overflow crash, the divide-by-zero crash, and a configurable non-consecutive MIDI port selector.

---

## Background

SoundDiver 3.0.x is a universal MIDI patch editor/librarian released by Emagic in the late 1990s. It was designed for Windows 98/XP (32-bit) and is no longer commercially available. It remains the most capable universal synth librarian ever made, with hundreds of adaptation modules for synthesizers from that era.

Getting it to run on modern macOS via Wine/CrossOver requires patching three separate bugs — two in the application binary and one in Wine's CoreMIDI driver.

---

## Requirements

- **macOS** (Apple Silicon or Intel, tested on macOS 15 Sequoia)
- **CrossOver** (tested on 26.x). Wine standalone may also work with adjustments.
- **SoundDiver 3.0.x installer** (Windows version)
- **Python 3** (comes with macOS)
- **Xcode Command Line Tools**: `xcode-select --install`

---

## The three problems

### Problem 1 — Splash screen hang (MMDRV_Alloc overflow)

**Symptom:** SoundDiver launches but freezes on the splash screen and never reaches the main interface.

**Root cause:** Wine's CoreMIDI driver (`winecoreaudio.so`) enumerates *all* CoreMIDI endpoints on the Mac and registers each as a MIDI device in Wine's internal driver table (`MMDRV_Alloc`). On modern macOS setups with many virtual MIDI ports — common with DAWs, virtual instruments, IAC buses, MIDI routing software — this table overflows.

The overflow happens in `unix_midi_init` inside `winecoreaudio.so` (x86_64 Mach-O), where `MIDIGetNumberOfSources()` and `MIDIGetNumberOfDestinations()` return the full count of CoreMIDI endpoints. With 20+ endpoints the internal Wine MIDI driver table fills up, causing `midiInOpen` calls to fail silently and leaving SoundDiver stuck waiting for MIDI initialization to complete.

**Log evidence:**
```
fixme:midi:midi_in_open No support for MIDI_IO_STATUS in flags yet  (×N)
err:winmm:MMDRV_Alloc Too many open drivers
```

**Fix:** Patch `winecoreaudio.so` to return a capped count instead of the real count. See [Fix 1](#fix-1--cap-midi-port-count) below.

---

### Problem 2 — Divide-by-zero crash when selecting a MIDI port

**Symptom:** SoundDiver gets past the splash screen (e.g. with audio disabled via `Audio=""` registry key) but crashes immediately when the user tries to select a MIDI OUT port.

**Root cause:** A division in SoundDiver's code at offset `0x4bbd4` divides by the result of `midiOutGetNumDevs()`. If Wine's MIDI initialization failed (Problem 1), `midiOutGetNumDevs()` returns 0 → divide by zero.

**Crash dump:**
```
Unhandled exception: divide by zero in wow64 32-bit code (0x0044bbd4)
EIP:0044bbd4  ECX:00000000
0x0044bbd4 sounddiver+0x4bbd4: divl %ecx
Platform: x86_64 (guest: i386)
```

**Fix:** Once Problem 1 is fixed, `midiOutGetNumDevs()` returns a non-zero count and this crash does not occur. No patch to the exe is needed if you fix Problem 1 first.

If you cannot fix Problem 1 (e.g. you need `Audio=""` for other reasons), you can NOP the division:
```bash
printf '\x90\x90' | dd of="SoundDiver.exe" bs=1 seek=$((0x4bbd4)) conv=notrunc
```

---

### Problem 3 — DYLD_INSERT_LIBRARIES does not work under Rosetta

**Symptom:** Any attempt to inject a custom dylib via `DYLD_INSERT_LIBRARIES` has no effect.

**Root cause:** CrossOver's `wine` script is a Perl program that runs natively as arm64. When it `exec`s `wineloader` (x86_64), the process transitions from arm64 to x86_64 (Rosetta 2). During this arm64→x86_64 handoff, Rosetta's loader strips `DYLD_INSERT_LIBRARIES`. Additionally, Rosetta's AOT (ahead-of-time) compilation of x86_64 dylibs resolves external symbols eagerly, bypassing the lazy binding mechanism that `DYLD_INTERPOSE` relies on — so even loading a dylib via `LC_LOAD_WEAK_DYLIB` does not activate interpose sections.

**Consequence:** Any approach that relies on runtime symbol interposition will not work. All fixes must be applied as static binary patches to `winecoreaudio.so`.

---

## The fix

Everything is patched into one file:

**`/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/winecoreaudio.so`**

Always back it up first:
```bash
cp winecoreaudio.so winecoreaudio.so.bak
```

The file is owned by your user, writable, and unsigned. CrossOver is not code-signed in a way that prevents modification of enclosed libraries. Rosetta's AOT translation cache is keyed on the file's hash and is automatically invalidated and regenerated after patching.

---

## Fix 1 — Cap MIDI port count

This is the minimum fix to make SoundDiver run. It patches two `callq` instructions in `unix_midi_init` to return a constant instead of calling the real CoreMIDI functions.

**Patch locations (file offset = VM address for this .so):**

| Offset | Original | Patched | Effect |
|--------|----------|---------|--------|
| `0x54ca` | `E8 D6 31 00 00` (callq `MIDIGetNumberOfDestinations`) | `B8 0N 00 00 00` (mov eax, N) | num_dests = N+1 after `incl %eax` at `0x54cf` |
| `0x54d7` | `E8 CF 31 00 00` (callq `MIDIGetNumberOfSources`) | `B8 0N 00 00 00` (mov eax, N) | num_srcs = N |

Choose N based on how many MIDI ports you need (8 is safe and sufficient for most setups):

```bash
SO="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/winecoreaudio.so"
N=8
printf "\xB8$(printf '\\x%02x' $((N-1)))\x00\x00\x00" | dd of="$SO" bs=1 seek=$((0x54ca)) conv=notrunc
printf "\xB8$(printf '\\x%02x' $N)\x00\x00\x00"        | dd of="$SO" bs=1 seek=$((0x54d7)) conv=notrunc
```

> **Note:** The destinations path has an `incl %eax` at `0x54cf` that adds 1 (for the MIDI Mapper slot). So patching `0x54ca` with `N-1` gives `N` real destination ports. The sources path has no incl, so patch `0x54d7` directly with `N`.

---

## Fix 2 — Non-consecutive MIDI port selection (lookup table)

Fix 1 always exposes the *first N* CoreMIDI ports in enumeration order. This is often wrong — the ports you want for your synths may be at indices 8, 14, 26, etc. while the first ports are DAW controllers or utility buses.

This fix patches the *loop* inside `unix_midi_init` that calls `MIDIGetSource(i)` and `MIDIGetDestination(i)`, replacing the sequential index `i` with a value from a lookup table. The table is embedded in the binary's header gap (the unused space between the Mach-O load commands and the `__text` section — 2496 bytes free in the CrossOver 26 build).

**Architecture:**

```
winecoreaudio.so header gap (offset 0xBF8, within __TEXT segment, executable):

TRAM_SRC (20 bytes + 128 bytes table):
  push %r12
  leaq src_table(%rip), %rax
  movl (%rax,%r12,4), %edi   ; edi = src_table[r12]
  pop  %r12
  jmp  PLT_MIDIGetSource      ; tail call

src_table: [idx0, idx1, idx2, ...]  ; CoreMIDI source indices

TRAM_DST (20 bytes + 128 bytes table) at 0xC8C:
  (same structure, separate table, jumps to PLT_MIDIGetDestination)
```

**Why separate src and dst tables?** Physical DIN MIDI synths connected to multi-port USB interfaces appear at *different* CoreMIDI indices depending on whether you're looking at sources (MIDI OUT of synth → Mac) or destinations (Mac → MIDI IN of synth). IAC virtual ports and most USB MIDI synths are symmetric (same index for both), but hardware connected via 8-port MIDI interfaces like the ESI M8U XL is not.

**Patch sites:**

| Offset | Original | Patched |
|--------|----------|---------|
| `0x56a3` | `4C 89 E7` + `E8 06 30 00 00` (movq r12,rdi + callq MIDIGetSource) | `E8 70 B5 FF FF` + `90 90 90` (callq TRAM_SRC + 3 NOPs) |
| `0x5831` | `4C 89 FF` + `E8 66 2E 00 00` (movq r15,rdi + callq MIDIGetDestination) | `E8 16 B5 FF FF` + `90 90 90` (callq TRAM_DST + 3 NOPs) |

> The callq offsets above assume `TRAM_SRC=0xBF8` and `TRAM_DST=0xC8C`. If CrossOver is updated and the binary changes, recompute them with `patch_midi_lookup.py`.

---

## Setup

### 1. Create a bottle

In CrossOver, create a new bottle: **Windows 7, 64-bit**. This is the best compatibility target for SoundDiver — Win7 has better Wine support than XP, and the WoW64 layer for 32-bit apps is unavoidable on modern Wine regardless of bottle type.

### 2. Install SoundDiver

Install SoundDiver 3.0.x into the bottle at `C:\Audio\SoundDiver\` (or wherever you prefer).

### 3. Identify your MIDI ports

Run `list_midi_ports.py` to see all CoreMIDI endpoints and their indices:

```bash
python3 list_midi_ports.py
```

**Important:** Run this with all your MIDI hardware powered on and all relevant software running. CoreMIDI indices can shift when devices are added or removed.

For physical DIN MIDI synths connected via a multi-port interface, note which physical port number on the interface each synth is connected to. The interface's ports will appear in CoreMIDI as generic "Port 1", "Port 2", etc.

### 4. Create your port config

Create `~/.sounddiver_midi.conf`. Each line specifies one MIDI port to expose:

```
# Format:
# N           — symmetric port (same CoreMIDI index for source and destination)
# src=X,dst=Y — asymmetric port (physical DIN synths via multi-port interface)

0   # IAC Bus or virtual synth
5   # Another virtual port
26  # USB MIDI Interface (single-port, symmetric)
src=28,dst=30   # Synth on ESI M8U XL port 1 (asymmetric)
src=29,dst=31   # Synth on ESI M8U XL port 2
```

### 5. Apply the patch

```bash
python3 patch_midi_lookup.py
```

This reads `~/.sounddiver_midi.conf`, writes the trampolines and tables into the binary, and patches the constant counts. Re-run it whenever you edit the config.

### 6. Launch SoundDiver

Via CrossOver normally, or via terminal:

```bash
CX_ROOT="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver"
CX_BOTTLE="YourBottleName"
"$CX_ROOT/bin/wine" "C:/Audio/SoundDiver/SoundDiver.exe"
```

---

## Identifying asymmetric port indices

For physical synths on multi-port MIDI interfaces, the source index (Mac receives from synth) and destination index (Mac sends to synth) often differ because CoreMIDI enumerates sources and destinations independently.

**How to identify them:**

Run `list_midi_ports.py` and look for the block of `Port 1`, `Port 2`, ... entries. You will typically see two groups — one for each interface. The 8-entry group (Port 1–8) is the ESI M8U XL or similar 8-port interface. Count from Port 1 to find your synth's port.

Example: if your interface's Port 1 appears at source index 28 and destination index 30:
- source indices: 28, 29, 30... = Port 1, 2, 3...
- destination indices: 30, 31, 32... = Port 1, 2, 3...

Use `src=28,dst=30` for the synth on Port 1, `src=29,dst=31` for Port 2, etc.

If you're unsure, add both candidate indices as separate entries and test which one responds in SoundDiver's MIDI setup.

---

## Known limitations

**MIDI SysEx data loss:** Wine's CoreMIDI driver does not implement the `MIDI_IO_STATUS` flag, which disables flow control for bulk MIDI transfers. On fast connections, SysEx dump data may be partially lost. Mitigation: increase the MIDI transmission delay in SoundDiver's Options → Setup.

**UI size:** SoundDiver has a hardcoded pixel layout from the Win98 era. There is no way to scale it within Wine/CrossOver without modifying macOS display settings.

**Port indices shift:** CoreMIDI re-enumerates endpoints whenever a device connects or disconnects. If you add or remove a virtual MIDI port (e.g. start/stop a virtual instrument or MIDI routing app), all indices may shift. Re-run `list_midi_ports.py` and `patch_midi_lookup.py` if ports disappear from SoundDiver.

**CrossOver updates:** The patch is applied to `winecoreaudio.so` inside the CrossOver app bundle. A CrossOver update will overwrite this file. Keep the backup (`.bak`) and re-run `patch_midi_lookup.py` after updating.

---

## Reverting

```bash
SO="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib/wine/x86_64-unix/winecoreaudio.so"
cp "${SO}.bak" "$SO"
```

---

## Tested configuration (example)

- **Machine:** Apple Silicon Mac (arm64), macOS 15 Sequoia
- **CrossOver:** 26.1.0
- **Wine build:** wine-11.0-8720-g4351038808c
- **Bottle:** Windows 7, 64-bit (`SoundDiver` bottle)
- **SoundDiver:** 3.0.5
- **Total CoreMIDI endpoints:** 42 sources / 43 destinations (all virtual, no physical devices — IAC buses from DAW, virtual instruments, routing apps)

**Active MIDI ports in config:**

| Wine port | Synth | CoreMIDI src | CoreMIDI dst | Notes |
|-----------|-------|-------------|-------------|-------|
| 0 | JV-2080 | 0 | 0 | IAC virtual, symmetric |
| 1 | BassStation | 1 | 1 | IAC virtual |
| 2 | MKS-50 | 3 | 3 | IAC virtual |
| 3 | M-1000 | 4 | 4 | IAC virtual |
| 4 | Pulse Plus | 8 | 8 | IAC virtual |
| 5 | TX-7 | 9 | 9 | IAC virtual |
| 6 | MWAVE | 10 | 10 | IAC virtual |
| 7 | SNOVA | 11 | 11 | IAC virtual |
| 8 | JX-3P | 14 | 14 | IAC virtual |
| 9 | X5D | 15 | 15 | IAC virtual |
| 10 | NORD LEAD 2 | 16 | 16 | IAC virtual |
| 11 | TG33 | 26 | 26 | Via USB MIDI Interface (single-port, symmetric) |
| 12 | K2500 | 28 | 30 | ESI M8U XL physical port 1 (asymmetric) |
| 13 | Extra Synth | 29 | 31 | ESI M8U XL physical port 2 (asymmetric) |

---

## Files

| File | Description |
|------|-------------|
| `list_midi_ports.py` | Lists all CoreMIDI endpoints with indices. Shows which ones Wine currently sees. |
| `patch_midi_lookup.py` | Main patcher. Reads `~/.sounddiver_midi.conf` and patches `winecoreaudio.so`. |
| `patch_winecoreaudio.py` | Earlier patcher (adds `LC_LOAD_WEAK_DYLIB`, restores callq). Superseded by `patch_midi_lookup.py` but kept for reference. |
| `midi_router.c` | Source for a DYLD interposer dylib. **Does not work under Rosetta** — kept for documentation. |
| `midi_limiter.c` | Source for an earlier DYLD interposer. Same limitation. |
| `launch_sounddiver.sh` | Terminal launcher script. |
