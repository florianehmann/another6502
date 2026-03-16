"""Echo typed characters back into stdout."""  # noqa: INP001

import sys
from functools import partial
from pathlib import Path
from queue import Queue
from threading import Thread

from emulator.cpu import CPU6502, run
from emulator.memory import MemoryBlock, MemoryMap
from emulator.peripherals import TerminalPeripheral, monitor_stdin


def interrupt_hook(cpu: CPU6502, input_queue: Queue[bytes | None], terminal: TerminalPeripheral) -> None:
    """Interrupt hook to handle terminal input."""
    if input_queue.qsize() > 0:
        ch = input_queue.get()
        if ch is None:
            sys.exit(0)
        terminal.receive_input(ch[0])
        cpu.irq()


def main() -> None:  # noqa: D103
    input_queue: Queue[bytes | None] = Queue()
    Thread(target=monitor_stdin, args=(input_queue,)).start()
    terminal = TerminalPeripheral()
    interrupt_hook_with_queue = partial(interrupt_hook, input_queue=input_queue, terminal=terminal)

    ram = MemoryBlock(0xD000)
    rom = MemoryBlock(0x2000)
    with (Path(__file__).parent / "6502_code/bin/rom.bin").open("rb") as f:
        rom_contents = f.read()
    for i, b in enumerate(rom_contents):
        rom.write(i, b)
    memory_map = (MemoryMap()
        .add_block(0x0000, ram)
        .add_block(0xD000, terminal.mmio_block)
        .add_block(0xE000, rom))
    cpu = CPU6502(memory_map)
    run(cpu, interrupt_hook=interrupt_hook_with_queue, max_steps=None, cycles_per_second=1e6)
    print("\r")
    print(f"A  NV-BDIZC\r")
    print(f"{cpu.a:02X} {cpu.status:08b}\r")


if __name__ == "__main__":
    main()
