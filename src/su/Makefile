# su/Makefile — cross-compile su.c + start.S for ARMv7 Android 4.2.2.
#
# No-libc build: this binary uses direct ARM-EABI syscalls (svc 0) with no
# libc dependency. The only library it links is libgcc.a (for compiler
# builtins like __aeabi_*), which the EPEL gcc-arm-linux-gnu package ships.
#
# Install (Rocky/Alma/RHEL/Fedora with EPEL):
#   sudo dnf install -y epel-release
#   sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu
#
# The Fedora/RHEL package layout uses the arm-linux-gnueabi triplet
# internally (in /usr/lib/gcc/arm-linux-gnueabi/) but installs binaries with
# the arm-linux-gnu- prefix. Both names refer to the same toolchain.

CC := arm-linux-gnu-gcc

CFLAGS := -nostdlib -ffreestanding -fno-builtin -fno-stack-protector \
          -Os -Wall -Wextra -std=gnu99 \
          -march=armv7-a -mthumb -mfloat-abi=soft \
          -fno-asynchronous-unwind-tables -fno-unwind-tables

LDFLAGS := -nostdlib -static -Wl,--build-id=none -Wl,--gc-sections

# libgcc.a path varies by gcc version; let the driver find it.
LIBS := -lgcc

BUILD_DIR := build
TARGET    := $(BUILD_DIR)/su
OBJS      := $(BUILD_DIR)/start.o $(BUILD_DIR)/su.o

.PHONY: all clean check verify-toolchain strip-info

all: $(TARGET)

verify-toolchain:
	@command -v $(CC) >/dev/null 2>&1 || { \
	  echo "ERROR: $(CC) not in PATH"; \
	  echo "Install: sudo dnf install -y epel-release && \\"; \
	  echo "         sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu"; \
	  exit 1; }
	@$(CC) --version | head -n1

$(BUILD_DIR):
	@mkdir -p $@

$(BUILD_DIR)/start.o: start.S | $(BUILD_DIR) verify-toolchain
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD_DIR)/su.o: su.c | $(BUILD_DIR) verify-toolchain
	$(CC) $(CFLAGS) -c -o $@ $<

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LIBS)
	arm-linux-gnu-strip --strip-all $@
	@echo ""
	@echo "Built: $@"
	@file $@ | sed 's/^/  /'
	@echo "  size: $$(wc -c < $@) bytes"
	@echo "  md5:  $$(md5sum $@ | awk '{print $$1}')"

# Sanity-check: ARM ELF, statically linked, no NEEDED libs (no dynamic deps).
check: $(TARGET)
	@file $(TARGET) | grep -q "ELF 32-bit LSB.*ARM"   || { echo "FAIL: not ARM ELF"; exit 1; }
	@file $(TARGET) | grep -q "statically linked"     || { echo "FAIL: not statically linked"; exit 1; }
	@! arm-linux-gnu-readelf -d $(TARGET) | grep -q NEEDED || { echo "FAIL: has NEEDED entries"; exit 1; }
	@echo "OK: $(TARGET) is a self-contained statically-linked ARM ELF"

clean:
	rm -rf $(BUILD_DIR)
