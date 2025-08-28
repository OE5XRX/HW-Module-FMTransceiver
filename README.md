# HW-Module-Template

## Description

This repository is a template for hardware modules using KiCad, designed for clean, maintainable development with CI/CD pipelines generating and publishing production data automatically.

## Features
* Structured KiCad project (schematic, PCB)
* Clean repository: no production data stored
* GitHub Actions CI/CD pipeline:
  * Generates Gerber, BOM, and assembly files
  * Uploads artifacts to GitHub Releases
* Open Hardware license ready (CERN-OHL-S)

## Repository Structure

```
HW-Module-Template/
├── .github/workflows/     # CI/CD pipeline scripts
├── doc/                   # Documentation exports
├── symbols/               # Local symbol library (if needed)
├── footprints/            # Local footprint library (if needed)
├── 3d-models/             # STEP/WRL files (optional)
├── main.kicad_pro         # KiCad main project file
├── main.kicad_sch         # KiCad main schematic file
├── main.kicad_pcb         # KiCad main pcb file
├── *.kicad_sch            # other KiCad schematic files
├── LICENSE
└── README.md
```

## Usage
1. Click “Use this template” on GitHub.
2. Clone your repository locally and open in KiCad.
3. fix the missing names in the readme and add documentation in the `doc/index.md` file.
4. Work on your schematic and PCB as usual (create a new project (main-file must be called `main.kicad_*`) or copy a new one into the folder structure).
5. Push your changes.
6. CI/CD will automatically generate production files.
7. If there is a release defined, CI/CD will create a documentation on the main webpage.

## License

This project is licensed under [CERN-OHL-S-2.0](https://choosealicense.com/licenses/cern-ohl-s-2.0/).

## Badges

![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/OE5XRX/<your-repo>/kibot-check.yaml?branch=main)<br>
![GitHub Release](https://img.shields.io/github/v/release/OE5XRX/<your-repo>)<br>
![License: CERN-OHL-S](https://img.shields.io/badge/license-CERN--OHL--S--2.0-blue)<br>
![KiCad Supported](https://img.shields.io/badge/KiCad-supported-blue)<br>
![Project Type](https://img.shields.io/badge/type-hardware-red)<br>

## Contact

OE5XRX Amateur Radio Club<br>
🌐 https://oe5xrx.org<br>
✉️ info@oe5xrx.org<br>
