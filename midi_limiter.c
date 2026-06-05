/*
 * midi_limiter.c
 *
 * DYLD interposer for CrossOver/Wine: caps the number of CoreMIDI
 * sources and destinations reported to Wine, preventing MMDRV_Alloc
 * overflow when the Mac has many virtual MIDI ports active.
 *
 * Build:
 *   clang -dynamiclib -o midi_limiter.dylib midi_limiter.c -framework CoreMIDI
 *
 * Use:
 *   DYLD_INSERT_LIBRARIES=/path/to/midi_limiter.dylib \
 *   CX_ROOT=... CX_BOTTLE="SoundDiver" wine "C:/Audio/SoundDiver/SoundDiver.exe"
 */

#include <CoreMIDI/CoreMIDI.h>

#define MAX_MIDI_PORTS 8

/* DYLD_INTERPOSE macro — places a replacement/original pair in the
 * __DATA,__interpose section so dyld swaps the symbol at load time. */
#define DYLD_INTERPOSE(_replacement, _original)                          \
    __attribute__((used)) static struct { const void *r; const void *o; }\
    _interpose_##_original                                               \
    __attribute__((section("__DATA,__interpose"))) = {                   \
        (const void *)_replacement, (const void *)_original             \
    }

static ItemCount my_MIDIGetNumberOfSources(void)
{
    ItemCount n = MIDIGetNumberOfSources();
    return (n > MAX_MIDI_PORTS) ? MAX_MIDI_PORTS : n;
}

static ItemCount my_MIDIGetNumberOfDestinations(void)
{
    ItemCount n = MIDIGetNumberOfDestinations();
    return (n > MAX_MIDI_PORTS) ? MAX_MIDI_PORTS : n;
}

/* Guard: if Wine somehow requests an index beyond our cap, return NULL
 * instead of an invalid endpoint. */
static MIDIEndpointRef my_MIDIGetSource(ItemCount index)
{
    if (index >= MAX_MIDI_PORTS) return 0;
    return MIDIGetSource(index);
}

static MIDIEndpointRef my_MIDIGetDestination(ItemCount index)
{
    if (index >= MAX_MIDI_PORTS) return 0;
    return MIDIGetDestination(index);
}

DYLD_INTERPOSE(my_MIDIGetNumberOfSources,      MIDIGetNumberOfSources);
DYLD_INTERPOSE(my_MIDIGetNumberOfDestinations, MIDIGetNumberOfDestinations);
DYLD_INTERPOSE(my_MIDIGetSource,               MIDIGetSource);
DYLD_INTERPOSE(my_MIDIGetDestination,          MIDIGetDestination);
