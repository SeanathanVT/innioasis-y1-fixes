# btlog-dump/Makefile — cross-compile btlog-dump.c for ARMv7 Android 4.2.2.
#
# Same toolchain + style as src/su/. No libc dependency; direct ARM-EABI
# syscalls only. Output is a small static ELF that connects to mtkbt's
# @btlog abstract UNIX socket and dumps the stream to stdout.
#
# Install toolchain (Rocky/Alma/RHEL/Fedora with EPEL):
#   sudo dnf install -y epel-release
#   sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu

CC := arm-linux-gnu-gcc

CFLAGS := -nostdlib -ffreestanding -fno-builtin -fno-stack-protector \
          -Os -Wall -Wextra -std=gnu99 \
          -march=armv7-a -mthumb -mfloat-abi=soft \
          -fno-asynchronous-unwind-tables -fno-unwind-tables

LDFLAGS := -nostdlib -static -Wl,--build-id=none -Wl,--gc-sections
LIBS    := -lgcc

BUILD_DIR := build
TARGET    := $(BUILD_DIR)/btlog-dump
# Reuse the entry stub from src/su/ — same _start convention.
OBJS      := $(BUILD_DIR)/start.o $(BUILD_DIR)/btlog-dump.o

.PHONY: all clean check verify-toolchain

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

$(BUILD_DIR)/start.o: ../su/start.S | $(BUILD_DIR) verify-toolchain
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD_DIR)/btlog-dump.o: btlog-dump.c | $(BUILD_DIR) verify-toolchain
	$(CC) $(CFLAGS) -c -o $@ $<

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ $(LIBS)
	arm-linux-gnu-strip --strip-all $@
	@echo ""
	@echo "Built: $@"
	@file $@ | sed 's/^/  /'
	@echo "  size: $$(wc -c < $@) bytes"
	@echo "  md5:  $$(md5sum $@ | awk '{print $$1}')"

check: $(TARGET)
	@file $(TARGET) | grep -q "ELF 32-bit LSB.*ARM"   || { echo "FAIL: not ARM ELF"; exit 1; }
	@file $(TARGET) | grep -q "statically linked"     || { echo "FAIL: not statically linked"; exit 1; }
	@! arm-linux-gnu-readelf -d $(TARGET) | grep -q NEEDED || { echo "FAIL: has NEEDED entries"; exit 1; }
	@echo "OK: $(TARGET) is a self-contained statically-linked ARM ELF"

clean:
	rm -rf $(BUILD_DIR)
