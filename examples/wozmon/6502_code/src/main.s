.include "mmio.inc"
.export start

; Variable Locations

xaml    = $24                   ; Examine index low byte.
xamh    = $25                   ; Examine index high byte.
stl     = $26                   ; Store address low byte.
sth     = $27                   ; Store address high byte.
l       = $28                   ; Hex value parsing low digit.
h       = $29                   ; Hex value parsing high digit.
ysav    = $2A                   ; ?
mode    = $2B                   ; $00=xam, $7F=stor, $AE=block xam

in      = $0200                 ; Input buffer, until $027F

kbd     = $D010                 ; PIA.A keyboard input
kbdcr   = $D011                 ; PIA.A keyboard control register
dsp     = $D012                 ; PIA.B display output register
dspcr   = $D013                 ; PIA.B display control register

; Modes

xam     = $00                   ; Examine.
stor    = $7B                   ; Store.
blokxam = $AE                   ; Block examine.

; Constants

bs      = $DF                   ; Backspace
cr      = $8D                   ; Carriage Return
esc     = $9B                   ; ESC key
prompt  = $5C                   ; Prompt character (\)

.segment "CODE"

start:
reset:                          ; Set up control registers for kbd and dsp.
        cld
        cli
        ldy #%0111.1111
        sty dsp
        lda #%1010.0111
        sta kbdcr
        sta dspcr

notcr:                          ; Routine for handling keys that are not cr.
        cmp #bs
        beq backspace
        cmp #esc
        beq escape
        iny                     ; Advance text index.
        bpl nextchar            ; Auto-Escape if > 127.

escape:
        lda #prompt
        jsr echo

getline:
        lda #cr
        jsr echo
        ldy #$01                ; Initialize text index.

backspace:
        dey
        bmi getline             ; Beyond start of line, reinitialize.

nextchar:
        lda kbdcr               ; Key ready?
        bpl nextchar            ; Loop until ready.
        lda kbd
        sta in,Y                ; Add text to buffer.
        jsr echo
        cmp #cr
        bne notcr
        ldy #$FF                ; Reset text index.
        lda #xam
        tax

setstor:
        asl                     ; Leaves $7B if setting in stor mode.

setmode:
        sta mode

blskip:
        iny                     ; Advance text index.

nextitem:
        lda in,Y                ; Get character from buffer.
        cmp #cr
        beq getline
        cmp #$AE                ; "."?
        bcc blskip              ; Skip delimiter.
        beq setmode             ; Set blokxam mode.
        cmp #$BA                ; ":"?
        beq setstor
        cmp #$D2                ; "R"?
        beq run                 ; Run user program.
        stx l                   ; 00 -> l.
        stx h                   ; 00 -> h.
        sty ysav                ; Save Y for comparison.

nexthex:
        lda in,Y                ; Get character for hex test.
        eor #$B0                ; Map digits to $0-9.
        cmp #$0A                ; Digit?
        bcc dig
        adc #$88                ; Map letter "A" - "F" to $FA-$FF.
        cmp #$FA                ; Hex letter?
        bcc nothex              ; No, character not hex.

dig:
        asl
        asl
        asl
        asl
        ldx #$04                ; Shift count.

hexshift:
        asl                     ; Hex digit left, MSB to carry.
        rol l                   ; Rotate into LSD.
        rol h                   ; Rotate into MSD'S.
        dex
        bne hexshift            ; Repeat if not four times.
        iny                     ; Advance text index.
        bne nexthex             ; Always taken. Check next character for hex.

nothex:
        cpy ysav                ; Check if L, H empty (no hex digits).
        beq escape              ; Yes, generate ESC sequence.
        bit mode                ; Test mode byte.
        bvc notstor             ; b6 = 0 for stor, 1 for xam and blokxam.
        lda l                   ; LSD's of hex data.
        sta (stl,X)             ; Store at current store index.
        inc stl                 ; Increment store index.
        bne nextitem            ; Get next item. (no carry).
        inc sth                 ; Add carry to 'store index' high order.

tonextitem:
        jmp nextitem            ; Get next command item

run:
        jmp (xaml)              ; Run at current xam index

notstor:
        bmi xamnext             ; b7 = 0 for xam, 1 for blokxam.
        ldx #$02                ; Byte count.

setadr:
        lda l-1,X               ; Copy hex data to
        sta stl-1,X             ;  'store index'
        sta xaml-1,X            ; And to 'XAM index'.
        dex                     ; Next of two bytes.
        bne setadr

nxtprnt:
        bne prdata              ; ne means no address to print.
        lda #cr
        jsr echo
        lda xamh                ; 'Examine index' high byte.
        jsr prbyte              ; output in hex format.
        lda xaml                ; 'Examine index' low byte.
        jsr prbyte
        lda #$BA                ; ":".
        jsr echo

prdata:
        lda #$A0                ; Blank.
        jsr echo
        lda (xaml,X)            ; Get data byte at 'examine index'.
        jsr prbyte

xamnext:
        stx mode                ; 0 -> mode (XAM mode).
        lda xaml
        cmp l                   ; Compare 'examine index' to hex data.
        lda xamh
        sbc h
        bcs tonextitem          ; Not less, so no more data to output.
        inc xaml
        bne mod8chk             ; Increment 'examine index'.
        inc xamh

mod8chk:
        lda xaml                ; Check low 'examine index' byte.
        and #$07                ;   For MOD 8 = 0.
        bpl nxtprnt             ; Always taken.

prbyte:
        pha                     ; Save A for LSD.
        lsr
        lsr
        lsr                     ; MSD to LSD position.
        lsr
        jsr prhex               ; Output hex digit.
        pla                     ; Restore A.

prhex:
        and #$0F                ; Mask LSD for hex print.
        ora #$B0                ; Add "0" digit.
        cmp #$BA                ; Digit?
        bcc echo                ; Yes, output it.
        adc #$06                ; Add offset for letters.

echo:
        bit dsp                 ; DA bit (B7) cleared yet?
        bmi echo                ; No, wait for display.
        sta dsp                 ; Output character. Sets DA.
        rts
