#!/bin/bash
# launch_sounddiver.sh
# Launches SoundDiver 3.0.5 via CrossOver.
# winecoreaudio.so is patched to cap CoreMIDI ports at 8,
# preventing Wine's MMDRV_Alloc overflow with 43+ virtual MIDI ports.
#
# Logging: pass --log to write Wine debug output to /tmp/sd.log
#   ./launch_sounddiver.sh --log

CX_ROOT="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver"
CX_BOTTLE="SoundDiver"
WINE="$CX_ROOT/bin/wine"
EXE="C:/Audio/SoundDiver/SoundDiver.exe"

export CX_ROOT
export CX_BOTTLE

if [[ "$1" == "--log" ]]; then
    exec "$WINE" --cx-log=/tmp/sd.log "$EXE"
else
    exec "$WINE" "$EXE"
fi
