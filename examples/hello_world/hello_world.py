"""Generate a Hello World output message."""  # noqa: INP001

from emulator.cpu import CPU6502, run
from emulator.memory import MemoryBlock, MemoryMap
from emulator.peripherals import TerminalPeripheral

# .ORG $1000
#
# ; MMIO register for writing to terminal
# TERMOUT = $d001
#
# JMP START
#
# ; data section
#
# MSG:
#         .ASCII "Hello, World!"
#         .BYTE $0A ; newline
# MSG_END:
#
# ; text section
#
# START:
#         LDX #0
# !       LDA MSG,X
#         STA TERMOUT
#         INX
#         CPX #MSG_END-MSG
#         BNE !-
#         BRK
program = bytes.fromhex("""
4C 11 10 48 65 6C 6C 6F
2C 20 57 6F 72 6C 64 21
0A A2 00 BD 03 10 8D 01
D0 E8 E0 0E D0 F5 00
""")


if __name__ == "__main__":
    terminal = TerminalPeripheral()
    program_memory = MemoryBlock(0x1000)
    program_memory.write_bytes(0, program)
    vectors = MemoryBlock(6)
    vectors.write_bytes(0, bytes.fromhex("00 10 00 10 00 10"))
    system_memory = (MemoryMap()
        .add_block(0x0000, MemoryBlock(0x1000))
        .add_block(0x1000, program_memory)
        .add_block(0xd000, terminal.mmio_block)
        .add_block(0xfffa, vectors))

    cpu = CPU6502(system_memory)
    run(cpu)
