; Per-line sprite test: sprites 0-2 are moved on every line from the STAT HBlank
; interrupt, as Polished Crystal's music player does: Y = LY+17 shows tile row 0 on the next line
; (sprite 2 uses Y-4, row 4), X comes from a per-line table and X = 0 hides the sprite. The tables
; (gen.py) put short and long segments against solid BG bars, the screen edges and 1-line gaps.
; With -DFOUR sprite 3 is written on every line too (X = 0), which must keep an emulator's per-line
; sprite drawing off. The scene is the same every frame. CGB and DMG.

DEF rLCDC EQU $FF40
DEF rSTAT EQU $FF41
DEF rLY   EQU $FF44
DEF rBGP  EQU $FF47
DEF rOBP0 EQU $FF48
DEF rBCPS EQU $FF68
DEF rBCPD EQU $FF69
DEF rOCPS EQU $FF6A
DEF rOCPD EQU $FF6B
DEF rIF   EQU $FF0F
DEF rIE   EQU $FFFF

SECTION "vblank", ROM0[$40]
	jp VBlank

SECTION "stat", ROM0[$48]
	; in HBlank of line L: the sprites' entries for line L+1. Registers are free (main loop only halts)
	ldh a,[rLY]
	inc a
	ld e,a
	ld d,HIGH(XT0)
	add 16
	ld hl,$FE00
	ld [hl+],a		; Y0
	ld b,a
	ld a,[de]
	ld [hl],a		; X0
	ld l,4
	ld [hl],b		; Y1
	inc l
	inc d
	ld a,[de]
	ld [hl],a		; X1
	ld l,8
	ld a,b
	sub 4
	ld [hl+],a		; Y2: row 4
	inc d
	ld a,[de]
	ld [hl],a		; X2
IF DEF(FOUR)
	ld l,13
	ld [hl],0		; X3: a 4th sprite written on every line (kept hidden: no time to move it)
ENDC
	reti

SECTION "header", ROM0[$100]
	nop
	jp Start
	ds $150 - @, 0

SECTION "main", ROM0
Start:
	di
	ld sp,$E000
	ldh [$FF80],a		; $11 on CGB
.wait:	ldh a,[rLY]
	cp 144
	jr nz,.wait
	xor a
	ldh [rLCDC],a

	ld hl,$8000		; VRAM bank 0 and OAM cleared
	ld bc,$2000
	call Clear
	ld hl,$FE00
	ld bc,$A0
	call Clear

	ld hl,$8010		; tile 1: solid colour 3
	ld b,16
	ld a,$FF
.t1:	ld [hl+],a
	dec b
	jr nz,.t1
	ld hl,$8020		; tile 2: rows 0 and 4 colour 1, the others colour 2
	ld b,0
.t2:	ld a,b
	and a
	jr z,.c1
	cp 4
	jr z,.c1
	xor a
	ld [hl+],a
	cpl
	ld [hl+],a
	jr .n2
.c1:	ld a,$FF
	ld [hl+],a
	xor a
	ld [hl+],a
.n2:	inc b
	ld a,b
	cp 8
	jr nz,.t2

	ld a,1			; map rows 0-1 (lines 0-15) and 14 (112-119): tile 1
	ld hl,$9800
	ld bc,64
	call Fill
	ld hl,$9800 + 14*32
	ld bc,32
	call Fill

	ld a,$E4
	ldh [rBGP],a
	ldh [rOBP0],a
	ldh a,[$FF80]
	cp $11
	jr nz,.dmg
	ld a,$80		; CGB: BG 0 white, 3 black; OBJ 0 1 red, 2 blue
	ldh [rBCPS],a
	ld hl,BgPal
	ld b,8
.bp:	ld a,[hl+]
	ldh [rBCPD],a
	dec b
	jr nz,.bp
	ld a,$80
	ldh [rOCPS],a
	ld b,8
.op:	ld a,[hl+]
	ldh [rOCPD],a
	dec b
	jr nz,.op
.dmg:
	call VBlankSprites
	ld a,$08		; STAT: HBlank interrupt
	ldh [rSTAT],a
	ld a,$03
	ldh [rIE],a
	xor a
	ldh [rIF],a
	ld a,$93		; LCD on, tiles at $8000, 8x8 sprites on, BG on
	ldh [rLCDC],a
	ei
.loop:	halt
	jr .loop

VBlank:
	call VBlankSprites
	reti

VBlankSprites:			; the sprites' entries for line 0
	ld a,16
	ld [$FE00],a
	ld [$FE04],a
	ld a,[XT0]
	ld [$FE01],a
	ld a,[XT1]
	ld [$FE05],a
	ld a,12
	ld [$FE08],a
	ld a,[XT2]
	ld [$FE09],a
	ld a,2
	ld [$FE02],a
	ld [$FE06],a
	ld [$FE0A],a
	ret

Clear:	xor a
Fill:	ld d,a
.f:	ld a,d
	ld [hl+],a
	dec bc
	ld a,b
	or c
	jr nz,.f
	ret

BgPal:	dw $7FFF, $5294, $294A, $0000
ObjPal:	dw $7FFF, $001F, $7C00, $0000

INCLUDE "objl_tables.inc"
