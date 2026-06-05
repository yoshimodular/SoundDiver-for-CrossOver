/*
 * midi_router.c
 *
 * CoreMIDI port router for SoundDiver/Wine on macOS.
 * Loaded as a static dependency of winecoreaudio.so (LC_LOAD_WEAK_DYLIB),
 * so it works under Rosetta without DYLD_INSERT_LIBRARIES.
 *
 * Config: ~/.sounddiver_midi.conf
 *   One CoreMIDI port index per line. Lines starting with # are comments.
 *   Same indices used for both sources (MIDI IN) and destinations (MIDI OUT).
 *   If the file is absent, falls back to the first 8 ports.
 *
 * Build:
 *   clang -arch x86_64 -dynamiclib \
 *         -install_name @loader_path/midi_router.dylib \
 *         -o midi_router.dylib midi_router.c -framework CoreMIDI
 */

#include <CoreMIDI/CoreMIDI.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_PORTS 64

static int  port_map[MAX_PORTS];
static int  port_count   = 0;
static int  initialized  = 0;

static void load_config(void)
{
    if (initialized) return;
    initialized = 1;

    const char *home = getenv("HOME");
    if (!home) home = "/tmp";

    char path[512];
    snprintf(path, sizeof(path), "%s/.sounddiver_midi.conf", home);

    FILE *f = fopen(path, "r");
    if (!f) {
        for (int i = 0; i < 8; i++) port_map[i] = i;
        port_count = 8;
        return;
    }

    char line[256];
    while (fgets(line, sizeof(line), f) && port_count < MAX_PORTS) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\n' || *p == '\r' || *p == '\0') continue;
        port_map[port_count++] = atoi(p);
    }
    fclose(f);

    if (port_count == 0) {
        for (int i = 0; i < 8; i++) port_map[i] = i;
        port_count = 8;
    }
}

/* Clamp to actually available CoreMIDI endpoints */
static int effective_count_src(void)
{
    load_config();
    ItemCount real = MIDIGetNumberOfSources();
    int n = 0;
    for (int i = 0; i < port_count; i++)
        if ((ItemCount)port_map[i] < real) n++;
    return n;
}

static int effective_count_dst(void)
{
    load_config();
    ItemCount real = MIDIGetNumberOfDestinations();
    int n = 0;
    for (int i = 0; i < port_count; i++)
        if ((ItemCount)port_map[i] < real) n++;
    return n;
}

/* — interposed functions — */

static ItemCount my_MIDIGetNumberOfSources(void)
{
    return (ItemCount)effective_count_src();
}

static ItemCount my_MIDIGetNumberOfDestinations(void)
{
    return (ItemCount)effective_count_dst();
}

static MIDIEndpointRef my_MIDIGetSource(ItemCount wine_index)
{
    load_config();
    ItemCount real = MIDIGetNumberOfSources();
    /* find the wine_index-th valid entry */
    int found = 0;
    for (int i = 0; i < port_count; i++) {
        if ((ItemCount)port_map[i] >= real) continue;
        if ((ItemCount)found == wine_index) return MIDIGetSource(port_map[i]);
        found++;
    }
    return 0;
}

static MIDIEndpointRef my_MIDIGetDestination(ItemCount wine_index)
{
    load_config();
    ItemCount real = MIDIGetNumberOfDestinations();
    int found = 0;
    for (int i = 0; i < port_count; i++) {
        if ((ItemCount)port_map[i] >= real) continue;
        if ((ItemCount)found == wine_index) return MIDIGetDestination(port_map[i]);
        found++;
    }
    return 0;
}

/* DYLD interpose table */
#define INTERPOSE(_new, _orig)                                              \
    __attribute__((used))                                                   \
    static struct { const void *r; const void *o; }                         \
    _interpose_##_orig                                                      \
    __attribute__((section("__DATA,__interpose"))) =                        \
        { (const void *)_new, (const void *)_orig }

INTERPOSE(my_MIDIGetNumberOfSources,      MIDIGetNumberOfSources);
INTERPOSE(my_MIDIGetNumberOfDestinations, MIDIGetNumberOfDestinations);
INTERPOSE(my_MIDIGetSource,               MIDIGetSource);
INTERPOSE(my_MIDIGetDestination,          MIDIGetDestination);
