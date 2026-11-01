#!/bin/sh
# Build a minimal static libmgba (mGBA 0.10.3, GBA and GB cores, no frontends) and the
# gbaharness (Goomba under test) and gbref (reference GB renderer) tools into ./build. Needs git, cmake and a C compiler.
set -e
cd "$(dirname "$0")"
MGBA_VERSION=0.10.3

mkdir -p build
if [ ! -d build/mgba-src ]; then
	git clone --depth 1 --branch "$MGBA_VERSION" https://github.com/mgba-emu/mgba.git build/mgba-src
fi

# CMAKE_POLICY_VERSION_MINIMUM: mGBA 0.10.3 declares a CMake minimum that CMake 4 no longer accepts.
cmake -S build/mgba-src -B build/mgba -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_BUILD_TYPE=Release \
	-DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_SHARED=OFF -DBUILD_STATIC=ON -DBUILD_GL=OFF -DBUILD_GLES2=OFF \
	-DBUILD_GLES3=OFF -DUSE_FFMPEG=OFF -DUSE_PNG=OFF -DUSE_ZLIB=OFF -DUSE_LIBZIP=OFF -DUSE_MINIZIP=OFF \
	-DUSE_SQLITE3=OFF -DUSE_ELF=OFF -DUSE_LUA=OFF -DUSE_EPOXY=OFF -DUSE_DISCORD_RPC=OFF -DUSE_EDITLINE=OFF \
	-DUSE_LZMA=OFF -DM_CORE_GB=ON -DUSE_DEBUGGERS=ON -DUSE_GDB_STUB=OFF -DENABLE_SCRIPTING=OFF
cmake --build build/mgba -j

EXTRA=""
[ "$(uname)" = Darwin ] && EXTRA="-framework CoreFoundation"
cc -O2 -std=c11 -Ibuild/mgba-src/include -Ibuild/mgba/include -DM_CORE_GBA gbaharness.c \
	build/mgba/libmgba.a -lz -lpthread -lm $EXTRA -o build/gbaharness
cc -O2 -std=c11 -Ibuild/mgba-src/include -Ibuild/mgba/include -DM_CORE_GB gbref.c \
	build/mgba/libmgba.a -lz -lpthread -lm $EXTRA -o build/gbref
echo "built build/gbaharness build/gbref"
