/*
 * start.S — ARM-EABI entry stub for the no-libc su.c.
 *
 * Linux ELF entry layout (process start state):
 *   sp -> argc          (4 bytes)
 *         argv[0..argc] (argc+1 pointers, last is NULL)
 *         envp[0..]     (NULL-terminated array of env pointers)
 *         auxv[..]      (key/value pairs, ignored here)
 *
 * We call main(argc, argv, envp) and exit with its return value.
 */

    .syntax unified
    .arch armv7-a
    .text
    .global _start
    .type _start, %function
    .thumb_func
_start:
    ldr     r0, [sp]              @ r0 = argc
    add     r1, sp, #4            @ r1 = &argv[0]
    add     r2, r0, #1            @ r2 = argc + 1 (count of argv slots incl NULL)
    add     r2, r1, r2, lsl #2    @ r2 = &envp[0]   (skip past argv + its NULL)
    bl      main
    @ tail-call _exit(r0):
    mov     r7, #1                @ __NR_exit
    svc     #0
1:  b       1b                    @ unreachable
    .size _start, .-_start

    /* Mark stack as non-executable (NX). */
    .section .note.GNU-stack, "", %progbits
