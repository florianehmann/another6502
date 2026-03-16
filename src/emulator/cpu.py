"""CPU Logic."""

import enum
import logging
import time
from collections.abc import Callable
from functools import partial
from typing import Any, ClassVar, Literal

from emulator.memory import Memory
from emulator.utils import assert_never, dec_to_bcd

logger = logging.getLogger(__name__)


class AddressingMode(enum.Enum):
    """Addressing mode of a 6502 instruction."""

    IMMEDIATE = enum.auto()
    ZERO_PAGE = enum.auto()
    ZERO_PAGE_X = enum.auto()
    ZERO_PAGE_Y = enum.auto()
    ABSOLUTE = enum.auto()
    ABSOLUTE_X = enum.auto()
    ABSOLUTE_Y = enum.auto()
    INDIRECT_X = enum.auto()
    INDIRECT_Y = enum.auto()


class StepResult(enum.Enum):
    """Result of a CPU fetch/execute step."""

    NORMAL = enum.auto()
    BRK = enum.auto()


def opcode(opcode: int, **kwargs: Any) -> Callable[..., Callable[..., None]]:  # noqa: ANN401
    """Register a set of arguments to an opcode."""
    def decorator(func: Callable[..., None]) -> Callable[..., None]:
        if not hasattr(func, "opcodes"):
            func.opcodes = []  # type: ignore[reportFunctionMemberAccess]
        if opcode in (op for op, _ in func.opcodes):  # type: ignore[reportFunctionMemberAccess]
            msg = f"Opcode 0x{opcode:02x} has already been registered for this function."
            raise ValueError(msg)
        func.opcodes.append((opcode, kwargs))  # type: ignore[reportUnknownMemberType]
        return func
    return decorator


class CPU6502:
    """A behavioral model of the MOS6502."""

    STATUS_C = 0
    STATUS_Z = 1
    STATUS_I = 2
    STATUS_D = 3
    STATUS_B = 4
    STATUS_V = 6
    STATUS_N = 7

    STACK_ROOT = 0x0100

    NMI_VECTOR = 0xfffa
    RST_VECTOR = 0xfffc
    IRQ_VECTOR = 0xfffe

    LOAD_CYCLE_COUNTS: ClassVar[dict[AddressingMode, int]] = {
        AddressingMode.IMMEDIATE: 2,
        AddressingMode.ZERO_PAGE: 3,
        AddressingMode.ZERO_PAGE_X: 4,
        AddressingMode.ZERO_PAGE_Y: 4,
        AddressingMode.ABSOLUTE: 4,
        AddressingMode.ABSOLUTE_X: 4,
        AddressingMode.ABSOLUTE_Y: 4,
        AddressingMode.INDIRECT_X: 6,
        AddressingMode.INDIRECT_Y: 5,
    }

    LOAD_EXTRA_CYCLE_MODES: ClassVar[tuple[AddressingMode, ...]] = (
        AddressingMode.ABSOLUTE_X,
        AddressingMode.ABSOLUTE_Y,
        AddressingMode.INDIRECT_Y,
    )

    STORE_CYCLE_COUNTS: ClassVar[dict[AddressingMode, int]] = {
        AddressingMode.ZERO_PAGE: 3,
        AddressingMode.ZERO_PAGE_X: 4,
        AddressingMode.ZERO_PAGE_Y: 4,
        AddressingMode.ABSOLUTE: 4,
        AddressingMode.ABSOLUTE_X: 5,
        AddressingMode.ABSOLUTE_Y: 5,
        AddressingMode.INDIRECT_X: 6,
        AddressingMode.INDIRECT_Y: 6,
    }

    UNARY_CYCLE_COUNTS: ClassVar[dict[AddressingMode, int]] = {
        AddressingMode.ZERO_PAGE: 5,
        AddressingMode.ZERO_PAGE_X: 6,
        AddressingMode.ABSOLUTE: 6,
        AddressingMode.ABSOLUTE_X: 7,
    }

    BINARY_CYCLE_COUNTS: ClassVar[dict[AddressingMode, int]] = {
        AddressingMode.IMMEDIATE: 2,
        AddressingMode.ZERO_PAGE: 3,
        AddressingMode.ZERO_PAGE_X: 4,
        AddressingMode.ZERO_PAGE_Y: 4,
        AddressingMode.ABSOLUTE: 4,
        AddressingMode.ABSOLUTE_X: 4,
        AddressingMode.ABSOLUTE_Y: 4,
        AddressingMode.INDIRECT_X: 6,
        AddressingMode.INDIRECT_Y: 5,
    }

    BINARY_EXTRA_CYCLE_MODES: ClassVar[tuple[AddressingMode, ...]] = (
        AddressingMode.ABSOLUTE_X,
        AddressingMode.ABSOLUTE_Y,
        # INDIRECT_Y only on some
    )

    def __init__(self, memory: Memory) -> None:
        """Initialize a CPU with memory."""
        # Registers
        self.a: int = 0
        self.x: int = 0
        self.y: int = 0
        self.pc: int = 0
        self.sp: int = 0xff
        self.status: int = 0
        self.cycles: int = 0

        self.memory = memory
        self.opcodes = self.build_opcode_table()

        # initial values for the status register
        self.status |= (1 << self.STATUS_Z)
        self.status |= (1 << self.STATUS_I)
        self.status |= (1 << 5)  # unused bit of the status register is usually set

    def build_opcode_table(self) -> dict[int, Callable[[], None]]:
        """Return a map between opcode and method that contains the logic for the instruction."""
        opcode_table: dict[int, Callable[[], None]] = {}
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            func = getattr(attr, "__func__", attr)

            if not hasattr(func, "opcodes"):
                continue

            for opcode, kwargs in func.opcodes:
                if opcode in opcode_table:
                    msg = f"Opcode 0x{opcode:02x} has already been registered."
                    raise ValueError(msg)
                opcode_table[opcode] = partial(attr, **kwargs)

        return opcode_table

    def step(self) -> StepResult:
        """Step one CPU tick.

        This function executes the next CPU instruction.
        """
        opcode = self.memory.read(self.pc)
        if opcode not in self.opcodes:
            logger.warning(f"Unhandled opcode at ${self.pc:04x}")
        self.pc += 1
        handler = self.opcodes.get(opcode, self.brk)
        handler()

        if self.status & (1 << self.STATUS_I) > 0 and self.status & (1 << self.STATUS_B) > 0:
            self.status &= ~(1 << self.STATUS_B)
            return StepResult.BRK
        return StepResult.NORMAL

    def update_zero_flag(self, result: int) -> None:
        """Update the zero (Z) flag of the status register based on the result of an operation.

        Args:
            result: Byte resulting from an operation that updates the status register.

        """
        self.status &= ~(1 << self.STATUS_Z)
        self.status |= (result == 0) << self.STATUS_Z

    def update_overflow_flag(self, a_initial: int, operand: int, result: int) -> None:
        """Update the overflow (V) flag of the status register based on the result of an operation.

        Args:
            a_initial: Accumulator value before operation.
            operand: Operand of potentially overflowing operation.
            result: Accumulator value after operation.

        """
        inputs_same_sign = ~(a_initial ^ operand) & 0x80
        result_sign_different_from_inputs = (a_initial ^ result) & 0x80
        v = inputs_same_sign & result_sign_different_from_inputs
        v = (v >> 7) & 1

        self.status &= ~(1 << self.STATUS_V)
        self.status |= v << self.STATUS_V

    def update_negative_flag(self, result: int) -> None:
        """Update the negative (N) flag of the status register based on the result of an operation.

        Args:
            result: Byte resulting from an operation that updates the status register.

        """
        result_msb = (result >> 7) & 0x01
        self.status &= ~(1 << self.STATUS_N)
        self.status |= result_msb << self.STATUS_N

    def resolve_address(self, mode: AddressingMode) -> tuple[int, bool]:  # noqa: PLR0915
        """Resolve the effective address for a given addressing mode.

        Args:
            mode: The addressing mode to resolve.

        Returns:
            (addr, page_boundary_crossed): The effective memory address and if a page boundary
            has been crossed by indexing.

        """
        addr: int
        page_boundary_crossed = False
        match mode:
            case AddressingMode.IMMEDIATE:
                addr = self.pc
                self.pc += 1
            case AddressingMode.ZERO_PAGE:
                addr = self.memory.read(self.pc)
                self.pc += 1
            case AddressingMode.ZERO_PAGE_X:
                zero_page_location = self.memory.read(self.pc)
                addr = (zero_page_location + self.x) & 0xff
                self.pc += 1
            case AddressingMode.ZERO_PAGE_Y:
                zero_page_location = self.memory.read(self.pc)
                addr = (zero_page_location + self.y) & 0xff
                self.pc += 1
            case AddressingMode.ABSOLUTE:
                addr_base_lo = self.memory.read(self.pc)
                addr_base_hi = self.memory.read(self.pc + 1)
                addr = (addr_base_hi << 8) | addr_base_lo
                self.pc += 2
            case AddressingMode.ABSOLUTE_X:
                addr_base_lo = self.memory.read(self.pc)
                addr_base_hi = self.memory.read(self.pc + 1)
                addr_base = (addr_base_hi << 8) | addr_base_lo
                addr = (addr_base + self.x) & 0xffff
                page_boundary_crossed = (addr_base & 0xff00) != (addr & 0xff00)
                self.pc += 2
            case AddressingMode.ABSOLUTE_Y:
                addr_base_lo = self.memory.read(self.pc)
                addr_base_hi = self.memory.read(self.pc + 1)
                addr_base = (addr_base_hi << 8) | addr_base_lo
                addr = (addr_base + self.y) & 0xffff
                page_boundary_crossed = (addr_base & 0xff00) != (addr & 0xff00)
                self.pc += 2
            case AddressingMode.INDIRECT_X:
                addr_zp = (self.memory.read(self.pc) + self.x) & 0xff
                addr_indirect_lo = self.memory.read(addr_zp)
                addr_indirect_hi = self.memory.read((addr_zp + 1) & 0xff)
                addr = (addr_indirect_hi << 8) | addr_indirect_lo
                self.pc += 1
            case AddressingMode.INDIRECT_Y:
                addr_zp = self.memory.read(self.pc)
                addr_base_lo = self.memory.read(addr_zp)
                addr_base_hi = self.memory.read((addr_zp + 1) & 0xff)
                addr_base = (addr_base_hi << 8) | addr_base_lo
                addr = (addr_base + self.y) & 0xffff
                page_boundary_crossed = (addr_base & 0xff00) != (addr & 0xff00)
                self.pc += 1
            case _:
                assert_never(mode)

        return addr, page_boundary_crossed

    def push_byte_to_stack(self, byte: int) -> None:
        """Push a byte to the stack and update stack pointer.

        Note: This method does not update the status register or perform underflow checks.

        Args:
            byte: Byte to push onto the stack.

        """
        self.memory.write(self.STACK_ROOT + self.sp, byte)
        self.sp = (self.sp - 1) & 0xff

    def pull_byte_from_stack(self) -> int:
        """Pull a byte from the stack and update the stack pointer.

        Note: This method does not update the status register or perform overflow checks.

        Returns:
            byte: Byte pulled from the stack.

        """
        self.sp = (self.sp + 1) & 0xff
        return self.memory.read(self.STACK_ROOT + self.sp)

    def _interrupt(self, interrupt_type: Literal["maskable", "non-maskable", "break"]) -> None:
        """Initiate an interrupt.

        This function is called in between CPU steps, so in this state the program counter points to the opcode byte of
        the next instruction to be executed.
        """
        if interrupt_type == "maskable":
            interrupt_disable_flag = self.status & (1 << self.STATUS_I) > 0
            if interrupt_disable_flag:
                return

        if interrupt_type == "break":
            self.status |= (1 << self.STATUS_B)

        self.cycles += 7
        pc_lo = self.pc & 0xff
        pc_hi = (self.pc >> 8) & 0xff
        self.push_byte_to_stack(pc_hi)
        self.push_byte_to_stack(pc_lo)
        self.push_byte_to_stack(self.status)

        self.status |= (1 << self.STATUS_I)

        vector = self.IRQ_VECTOR
        if interrupt_type == "non-maskable":
            vector = self.NMI_VECTOR

        isr_lo = self.memory.read(vector)
        isr_hi = self.memory.read(vector + 1)
        self.pc = (isr_hi << 8) | isr_lo

    def irq(self) -> None:
        """Issue an Interrupt ReQuest (IRQ) to the CPU."""
        self._interrupt("maskable")

    def nmi(self) -> None:
        """Issue a Non-Maskable Interrupt (NMI) to the CPU."""
        self._interrupt("non-maskable")

    # System instructions

    @opcode(0x10, flag_index=STATUS_N, flag_value=0)
    @opcode(0x30, flag_index=STATUS_N, flag_value=1)
    @opcode(0x50, flag_index=STATUS_V, flag_value=0)
    @opcode(0x70, flag_index=STATUS_V, flag_value=1)
    @opcode(0x90, flag_index=STATUS_C, flag_value=0)
    @opcode(0xb0, flag_index=STATUS_C, flag_value=1)
    @opcode(0xd0, flag_index=STATUS_Z, flag_value=0)
    @opcode(0xf0, flag_index=STATUS_Z, flag_value=1)
    def branch(self, flag_index: int, flag_value: int) -> None:
        """Branch to relative address if specified flag is set or clear.

        Args:
            flag_index: Index of the flag in the status register to check.
            flag_value: The value the flag should have for the branch to be taken (0 or 1).

        """
        int8_min = 0x80
        should_branch = ((self.status >> flag_index) & 1) == flag_value
        if should_branch:
            offset = self.memory.read(self.pc)
            self.pc += 1

            # convert negative offsets to signed values
            if offset >= int8_min:
                offset -= 0x100

            # jump
            old_pc = self.pc
            self.pc = (self.pc + offset) & 0xffff
            self.cycles += 3

            # add another cycle if page boundary is crossed
            if (old_pc & 0xff00) != (self.pc & 0xff00):
                self.cycles += 1
        else:
            self.pc += 1
            self.cycles += 2

    @opcode(0x00)
    def brk(self) -> None:
        """Execute the BReaK (BRK) instruction."""
        self.pc += 1
        self._interrupt("break")

    @opcode(0x4c, mode="absolute")
    @opcode(0x6c, mode="indirect")
    def jmp(self, mode: Literal["absolute", "indirect"]) -> None:
        """Execute the JuMP (JMP) instruction.

        Note: This implementation correctly reproduces the hardware bug of the original NMOS 6502 in which the high byte
        of the target address is fetched from the beginning of the same page when the low byte is 0xff.
        """
        addr_lo = self.memory.read(self.pc)
        addr_hi = self.memory.read((self.pc + 1) & 0xffff)
        addr = (addr_hi << 8) | addr_lo

        if mode == "absolute":
            self.pc = addr
        elif mode == "indirect":
            addr_lo = self.memory.read(addr)
            # this reproduces the NMOS 6502's hardware bug
            addr_incremented = (addr & 0xff00) | ((addr + 1) & 0x00ff)
            addr_hi = self.memory.read(addr_incremented)
            self.pc = (addr_hi << 8) | addr_lo
        else:
            msg = f"Invalid mode {mode} for jmp."
            raise ValueError(msg)

        self.cycles += 3 if mode == "absolute" else 5

    @opcode(0x20)
    def jsr(self) -> None:
        """Execute the Jump to SubRoutine (JSR) instruction."""
        sr_addr_lo = self.memory.read(self.pc)
        sr_addr_hi = self.memory.read((self.pc + 1) & 0xffff)
        sr_addr = (sr_addr_hi << 8) | sr_addr_lo

        # point to last byte of jsr instruction
        return_addr = (self.pc + 1) & 0xffff
        return_addr_lo = return_addr & 0xff
        return_addr_hi = (return_addr >> 8) & 0xff

        self.push_byte_to_stack(return_addr_hi)
        self.push_byte_to_stack(return_addr_lo)

        self.pc = sr_addr
        self.cycles += 6

    @opcode(0xea)
    def nop(self) -> None:
        """Execute No OPeration (NOP) instruction."""
        self.cycles += 2

    @opcode(0x40)
    def rti(self) -> None:
        """Execute the ReTurn from Interrupt (RTI) instruction."""
        recovered_status = self.pull_byte_from_stack()
        rt_lo = self.pull_byte_from_stack()
        rt_hi = self.pull_byte_from_stack()
        rt = (rt_hi << 8) | rt_lo
        self.pc = rt
        self.status = recovered_status
        self.cycles += 6

    @opcode(0x60)
    def rts(self) -> None:
        """Execute the ReTurn from Subroutine (RTS) instruction."""
        return_addr_lo = self.pull_byte_from_stack()
        return_addr_hi = self.pull_byte_from_stack()
        return_addr = (return_addr_hi << 8) | return_addr_lo
        self.pc = return_addr + 1
        self.cycles += 6

    # Flag instructions

    @opcode(0x18)
    def clc(self) -> None:
        """Execute the CLear Carry (CLC) instruction."""
        self.status &= ~(1 << self.STATUS_C)
        self.cycles += 2

    @opcode(0x38)
    def sec(self) -> None:
        """Execute the SEt Carry (SEC) instruction."""
        self.status |= (1 << self.STATUS_C)
        self.cycles += 2

    @opcode(0x58)
    def cli(self) -> None:
        """Execute the CLear Interrupt (CLI) instruction."""
        self.status &= ~(1 << self.STATUS_I)
        self.cycles += 2

    @opcode(0x78)
    def sei(self) -> None:
        """Execute the SEt Interrupt (SEI) instruction."""
        self.status |= (1 << self.STATUS_I)
        self.cycles += 2

    @opcode(0xd8)
    def cld(self) -> None:
        """Execute the CLear Decimal (CLD) instruction."""
        self.status &= ~(1 << self.STATUS_D)
        self.cycles += 2

    @opcode(0xf8)
    def sed(self) -> None:
        """Execute the SEt Decimal (SED) instruction."""
        self.status |= (1 << self.STATUS_D)
        self.cycles += 2

    @opcode(0xb8)
    def clv(self) -> None:
        """Execute the CLear oVerflow (CLV) instruction."""
        self.status &= ~(1 << self.STATUS_V)
        self.cycles += 2

    # Register loading

    @opcode(0xa9, mode=AddressingMode.IMMEDIATE)
    @opcode(0xa5, mode=AddressingMode.ZERO_PAGE)
    @opcode(0xb5, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0xad, mode=AddressingMode.ABSOLUTE)
    @opcode(0xbd, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0xb9, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0xa1, mode=AddressingMode.INDIRECT_X)
    @opcode(0xb1, mode=AddressingMode.INDIRECT_Y)
    def lda(self, mode: AddressingMode) -> None:
        """Execute LDA instruction with specified addressing mode."""
        # load value into register
        addr, page_boundary_crossed = self.resolve_address(mode)
        self.a = self.memory.read(addr)

        # update cycle counter
        self.cycles += self.LOAD_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in self.LOAD_EXTRA_CYCLE_MODES:
            self.cycles += 1

        self.update_zero_flag(self.a)
        self.update_negative_flag(self.a)

    @opcode(0xa2, mode=AddressingMode.IMMEDIATE)
    @opcode(0xa6, mode=AddressingMode.ZERO_PAGE)
    @opcode(0xb6, mode=AddressingMode.ZERO_PAGE_Y)
    @opcode(0xae, mode=AddressingMode.ABSOLUTE)
    @opcode(0xbe, mode=AddressingMode.ABSOLUTE_Y)
    def ldx(self, mode: AddressingMode) -> None:
        """Execute LDX instruction with specified addressing mode."""
        # load value into register
        addr, page_boundary_crossed = self.resolve_address(mode)
        self.x = self.memory.read(addr)

        # update cycle counter
        self.cycles += self.LOAD_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in self.LOAD_EXTRA_CYCLE_MODES:
            self.cycles += 1

        self.update_zero_flag(self.x)
        self.update_negative_flag(self.x)

    @opcode(0xa0, mode=AddressingMode.IMMEDIATE)
    @opcode(0xa4, mode=AddressingMode.ZERO_PAGE)
    @opcode(0xb4, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0xac, mode=AddressingMode.ABSOLUTE)
    @opcode(0xbc, mode=AddressingMode.ABSOLUTE_X)
    def ldy(self, mode: AddressingMode) -> None:
        """Execute LDY instruction with specified addressing mode."""
        # load value into register
        addr, page_boundary_crossed = self.resolve_address(mode)
        self.y = self.memory.read(addr)

        # update cycle counter
        self.cycles += self.LOAD_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in self.LOAD_EXTRA_CYCLE_MODES:
            self.cycles += 1

        self.update_zero_flag(self.y)
        self.update_negative_flag(self.y)

    # Register storing

    @opcode(0x85, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x95, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x8d, mode=AddressingMode.ABSOLUTE)
    @opcode(0x9d, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0x99, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0x81, mode=AddressingMode.INDIRECT_X)
    @opcode(0x91, mode=AddressingMode.INDIRECT_Y)
    def sta(self, mode: AddressingMode) -> None:
        """Execute the STore A (STA) instruction."""
        # write register value to memory
        addr, _ = self.resolve_address(mode)
        self.memory.write(addr, self.a)
        self.cycles += self.STORE_CYCLE_COUNTS[mode]

    @opcode(0x86, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x96, mode=AddressingMode.ZERO_PAGE_Y)
    @opcode(0x8e, mode=AddressingMode.ABSOLUTE)
    def stx(self, mode: AddressingMode) -> None:
        """Execute the STore X (STX) instruction."""
        # write register value to memory
        addr, _ = self.resolve_address(mode)
        self.memory.write(addr, self.x)
        self.cycles += self.STORE_CYCLE_COUNTS[mode]

    @opcode(0x84, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x94, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x8c, mode=AddressingMode.ABSOLUTE)
    def sty(self, mode: AddressingMode) -> None:
        """Execute the STore Y (STY) instruction."""
        # write register value to memory
        addr, _ = self.resolve_address(mode)
        self.memory.write(addr, self.y)
        self.cycles += self.STORE_CYCLE_COUNTS[mode]

    # Register transfer

    @opcode(0xaa)
    def tax(self) -> None:
        """Execute the Transfer Accumulator to X (TAX) instruction."""
        self.x = self.a
        self.cycles += 2
        self.update_zero_flag(self.x)
        self.update_negative_flag(self.x)

    @opcode(0xa8)
    def tay(self) -> None:
        """Execute the Transfer Accumulator to Y (TAY) instruction."""
        self.y = self.a
        self.cycles += 2
        self.update_zero_flag(self.y)
        self.update_negative_flag(self.y)

    @opcode(0xba)
    def tsx(self) -> None:
        """Execute the Transfer Stack Pointer to X (TSX) instruction."""
        self.x = self.sp
        self.cycles += 2
        self.update_zero_flag(self.x)
        self.update_negative_flag(self.x)

    @opcode(0x8a)
    def txa(self) -> None:
        """Execute the Transfer X to Accumulator (TXA) instruction."""
        self.a = self.x
        self.cycles += 2
        self.update_zero_flag(self.a)
        self.update_negative_flag(self.a)

    @opcode(0x9a)
    def txs(self) -> None:
        """Execute the Transfer X to Stack Pointer (TXS) instruction."""
        self.sp = self.x
        self.cycles += 2

    @opcode(0x98)
    def tya(self) -> None:
        """Execute the Transfer Y to Accumulator (TYA) instruction."""
        self.a = self.y
        self.cycles += 2
        self.update_zero_flag(self.a)
        self.update_negative_flag(self.a)

    # Stack instructions

    @opcode(0x48)
    def pha(self) -> None:
        """Execute the PusH Accumulator (PHA) instruction."""
        self.push_byte_to_stack(self.a)
        self.cycles += 3

    @opcode(0x08)
    def php(self) -> None:
        """Execute the PusH Processor status (PHP) instruction."""
        status_to_push = self.status | (1 << self.STATUS_B)
        self.push_byte_to_stack(status_to_push)
        self.cycles += 3

    @opcode(0x68)
    def pla(self) -> None:
        """Execute the PuLl Accumulator (PLA) instruction."""
        self.a = self.pull_byte_from_stack()
        self.update_negative_flag(self.a)
        self.update_zero_flag(self.a)
        self.cycles += 4

    @opcode(0x28)
    def plp(self) -> None:
        """Execute the PuLl Processor status (PLP) instruction."""
        pulled_status = self.pull_byte_from_stack()
        pulled_status &= ~(1 << self.STATUS_B)
        self.status = pulled_status
        self.cycles += 4

    # Unary arithmetic

    @opcode(0xc6, mode=AddressingMode.ZERO_PAGE)
    @opcode(0xd6, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0xce, mode=AddressingMode.ABSOLUTE)
    @opcode(0xde, mode=AddressingMode.ABSOLUTE_X)
    def dec(self, mode: AddressingMode) -> None:
        """Execute the DECrement (DEC) instruction."""
        addr, _ = self.resolve_address(mode)
        byte = self.memory.read(addr)
        byte = (byte - 1) & 0xff
        self.memory.write(addr, byte)

        self.cycles += self.UNARY_CYCLE_COUNTS[mode]

        self.update_zero_flag(byte)
        self.update_negative_flag(byte)

    @opcode(0xca)
    def dex(self) -> None:
        """Execute the DEcrement X (DEX) instruction."""
        self.x = (self.x - 1) & 0xff

        self.cycles += 2

        self.update_zero_flag(self.x)
        self.update_negative_flag(self.x)

    @opcode(0x88)
    def dey(self) -> None:
        """Execute the DEcrement Y (DEY) instruction."""
        self.y = (self.y - 1) & 0xff

        self.cycles += 2

        self.update_zero_flag(self.y)
        self.update_negative_flag(self.y)

    @opcode(0xe6, mode=AddressingMode.ZERO_PAGE)
    @opcode(0xf6, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0xee, mode=AddressingMode.ABSOLUTE)
    @opcode(0xfe, mode=AddressingMode.ABSOLUTE_X)
    def inc(self, mode: AddressingMode) -> None:
        """Execute the INCrement (INC) instruction."""
        addr, _ = self.resolve_address(mode)
        byte = self.memory.read(addr)
        byte = (byte + 1) & 0xff
        self.memory.write(addr, byte)

        self.cycles += self.UNARY_CYCLE_COUNTS[mode]

        self.update_zero_flag(byte)
        self.update_negative_flag(byte)

    @opcode(0xe8)
    def inx(self) -> None:
        """Execute the INcrement X (INX) instruction."""
        self.x = (self.x + 1) & 0xff

        self.cycles += 2

        self.update_zero_flag(self.x)
        self.update_negative_flag(self.x)

    @opcode(0xc8)
    def iny(self) -> None:
        """Execute the INcrement Y (INY) instruction."""
        self.y = (self.y + 1) & 0xff

        self.cycles += 2

        self.update_zero_flag(self.y)
        self.update_negative_flag(self.y)

    @opcode(0x0a)
    @opcode(0x06, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x16, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x0e, mode=AddressingMode.ABSOLUTE)
    @opcode(0x1e, mode=AddressingMode.ABSOLUTE_X)
    def asl(self, mode: AddressingMode | None = None) -> None:
        """Execute the Arithmetic Shift Left (ASL) instruction.

        If `mode` is None, ASL is performed on the accumulator.
        """
        value: int
        carry: int
        if mode:
            addr, _ = self.resolve_address(mode)
            value = self.memory.read(addr)

            carry = (value >> 7) & 1
            value = (value << 1) & 0xff

            self.memory.write(addr, value)
        else:
            value = self.a

            carry = (value >> 7) & 1
            value = (value << 1) & 0xff

            self.a = value

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry << self.STATUS_C)

        self.cycles += self.UNARY_CYCLE_COUNTS[mode] if mode else 2

        self.update_zero_flag(value)
        self.update_negative_flag(value)

    @opcode(0x4a)
    @opcode(0x46, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x56, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x4e, mode=AddressingMode.ABSOLUTE)
    @opcode(0x5e, mode=AddressingMode.ABSOLUTE_X)
    def lsr(self, mode: AddressingMode | None = None) -> None:
        """Execute the Logic Shift Right (LSR) instruction.

        If `mode` is None, LSR is performed on the accumulator.
        """
        value: int
        carry: int
        if mode:
            addr, _ = self.resolve_address(mode)
            value = self.memory.read(addr)

            carry = value & 1
            value >>= 1

            self.memory.write(addr, value)
        else:
            value = self.a

            carry = value & 1
            value >>= 1

            self.a = value

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry << self.STATUS_C)

        self.cycles += self.UNARY_CYCLE_COUNTS[mode] if mode else 2

        self.update_zero_flag(value)
        self.update_negative_flag(value)  # Always zero here

    @opcode(0x2a)
    @opcode(0x26, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x36, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x2e, mode=AddressingMode.ABSOLUTE)
    @opcode(0x3e, mode=AddressingMode.ABSOLUTE_X)
    def rol(self, mode: AddressingMode | None) -> None:
        """Execute the Rotate Left (ROL) instruction.

        If `mode` is None, ROL is performed on the accumulator.
        """
        buffer = (self.status >> self.STATUS_C) & 1
        value: int
        carry: int
        if mode:
            addr, _ = self.resolve_address(mode)
            value = self.memory.read(addr)

            carry = (value >> 7) & 1
            value = (value << 1 | buffer) & 0xff

            self.memory.write(addr, value)
        else:
            value = self.a

            carry = (value >> 7) & 1
            value = (value << 1 | buffer) & 0xff

            self.a = value

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry << self.STATUS_C)

        self.cycles += self.UNARY_CYCLE_COUNTS[mode] if mode else 2

        self.update_zero_flag(value)
        self.update_negative_flag(value)

    @opcode(0x6a)
    @opcode(0x66, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x76, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x6e, mode=AddressingMode.ABSOLUTE)
    @opcode(0x7e, mode=AddressingMode.ABSOLUTE_X)
    def ror(self, mode: AddressingMode | None) -> None:
        """Execute the Rotate Right (ROR) instruction.

        If `mode` is None, ROR is performed on the accumulator.
        """
        buffer = (self.status >> self.STATUS_C) & 1
        value: int
        carry: int
        if mode:
            addr, _ = self.resolve_address(mode)
            value = self.memory.read(addr)

            carry = value & 1
            value = ((buffer << 8) | value) >> 1

            self.memory.write(addr, value)
        else:
            value = self.a

            carry = value & 1
            value = ((buffer << 8) | value) >> 1

            self.a = value

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry << self.STATUS_C)

        self.cycles += self.UNARY_CYCLE_COUNTS[mode] if mode else 2

        self.update_zero_flag(value)
        self.update_negative_flag(value)

    # Binary arithmetic

    @opcode(0x69, mode=AddressingMode.IMMEDIATE)
    @opcode(0x65, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x75, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x6d, mode=AddressingMode.ABSOLUTE)
    @opcode(0x7d, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0x79, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0x61, mode=AddressingMode.INDIRECT_X)
    @opcode(0x71, mode=AddressingMode.INDIRECT_Y)
    def adc(self, mode: AddressingMode) -> None:
        """Execute the ADd with Carry (ADC) instruction."""
        addr, page_boundary_crossed = self.resolve_address(mode)
        operand = self.memory.read(addr)

        a_initial = self.a
        carry_in = (self.status >> self.STATUS_C) & 1
        binary_intermediate_sum = self.a + operand + carry_in
        carry_out = binary_intermediate_sum >> 8
        binary_result = binary_intermediate_sum & 0xff

        if (self.status & (1 << self.STATUS_D)) == 0:
            self.a = binary_result
        else:
            lo_nibble_a = a_initial & 0xF
            hi_nibble_a = a_initial >> 4
            lo_nibble_operand = operand & 0xF
            hi_nibble_operand = operand >> 4

            a_dec = lo_nibble_a + hi_nibble_a * 10
            operand_dec = lo_nibble_operand + hi_nibble_operand * 10
            intermediate_sum = a_dec + operand_dec + carry_in

            carry_out = 1 if intermediate_sum >= 100 else 0  # noqa: PLR2004
            intermediate_sum = intermediate_sum - 100 if carry_out else intermediate_sum
            self.a = dec_to_bcd(intermediate_sum)

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry_out << self.STATUS_C)
        self.update_zero_flag(binary_result)
        self.update_negative_flag(binary_result)
        self.update_overflow_flag(a_initial, operand, binary_result)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in (*self.BINARY_EXTRA_CYCLE_MODES, AddressingMode.INDIRECT_Y):
            self.cycles += 1

    @opcode(0x29, mode=AddressingMode.IMMEDIATE)
    @opcode(0x25, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x35, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x2d, mode=AddressingMode.ABSOLUTE)
    @opcode(0x3d, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0x39, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0x21, mode=AddressingMode.INDIRECT_X)
    @opcode(0x31, mode=AddressingMode.INDIRECT_Y)
    def and_op(self, mode: AddressingMode) -> None:
        """Execute the AND instruction."""
        addr, page_boundary_crossed = self.resolve_address(mode)
        operand = self.memory.read(addr)

        self.a &= operand

        self.update_zero_flag(self.a)
        self.update_negative_flag(self.a)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in (*self.BINARY_EXTRA_CYCLE_MODES, AddressingMode.INDIRECT_Y):
            self.cycles += 1

    @opcode(0x49, mode=AddressingMode.IMMEDIATE)
    @opcode(0x45, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x55, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x4d, mode=AddressingMode.ABSOLUTE)
    @opcode(0x5d, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0x59, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0x41, mode=AddressingMode.INDIRECT_X)
    @opcode(0x51, mode=AddressingMode.INDIRECT_Y)
    def eor(self, mode: AddressingMode) -> None:
        """Execute the Exclusive OR instruction."""
        addr, page_boundary_crossed = self.resolve_address(mode)
        operand = self.memory.read(addr)

        self.a ^= operand

        self.update_zero_flag(self.a)
        self.update_negative_flag(self.a)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in self.BINARY_EXTRA_CYCLE_MODES:
            self.cycles += 1

    @opcode(0x09, mode=AddressingMode.IMMEDIATE)
    @opcode(0x05, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x15, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0x0d, mode=AddressingMode.ABSOLUTE)
    @opcode(0x1d, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0x19, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0x01, mode=AddressingMode.INDIRECT_X)
    @opcode(0x11, mode=AddressingMode.INDIRECT_Y)
    def ora(self, mode: AddressingMode) -> None:
        """Execute the OR with Accumulator instruction."""
        addr, page_boundary_crossed = self.resolve_address(mode)
        operand = self.memory.read(addr)

        self.a |= operand

        self.update_zero_flag(self.a)
        self.update_negative_flag(self.a)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in self.BINARY_EXTRA_CYCLE_MODES:
            self.cycles += 1

    @opcode(0xe9, mode=AddressingMode.IMMEDIATE)
    @opcode(0xe5, mode=AddressingMode.ZERO_PAGE)
    @opcode(0xf5, mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0xed, mode=AddressingMode.ABSOLUTE)
    @opcode(0xfd, mode=AddressingMode.ABSOLUTE_X)
    @opcode(0xf9, mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0xe1, mode=AddressingMode.INDIRECT_X)
    @opcode(0xf1, mode=AddressingMode.INDIRECT_Y)
    def sbc(self, mode: AddressingMode) -> None:
        """Execute the SuBtract with Carry / borrow (SBC) instruction."""
        addr, page_boundary_crossed = self.resolve_address(mode)
        operand = self.memory.read(addr)

        a_initial = self.a
        carry_in = (self.status >> self.STATUS_C) & 1
        binary_intermediate_difference = self.a + (~operand & 0xff) + carry_in
        carry_out = binary_intermediate_difference >> 8
        binary_result = binary_intermediate_difference & 0xff

        if (self.status & (1 << self.STATUS_D)) == 0:
            self.a = binary_result
        else:
            lo_nibble_a = a_initial & 0xF
            hi_nibble_a = a_initial >> 4
            lo_nibble_operand = operand & 0xF
            hi_nibble_operand = operand >> 4

            a_dec = lo_nibble_a + hi_nibble_a * 10
            operand_dec = lo_nibble_operand + hi_nibble_operand * 10
            intermediate_sum = a_dec - operand_dec + carry_in - 1

            carry_out = 1 if intermediate_sum >= 0 else 0
            intermediate_sum = intermediate_sum if carry_out else intermediate_sum + 100
            self.a = dec_to_bcd(intermediate_sum)

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry_out << self.STATUS_C)
        self.update_zero_flag(binary_result)
        self.update_negative_flag(binary_result)
        self.update_overflow_flag(a_initial, ~operand & 0xff, binary_result)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in (*self.BINARY_EXTRA_CYCLE_MODES, AddressingMode.INDIRECT_Y):
            self.cycles += 1

    # Binary logic

    @opcode(0x24, mode=AddressingMode.ZERO_PAGE)
    @opcode(0x2c, mode=AddressingMode.ABSOLUTE)
    def bit(self, mode: AddressingMode) -> None:
        """Execute the BIT test (BIT) instruction."""
        addr, _ = self.resolve_address(mode)
        operand = self.memory.read(addr)

        operand_bit_7 = (operand >> 7) & 1
        operand_bit_6 = (operand >> 6) & 1
        operand_mask_zero = 1 if operand & self.a == 0 else 0

        self.status &= ~(1 << self.STATUS_N)
        self.status &= ~(1 << self.STATUS_V)
        self.status &= ~(1 << self.STATUS_Z)
        self.status |= (operand_bit_7 << self.STATUS_N)
        self.status |= (operand_bit_6 << self.STATUS_V)
        self.status |= (operand_mask_zero << self.STATUS_Z)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]

    @opcode(0xc9, register="a", mode=AddressingMode.IMMEDIATE)
    @opcode(0xc5, register="a", mode=AddressingMode.ZERO_PAGE)
    @opcode(0xd5, register="a", mode=AddressingMode.ZERO_PAGE_X)
    @opcode(0xcd, register="a", mode=AddressingMode.ABSOLUTE)
    @opcode(0xdd, register="a", mode=AddressingMode.ABSOLUTE_X)
    @opcode(0xd9, register="a", mode=AddressingMode.ABSOLUTE_Y)
    @opcode(0xc1, register="a", mode=AddressingMode.INDIRECT_X)
    @opcode(0xd1, register="a", mode=AddressingMode.INDIRECT_Y)
    @opcode(0xe0, register="x", mode=AddressingMode.IMMEDIATE)
    @opcode(0xe4, register="x", mode=AddressingMode.ZERO_PAGE)
    @opcode(0xec, register="x", mode=AddressingMode.ABSOLUTE)
    @opcode(0xc0, register="y", mode=AddressingMode.IMMEDIATE)
    @opcode(0xc4, register="y", mode=AddressingMode.ZERO_PAGE)
    @opcode(0xcc, register="y", mode=AddressingMode.ABSOLUTE)
    def compare(self, register: Literal["a", "x", "y"], mode: AddressingMode) -> None:
        """Execute the compare instruction (CMP, CPX, CPY)."""
        if register == "a":
            register_value = self.a
        elif register == "x":
            register_value = self.x
        elif register == "y":
            register_value = self.y
        else:
            msg = f"Invalid register '{register}'."
            raise ValueError(msg)

        self.compare_logic(register_value, mode)

    def compare_logic(self, register_value: int, mode: AddressingMode) -> None:
        """Execute logic for comparison instructions and update registers and cycle counts."""
        addr, page_boundary_crossed = self.resolve_address(mode)
        operand = self.memory.read(addr)

        binary_intermediate_difference = register_value + (~operand & 0xff) + 1
        carry_out = (binary_intermediate_difference >> 8) & 1
        binary_result = binary_intermediate_difference & 0xff

        self.status &= ~(1 << self.STATUS_C)
        self.status |= (carry_out << self.STATUS_C)
        self.update_zero_flag(binary_result)
        self.update_negative_flag(binary_result)

        self.cycles += self.BINARY_CYCLE_COUNTS[mode]
        if page_boundary_crossed and mode in (*self.BINARY_EXTRA_CYCLE_MODES, AddressingMode.INDIRECT_Y):
            self.cycles += 1


def run(
    cpu: CPU6502,
    max_steps: int | None = 10_000,
    interrupt_hook: Callable[[CPU6502], None] | None = None,
    cycles_per_second: float | None = None,
) -> None:
    """Let a CPU run it's program.

    Args:
        cpu: CPU to let run.
        max_steps: Maximum number of instructions to execute. If set to None there is no limit on number of
        instructions.
        interrupt_hook: Hook to trigger interrupts in the CPU based on the state of, e.g., peripherals.
        cycles_per_second: Roughly limit program execution speed to the specified frequency.

    Raises:
        RuntimeError: When maximum number of steps is reached.

    """
    time_per_cycle = 1 / cycles_per_second if cycles_per_second is not None else 0
    steps = 0
    cycles_at_last_sleep = 0
    while True:
        result = cpu.step()
        steps += 1

        if result == StepResult.BRK:
            break

        if interrupt_hook is not None:
            interrupt_hook(cpu)

        if max_steps is not None:  # noqa: SIM102, doesn't work here
            if steps > max_steps:
                msg = "Maximum number of steps reached."
                raise RuntimeError(msg)

        if cycles_per_second is not None:
            cycles_since_last_sleep = cpu.cycles - cycles_at_last_sleep
            time.sleep(cycles_since_last_sleep * time_per_cycle)
            cycles_at_last_sleep = cpu.cycles
