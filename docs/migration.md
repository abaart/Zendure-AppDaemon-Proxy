# Switch an existing installation

The new repository installs into `/config/appdaemon/apps/Zendure-AppDaemon-Proxy/`. Keep the `zendure_proxy:` block, device addresses, server port, module and class unchanged. Stable sensor IDs, discovery topics and device IDs keep existing automation connected.

## Prepare while the old proxy runs

Confirm `production_mode: true` in global AppDaemon configuration. Record the working report, installed module hashes, `apps.yaml`, other app names, MQTT identities and metrics counters. Copy the installed modules and configuration to a timestamped backup outside `/config/appdaemon/apps/`. Store sensitive configuration backups outside Git.

Download the tagged archive, verify its checksum and inspect its root members before the restart. Install the new custom repository through HACS. With production mode enabled, AppDaemon continues using its loaded code until restarted. Check that the old report still answers and a single `zendure_proxy:` app is configured. Verify that the new installed Python files match the tested archive.

Run a fresh-install, switch and rollback rehearsal on an isolated HA/HACS setup. A local HTTP/device simulation checks runtime behavior but does not replace this rehearsal. Measure the rehearsal before choosing a restart deadline. The target is at most 60 seconds from the last successful old report to the first validated new report.

## Approved switch

Perform these operations only after the owner explicitly approves the concrete switch:

1. Stop the AppDaemon add-on: `ha apps stop a0d7b954_appdaemon`.
2. Remove the old proxy installation through HACS. Verify only the successor provides `zendure_proxy.py` in the active app directory.
3. Preserve `apps.yaml` and the global AppDaemon configuration. Start AppDaemon: `ha apps start a0d7b954_appdaemon`.
4. Poll `http://a0d7b954-appdaemon:8120/properties/report` until the report says `v0.2.0`, contains the expected configured devices and usable health data.
5. Verify existing sensor IDs, counters and MQTT identifiers and observe normal automation requests resuming. Send no added live battery test commands.
6. Verify the other AppDaemon apps, including `dynamisch_handelen` and `zendure_kwartieradministratie`.

A Home Assistant Core restart is unnecessary. AppDaemon's restart also briefly pauses other apps in the add-on. The broad `deploy_ha.sh` script copies unrelated strategy files and packages and must not be used for this switch.

## Rollback

If the readiness deadline or first operational checks fail, stop the add-on, move the successor folder to the staging/backup location outside the active app directory, restore the backed-up old folder and unchanged configuration, and start AppDaemon. Verify the old report version and normal requests. Avoid keeping two active copies of `zendure_proxy.py`.

Reinstall the old HACS entry if removal changed its registration. Keep both release archives and backups until the successor has operated normally. Do not change HACS `.storage` records directly.

## Historical repository

Prepare a README migration notice pointing to the successor. Change the old repository only with owner approval. After successful live migration, test historical HACS downloads before setting a reference-only `migration` default branch. Keep the old `main`, tags, licenses and release assets. Keep the old app structure if the reference-only branch breaks historical downloads.
