# Failure Modes and Debug Plan

| Failure mode | Detection | Response |
|---|---|---|
| Product spec from shop text treated as engineering data | quality level Q0/Q1 | block design/BOM use |
| Spreadsheet silently changes Voc/Isc/panel dimensions | protected header scan | block import |
| Roof height missing | mounting precheck | structural status S0 / block install pack |
| Roof type unknown | mounting precheck | block mounting recommendation |
| Old quote changes after product update | calculation packet hash/versioning | never mutate old quote |
| Manufacturer report used after design changes | stale report detector, future phase | mark report stale |
| Shading model overconfidence | confidence labels, future benchmark | show estimate level |
| Wrong inverter selected | string engine, future phase | invalid design blocked |

## Debug philosophy

Every failure should produce: code, severity, area, path, human message, and suggested fix. No silent failures. No mystery warnings. No little red triangle that expects a human to divine the gods.
