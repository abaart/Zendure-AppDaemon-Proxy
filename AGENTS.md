# Working instructions

Keep explanations concrete and name changed files and functions. Read CONTRIBUTING.md and PR templates before writing PR text. Start PR bodies with Problem statement, Solution and Verification, adding Implementation notes for behavior or configuration changes.

The source directory is apps/Zendure-AppDaemon-Proxy. Preserve module zendure_proxy, class ZendureProxy, endpoints, sensor IDs and MQTT identifiers. Every release requires compile/import checks, both unittest and pytest, runtime direct/awaitable AppDaemon tests, archive verification and provenance review. Include LICENSE and NOTICE at the HACS zip root.

AppDaemon production_mode must be true in global appdaemon.yaml. HACS file updates require a deliberate add-on restart. Do not deploy, remove the old installation or change the old repository without the owner's explicit approval of the concrete operation. Keep backups outside the active app directory. Do not use a broad Home Assistant deployment script for a proxy-only migration.

Independent core implementation inputs are recorded in audit/. Do not use predecessor Python core or Node-RED source as translation input. Explain any revised compatibility expectation in the audit record.
