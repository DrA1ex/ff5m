## Firmware Recovery Guide

If you’ve modified internal system files and something went wrong—your printer no longer responds to a USB drive (you can’t flash the Factory firmware), and it doesn’t progress past the boot screen—don’t worry. This is fixable, and it doesn’t require advanced skills. Let’s start by diagnosing the issue.

### Diagnostics

To understand the problem, you’ll need a **UART-USB** adapter that supports **3.3V** logic levels. Using a 5V adapter can **damage** the motherboard, so be careful. Alternatively, you can use an ESP8266/ESP32 (do not use an Arduino, as it operates at 5V and could **fry the CPU**). Flash the ESP with the MultiSerial example from the Arduino IDE `(Examples -> Communications -> MultiSerial)`, but change the `Serial1` Baud to `115200`.

Next, connect the UART adapter to the motherboard near the processor (next to USB0). Connect the wires as follows:
- **RX** on the adapter to **TX** on the motherboard.
- **TX** on the adapter to **RX** on the motherboard.
- **GND** to **GND**.
- Do **not** connect the power line.

<p align="center">
<img width="400" alt="image" src="https://github.com/user-attachments/assets/458aad73-b224-43d3-aca0-e5998fccc44e" />
<p align="center">Pic 1. Connection of the printer UART</p>
</p>


Connect the adapter to your PC and open a terminal program (e.g., PuTTY, Arduino IDE, or PlatformIO). Power on the printer and observe the logs. 
These logs will help you determine how far the boot process goes. If the Linux kernel loads, the situation isn’t too bad, and you might be able to fix it without advanced procedures.

<p align="center">
<img width="400" alt="image" src="https://github.com/user-attachments/assets/1e8ee6ff-836a-439d-a45a-613291416d3e" />
<p align="center">Pic 2. Connection of the adapter (Wemos D1 Mini) UART</p>
</p>

### The Easy Way

If the Linux kernel loads but the system fails to boot due to your modifications, you can try rolling back those changes. Here’s how:
1. Connect to the printer via UART as described above.
2. Wait for the following line to appear in the logs:
```
Hit any key to stop autoboot
```

<p align="center">
<img width="400" alt="image" src="https://github.com/user-attachments/assets/dcfb3475-ac0d-4559-b79a-a709b3f46ea9" />
<p align="center">Pic 3. Example of UART terminal</p>
</p>

3. Quickly press Enter.

4. You’ll now be in **U-Boot**. From here, you can redefine the kernel startup command to get a shell. Enter the following commands:

```
setenv init /bin/sh
boot
```

5. If done correctly, you’ll get a shell after the Linux kernel loads. The filesystem will be mounted as read-only, so remount it as read-write:

```
mount -t proc proc /proc
mount -o remount,rw /
```

6. Now, fix whatever changes caused the issue. Note that the system isn’t fully booted, so some features may not work. Search online for solutions or ask for help in the community.

7. Once you’re done, reboot the system:

```
sync
reboot -f
```

The system should now boot normally. You can leave the UART connected if needed. If SSH isn’t working, you can log in via UART using the credentials root/root.

If this method doesn’t work, don’t lose hope. If the system partially boots, you might still be able to recover files via UART.

### The Hard Way

If the easy method doesn’t work and the system is completely unbootable, you’ll need to restore the firmware using FEL mode. This requires a firmware dump and some additional steps.

1. Enter FEL Mode:

- You don’t need to solder a button to a resistor. Instead, interrupt the boot process via UART (press Enter when prompted) to enter U-Boot.
- From U-Boot, run the following command to enter FEL mode:

```
efex
```

2. Prepare for Recovery:

- You’ll need to desolder USB0. This is relatively simple, even for beginners.

- Use the firmware dump and tools provided here: [link](https://disk.yandex.ru/d/oie2Chx1rexkgw).

3. Follow the Detailed Guide:

For step-by-step instructions, refer to this resource: [link](https://t.me/FF_5M_5M_Pro/441456/487025).

### Important Notes

Always double-check your connections when using UART to avoid damaging the hardware.
If you’re unsure about any step, ask for help in the community before proceeding.

Backup your system files before making any modifications to avoid recovery scenarios.
