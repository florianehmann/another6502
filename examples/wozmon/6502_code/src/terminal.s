.include "constants.inc"
.include "mmio.inc"
.include "variables.inc"
.export process_input, terminal_output

ascii_bs    = $7F

; Process an incoming character in the MMIO register of the terminal peripheral
; by mapping keycodes from ASCII to Apple I keycodes and copying them addresses
; for wozmon to read.
process_input:
        lda TERMIN
        cmp #ascii_bs
        bne map_non_bs
        lda #bs                 ; replace with Apple I code for backspace.
        jmp code_map_done
map_non_bs:
        ora #$80

        ; Store mapped byte in register for wozmon to pick up.
        sta kbd

        ; Set B7 in kbdcr to indicate to wozmon that new character is
        ; available.
        lda kbdcr
        ora #$80
        sta kbdcr
code_map_done:
        rti

; Process an outgoing character and output it to the terminal peripheral
terminal_output:
        lda dsp
        cmp #bs
        beq output_bs
        cmp prompt
        beq write_output
        jmp output_other
output_bs:
        lda #ascii_bs
        jmp write_output
output_other:
        and #$7F                ; clear B7.
write_output:
        sta TERMOUT
        rts

