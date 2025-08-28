---
title: FM Transceiver
nav_order: 5
parent: Hardware
---

# FM Transceiver PCB

<table>
  <tr><th>Top</th><th>Bottom</th></tr>
  <tr>
    <td><img src="main-3D_top.png?dummy={{ site.data['hash'] }}" alt="top" /></td>
    <td><img src="main-3D_bottom.png?dummy={{ site.data['hash'] }}" alt="bottom" /></td>
  </tr>
</table>

Das `FM` Modul beherbergt einen FM Chip (SA818x) für das 2m oder 70cm Band.

| Spannung |                          benötigter Strom |
| -------- | ----------------------------------------- |
|      +5V |                                   max. 1A |
|     +12V | max. 1A (SA818) via buck converter for 5V |

## Daten

- [Schaltplan](main-schematic.pdf)
- [BOM](main-bom.html)
- [iBOM](main-ibom.html)
- [JLCPCB fabrication & stencil](JLCPCB/main-_JLCPCB_compress.zip)
- [JLCPCB Bom](JLCPCB/main_bom_jlc.csv)
- [JLCPCB Pick&Place](JLCPCB/main_cpl_jlc.csv)
