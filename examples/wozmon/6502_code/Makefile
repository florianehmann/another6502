# Project settings
TARGET     := rom
CONFIG     := memory.cfg
ASM_SRCS   := $(wildcard src/*.s)
OBJ_DIR    := obj
BIN_DIR    := bin

# Toolchain
CC65       := cc65
CA65       := ca65
LD65       := ld65

# Flags
CA65FLAGS  :=
LD65FLAGS  := -C $(CONFIG) -o $(BIN_DIR)/$(TARGET).bin

# Derived
OBJS       := $(patsubst src/%.s,$(OBJ_DIR)/%.o,$(ASM_SRCS))

# Default target
all: $(BIN_DIR)/$(TARGET).bin

# Link final binary
$(BIN_DIR)/$(TARGET).bin: $(OBJS) | $(BIN_DIR)
	$(LD65) $(LD65FLAGS) $(OBJS)

# Assemble .s into .o
$(OBJ_DIR)/%.o: src/%.s | $(OBJ_DIR)
	$(CA65) $(CA65FLAGS) -o $@ $<

# Ensure directories exist
$(OBJ_DIR):
	mkdir -p $@

$(BIN_DIR):
	mkdir -p $@

# Clean
clean:
	rm -rf $(OBJ_DIR) $(BIN_DIR)

.PHONY: all clean
