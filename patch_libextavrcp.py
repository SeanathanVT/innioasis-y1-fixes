#!/usr/bin/env python3
"""
Patch libextavrcp.so to advertise AVRCP 1.4 instead of 1.3.

MediaTek MT6572 / Android 4.2.2 — Innioasis Y1 DAP
Enables bidirectional metadata flow with AVRCP 1.4 car head units.

Usage:
    python3 patch_libextavrcp.py libextavrcp.so libextavrcp.so.patched

The patch modifies the AVRCP version constant from 0x0103 (1.3) to 0x0104 (1.4).
"""

import sys
import hashlib
import struct
import os

def verify_binary_format(data):
    """Verify this is an ARM32 ELF shared object."""
    if data[:4] != b'\x7fELF':
        return False, "Not an ELF binary"
    
    ei_class = data[4]
    ei_data = data[5]
    
    if ei_class != 1:  # 32-bit
        return False, f"Expected 32-bit ELF, got class {ei_class}"
    
    if ei_data != 1:  # Little-endian
        return False, f"Expected little-endian, got endianness {ei_data}"
    
    # Check ARM machine type
    e_machine = struct.unpack('<H', data[0x12:0x14])[0]
    if e_machine != 0x28:  # ARM
        return False, f"Expected ARM (0x28), got 0x{e_machine:04x}"
    
    return True, "Valid ARM32 ELF"

def patch_libextavrcp(input_file, output_file):
    """
    Patch libextavrcp.so version constant for AVRCP 1.4.
    
    Patch details:
    - Offset: 0x002e3b
    - Before: 0x0103 (AVRCP 1.3, little-endian)
    - After:  0x0104 (AVRCP 1.4, little-endian)
    
    Args:
        input_file: Path to stock libextavrcp.so
        output_file: Path to write patched binary
    
    Returns:
        (success: bool, message: str)
    """
    
    print("[*] libextavrcp.so AVRCP 1.4 Patch Tool")
    print("=" * 70)
    
    # Check input exists
    if not os.path.exists(input_file):
        return False, f"Input file not found: {input_file}"
    
    # Read input
    print(f"\n[*] Reading {input_file}...")
    try:
        with open(input_file, 'rb') as f:
            data = bytearray(f.read())
    except Exception as e:
        return False, f"Failed to read input: {e}"
    
    print(f"    Size: {len(data)} bytes")
    
    # Verify binary format
    is_valid, msg = verify_binary_format(bytes(data))
    if not is_valid:
        return False, f"Invalid binary format: {msg}"
    print(f"    ✓ {msg}")
    
    # Calculate stock MD5
    stock_md5 = hashlib.md5(bytes(data)).hexdigest()
    print(f"    MD5: {stock_md5}")
    
    # Verify patch location
    patch_offset = 0x002e3b
    patch_before = bytes(data[patch_offset:patch_offset+2])
    
    print(f"\n[*] Checking patch site at 0x{patch_offset:06x}...")
    print(f"    Current value: {patch_before.hex()}")
    
    if patch_before == b'\x03\x01':
        print("    ✓ Found expected AVRCP 1.3 constant (0x0103)")
    else:
        print(f"    ⚠ Expected b'\\x03\\x01', found b'{patch_before.hex()}'")
        print("    This binary may not be the expected stock version.")
        resp = input("    Continue anyway? [y/N]: ").strip().lower()
        if resp != 'y':
            return False, "Patch aborted by user"
    
    # Apply patch
    print(f"\n[*] Applying patch...")
    print(f"    Location: 0x{patch_offset:06x}")
    print(f"    Before:   {patch_before.hex()} (AVRCP 1.3)")
    
    data[patch_offset:patch_offset+2] = b'\x04\x01'
    
    print(f"    After:    04 01 (AVRCP 1.4)")
    print("    ✓ Patch applied")
    
    # Write output
    print(f"\n[*] Writing {output_file}...")
    try:
        with open(output_file, 'wb') as f:
            f.write(data)
    except Exception as e:
        return False, f"Failed to write output: {e}"
    
    print("    ✓ File written")
    
    # Verify patch took
    print(f"\n[*] Verifying patch...")
    try:
        with open(output_file, 'rb') as f:
            patched_data = f.read()
    except Exception as e:
        return False, f"Failed to read patched file: {e}"
    
    patched_md5 = hashlib.md5(patched_data).hexdigest()
    print(f"    Stock MD5:   {stock_md5}")
    print(f"    Patched MD5: {patched_md5}")
    
    patched_offset = patched_data[patch_offset:patch_offset+2]
    if patched_offset == b'\x04\x01':
        print(f"    ✓ Patch verified at 0x{patch_offset:06x}")
    else:
        return False, f"Patch verification failed: got {patched_offset.hex()}"
    
    # Summary
    print("\n" + "=" * 70)
    print("[✓] Patch successful!")
    print(f"\nDeployment:")
    print(f"  adb push {output_file} /system/lib/libextavrcp.so")
    print(f"  adb reboot")
    print(f"\nTesting:")
    print(f"  sdptool browse <device_addr>")
    print(f"  # Should show: AV Remote (0x110e) Version: 0x0104")
    
    return True, "Patch completed successfully"

def main():
    if len(sys.argv) != 3:
        print("Usage: patch_libextavrcp.py <input_file> <output_file>")
        print("\nExample:")
        print("  python3 patch_libextavrcp.py libextavrcp.so libextavrcp.so.patched")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    success, message = patch_libextavrcp(input_file, output_file)
    
    if not success:
        print(f"\n[✗] Error: {message}", file=sys.stderr)
        sys.exit(1)
    
    print(f"\n[✓] {message}")
    sys.exit(0)

if __name__ == '__main__':
    main()
