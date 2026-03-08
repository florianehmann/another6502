.import start
.import process_input
.import __STARTUP_LOAD__
.export __STARTUP__ : absolute = 1

.segment "STARTUP"

        ; initialize stack pointer
        ldx #$ff
        txs

        cld
        cli

        jmp start

.segment "ONCE"

nmi_handler:
        rti
reset_handler:
        jmp __STARTUP_LOAD__

.segment "VECTORS"

        .word nmi_handler
        .word reset_handler
        .word process_input
