"""Memory for running the CPU."""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Self, override

logger = logging.getLogger(__name__)

class Memory(ABC):
    """Abstract interface for computer memory."""

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of bytes in the memory object."""

    @abstractmethod
    def read(self, address: int) -> int:
        """Return the byte at the given memory location.

        Args:
            address: Memory location to read byte from.
            value: Value of the byte. Anything below the eight most significant bits is discarded by a bit mask.

        Returns:
            value: Value of byte read from memory.

        Raises:
            IndexError: If address is outside of memory.

        """

    @abstractmethod
    def write(self, address: int, value: int) -> None:
        """Write a value to a memory location.

        Args:
            address: Memory location to write the byte to.
            value: Value of the byte. Anything below the eight most significant bits is discarded by a bit mask.

        Raises:
            IndexError: If address is outside of memory.

        """


class MemoryBlock(Memory):
    """Simple block of contiguous memory of configurable size."""

    def __init__(self, size: int = 65536) -> None:
        """Initialize empty memory of given size.

        Args:
            size: Number of bytes in the memory.

        """
        super().__init__()
        self.mem = bytearray(size)

    def _check_address_in_bounds(self, address: int) -> bool:
        """Check if memory address is within the bound of this memory."""
        if not (0 <= address < len(self.mem)):
            msg = f"Address {address:04X} out of memory range."
            logger.error(msg)
            return False
        return True

    @override
    def __len__(self) -> int:
        return len(self.mem)

    @override
    def read(self, address: int) -> int:
        if not self._check_address_in_bounds(address):
            return 0
        return self.mem[address]

    @override
    def write(self, address: int, value: int) -> None:
        if not self._check_address_in_bounds(address):
            return
        self.mem[address] = value & 0xff

    def write_bytes(self, start_address: int, sequence: bytes) -> None:
        """Write a sequence of bytes to a memory region.

        Args:
            start_address: First memory address to be overwritten by `sequence`.
            sequence: Sequence of bytes to write to memory region.

        Raises:
            IndexError: If sequence at specified location exceeds the bounds of the memory.

        """
        self._check_address_in_bounds(start_address)
        self._check_address_in_bounds(start_address + len(sequence) - 1)
        self.mem[start_address:start_address + len(sequence) - 1] = sequence

    def write_bytes_hex(self, start_address: int, sequence: str) -> None:
        """Write a sequence of bytes written as a string of hexadecimal digits to a memory region.

        Args:
            start_address: First memory address to be overwritten by `sequence`.
            sequence: Sequence of bytes written ad hexadecimal digits to write to memory region.

        Raises:
            IndexError: If sequence at specified location exceeds the bounds of the memory.

        """
        self.write_bytes(start_address, bytes.fromhex(sequence))


@dataclass
class MemoryMapRegion:
    """One memory region entry in a `MemoryMap`."""

    offset: int
    """Offset on the region within the address space of the memory map.

    This is the first address in the memory map that falls into this region.
    """

    memory: Memory
    """Reference to the `Memory` object backing this region."""

    def __contains__(self, address: int) -> bool:
        """Check if the region contains a given address."""
        return address >= self.offset and address <= self.top

    @property
    def top(self) -> int:
        """Highest address within the memory region."""
        return self.offset + len(self.memory) - 1

    def overlaps(self, other: Self) -> bool:
        """Check if two memory regions overlap."""
        return other.offset in self or other.top in self


class MMIORegister(Memory):
    """Memory address that contains Memory-Mapped I/O (MMIO) functionality."""

    def __init__(
        self, read_callback: Callable[[], int] = lambda: 0,
        write_callback: Callable[[int], None] = lambda _: None,
    ) -> None:
        """Initialize MMIO register with callbacks functions that are called when a read or write occurs.

        Args:
            read_callback: Function that is called when the register is read from.
            write_callback: Function that is called when the register is written to.

        """
        super().__init__()
        self.read_callback = read_callback
        self.write_callback = write_callback

    @override
    def __len__(self) -> int:
        return 1

    @override
    def read(self, address: int) -> int:
        return self.read_callback()

    @override
    def write(self, address: int, value: int) -> None:
        self.write_callback(value)


class MemoryMap(Memory):
    """Memory map of multiple components."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__()
        self.regions: list[MemoryMapRegion] = []

    def add_block(self, offset: int, block: Memory) -> Self:
        """Add a memory block to the map at a given offset address.

        If `offset` is 0x0100, the the first byte within the block can be found at address 0x0100 within the memory map.
        """
        region = MemoryMapRegion(offset, block)
        if any(region.overlaps(r) for r in self.regions):
            msg = "Memory region overlaps existing region in memory map."
            raise ValueError(msg)
        self.regions.append(region)
        return self

    def get_containing_region(self, address: int) -> MemoryMapRegion | None:
        """Return the region containing `address` or None."""
        try:
            return next(r for r in self.regions if address in r)
        except StopIteration:
            return None

    @override
    def __len__(self) -> int:
        top = max(r.top for r in self.regions)
        return top + 1

    @override
    def read(self, address: int) -> int:
        region = self.get_containing_region(address)
        if region is None:
            logger.warning(f"Tried to read address 0x{address:04x} that is not part of memory map.")
            return 0
        return region.memory.read(address - region.offset)

    @override
    def write(self, address: int, value: int) -> None:
        region = self.get_containing_region(address)
        if region is None:
            logger.warning(f"Tried to write to address 0x{address:04x} that is not part of memory map.")
            return None
        return region.memory.write(address - region.offset, value)

