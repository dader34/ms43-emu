#!/bin/sh
# ghidra/decomp.sh 0E09D2   -> listing + decompiled C of the function there (ms43_430069 project in ~)
H=/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless
D=$(cd "$(dirname "$0")" && pwd)
$H ~ ms43_430069 -process stock_512k.bin -noanalysis -scriptPath "$D/scripts" -postScript DecompOne.java "$1" 2>&1 \
  | awk '/DecompOne.java> /{sub(/^INFO  DecompOne.java> /,""); sub(/ \(GhidraScript\)  $/,""); p=1} /^INFO  REPORT|^INFO  ANALYZING|^INFO  Packed/{p=0} p'
