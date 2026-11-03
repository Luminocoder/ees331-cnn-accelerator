@echo off
rem 重新编译固件（输出 app.elf 到当前目录的上一级）
setlocal
set "GCC=C:\AMDDesignTools\2025.2\gnu\aarch32\nt\gcc-arm-none-eabi\bin"

"%GCC%\arm-none-eabi-gcc.exe" -mcpu=cortex-a9 -marm -mfloat-abi=soft -O2 ^
  -mno-unaligned-access -ffreestanding -fno-builtin -nostdlib -Wall ^
  -T link.ld -Wl,--entry=_start -Wl,--build-id=none ^
  start.S fw_seg.c -lgcc -o ..\app.elf

if errorlevel 1 (
  echo [ERROR] build failed
  pause
  exit /b 1
)
echo [OK] built ..\app.elf
pause
