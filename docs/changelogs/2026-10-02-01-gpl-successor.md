---
title: "Zendure AppDaemon Proxy 0.2.0"
summary: "A GPL successor keeps existing control interfaces and protects low batteries during discharge."
---

## Installation

**New HACS repository**
Use `abaart/Zendure-AppDaemon-Proxy` as an AppDaemon custom repository. Keep your existing app configuration, endpoints and sensor identifiers. Keep global `production_mode: true` and restart AppDaemon after each HACS update.

## Battery control

**Protected discharge minimum**
A battery at or below its protected minimum stays excluded when the other batteries cannot meet a discharge request. The proxy limits the request to the safe capacity of the available batteries.

## License

**GPL-3.0-only source**
The ten core modules have been written independently from recorded functional contracts and synthetic tests. Audited own modules and the owner-confirmed AI illustration remain available. Each distributed file has a recorded source and licensing basis.
