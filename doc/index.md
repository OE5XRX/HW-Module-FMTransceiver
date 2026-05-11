---
title: FM Transceiver
nav_order: 5
parent: Hardware
---

# FM Transceiver PCB

<table>
  <tr><th>Top</th><th>Bottom</th></tr>
  <tr>
    <td><img src="{{ site.data.project.name }}-3D_top.png?dummy={{ site.data['hash'] }}" alt="top" /></td>
    <td><img src="{{ site.data.project.name }}-3D_bottom.png?dummy={{ site.data['hash'] }}" alt="bottom" /></td>
  </tr>
</table>

Das `FM` Modul beherbergt einen FM Chip (SA818x) für das 2m oder 70cm Band.

| Spannung |                          benötigter Strom |
| -------- | ----------------------------------------- |
|      +5V |                                   max. 1A |
|     +12V | max. 1A (SA818) via buck converter for 5V |

## Daten

- [Schaltplan]({{ site.data.project.name }}-schematic.pdf)
- [BOM]({{ site.data.project.name }}-bom.html)
- [iBOM]({{ site.data.project.name }}-ibom.html)
- [JLCPCB fabrication & stencil](JLCPCB/{{ site.data.project.name }}-_JLCPCB_compress.zip)
- [JLCPCB Bom](JLCPCB/{{ site.data.project.name }}_bom_jlc.csv)
- [JLCPCB Pick&Place](JLCPCB/{{ site.data.project.name }}_cpl_jlc.csv)
