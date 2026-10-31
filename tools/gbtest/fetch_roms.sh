#!/bin/sh
# Download the c-sp/game-boy-test-roms collection (Blargg, Mooneye, acid2, SameSuite, AGE,
# Mealybug, GBMicrotest, Gambatte, ...) into ./roms.
set -e
cd "$(dirname "$0")"
VERSION=v7.0
mkdir -p roms
curl -sSL -o roms/game-boy-test-roms.zip \
	"https://github.com/c-sp/game-boy-test-roms/releases/download/$VERSION/game-boy-test-roms-$VERSION.zip"
unzip -q -o roms/game-boy-test-roms.zip -d roms
rm roms/game-boy-test-roms.zip
echo "test ROMs in roms/"
