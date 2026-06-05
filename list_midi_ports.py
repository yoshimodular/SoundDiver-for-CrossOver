#!/usr/bin/env python3
"""
Lists all CoreMIDI sources and destinations in enumeration order.
Indices 0-7 are what Wine currently exposes to SoundDiver.
"""
import ctypes, ctypes.util

cm = ctypes.CDLL('/System/Library/Frameworks/CoreMIDI.framework/CoreMIDI')
cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')

cm.MIDIGetNumberOfSources.restype      = ctypes.c_uint32
cm.MIDIGetNumberOfDestinations.restype = ctypes.c_uint32
cm.MIDIGetSource.restype               = ctypes.c_uint32
cm.MIDIGetSource.argtypes              = [ctypes.c_uint32]
cm.MIDIGetDestination.restype          = ctypes.c_uint32
cm.MIDIGetDestination.argtypes         = [ctypes.c_uint32]
cm.MIDIObjectGetStringProperty.restype = ctypes.c_int32
cm.MIDIObjectGetStringProperty.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

cf.CFStringGetCStringPtr.restype  = ctypes.c_char_p
cf.CFStringGetCStringPtr.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
cf.CFStringGetCString.restype     = ctypes.c_bool
cf.CFStringGetCString.argtypes    = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFRelease.argtypes             = [ctypes.c_void_p]

# kMIDIPropertyName — load the symbol from the framework
kMIDIPropertyName = ctypes.c_void_p.in_dll(cm, 'kMIDIPropertyName')

def get_name(endpoint):
    name_ref = ctypes.c_void_p(0)
    cm.MIDIObjectGetStringProperty(endpoint, kMIDIPropertyName, ctypes.byref(name_ref))
    if not name_ref.value:
        return "<no name>"
    buf = ctypes.create_string_buffer(256)
    cf.CFStringGetCString(name_ref, buf, 256, 0x08000100)  # kCFStringEncodingUTF8
    cf.CFRelease(name_ref)
    return buf.value.decode('utf-8', errors='replace')

MAX_SHOWN = 8  # ports currently visible to Wine

n_src  = cm.MIDIGetNumberOfSources()
n_dest = cm.MIDIGetNumberOfDestinations()

print(f"\n{'─'*55}")
print(f"  MIDI SOURCES (inputs)   — total: {n_src}")
print(f"{'─'*55}")
for i in range(n_src):
    ep   = cm.MIDIGetSource(i)
    name = get_name(ep)
    flag = " ◀ Wine sees this" if i < MAX_SHOWN else ""
    print(f"  [{i:2d}] {name}{flag}")

print(f"\n{'─'*55}")
print(f"  MIDI DESTINATIONS (outputs) — total: {n_dest}")
print(f"{'─'*55}")
for i in range(n_dest):
    ep   = cm.MIDIGetDestination(i)
    name = get_name(ep)
    flag = " ◀ Wine sees this" if i < MAX_SHOWN else ""
    print(f"  [{i:2d}] {name}{flag}")

print()
