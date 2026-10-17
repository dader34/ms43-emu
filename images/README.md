Put a 512 KB full read of an MS43 here (boot block, program and
calibration: flash 0x00000-0x7FFFF), or point MS43_FULL at one (see
ms43emu/image.py):

    stock_512k.bin      an unpatched read, program 430069 (E46 M54B25, US)   (MS43_FULL)
                        sha256 a6aa131f57bf3f76b6239baa79f08500c4540c784ccdea56e9b9f6646451ee55

Nothing in this directory is committed.
