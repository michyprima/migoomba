#!/bin/sh
# Builds objl3.gbc (3 sprites per line, as Polished Crystal's music player) and objl4.gbc (a 4th sprite written too: must stay off).
set -e
cd "$(dirname "$0")"
python3 gen.py
for n in 3 4; do
	if [ $n = 4 ]; then def=-DFOUR; else def=; fi
	rgbasm $def -o objl$n.o objl.asm
	rgblink -o objl$n.gbc objl$n.o
	rgbfix -v -C -t OBJL$n -p 0 objl$n.gbc
	rm objl$n.o
done
