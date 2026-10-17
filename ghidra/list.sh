#!/bin/sh
# ghidra/list.sh 0E1192:80 0DFE0A:60   -> listing of each range (ms43_430069 project in ~)
H=/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless
D=$(cd "$(dirname "$0")" && pwd)
$H ~ ms43_430069 -process stock_512k.bin -noanalysis -scriptPath "$D/scripts" -postScript Listing.java "$@" 2>&1 \
  | sed -n 's/^INFO  Listing.java> \(.*\) (GhidraScript)  $/\1/p'
