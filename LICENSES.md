# License Compliance

This table documents every component shipped with or used by SparkyBot and how
each license obligation is met.

| Component | License | How We Comply |
|---|---|---|
| SparkyBot | MIT | Our code. See [LICENSE](./LICENSE). |
| GW2 Elite Insights Parser CLI | MIT | Bundled in the release package. License text included in [THIRD_PARTY_LICENSES/](./THIRD_PARTY_LICENSES/GW2-Elite-Insights-Parser.LICENSE). |
| PySide6 / Qt | LGPL-3.0 | Dynamically linked into binary builds. The source code of the application is publicly available in this repository. Users can relink with a modified PySide6 per LGPL-3.0 section 4(d). |
| watchdog | Apache-2.0 | Python dependency installed at runtime. License text available at https://www.apache.org/licenses/LICENSE-2.0. |
| requests | Apache-2.0 | Python dependency installed at runtime. License text available at https://www.apache.org/licenses/LICENSE-2.0. |
| edge-tts | LGPL-3.0 (primary) | Optional runtime dependency invoked in-process. Source: https://github.com/rany2/edge-tts (one file uses MIT; the rest LGPL-3.0). **NEEDS-REVIEW** — invoked in-process under a GPL-family license. |
| GW2_EI_log_combiner | GPL-3.0 | **Not bundled, not imported, and not shipped in this repository or its installers.** Downloaded at runtime by the user (with explicit consent) and executed as a separate program. No GPL code resides in this repository. |

## Interoperability adapters are not bundled components

SparkyBot's import/export adapters are original code that reads or writes a
small allowlist of documented settings fields. They do not copy or distribute
another project's source, algorithms, binaries, artwork, or documentation.
GPL-3.0 tools such as AxiBridge and the Drevarr stack therefore remain separate
programs; WvW Insights and other repositories without a published license are
handled with the stricter format-only, zero-code-reuse posture.

The linked projects, their reviewed license labels, honest feature differences,
and adapter coverage are recorded in
[docs/WVW_LOG_TOOL_INTEROPERABILITY.md](docs/WVW_LOG_TOOL_INTEROPERABILITY.md).
Upstream licenses must be rechecked whenever an adapter's scope changes.
