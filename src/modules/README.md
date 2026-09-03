# modules

Every module the repo knows how to install — the full catalog, `sites-available`-style
(ARCHITECTURE.md §3). Turning one on means putting its manifest in `../modules-enabled/`; nothing
in this folder is running just because it's here.

`_template/` is what `platform-cli module scaffold <name>` will generate from once `platform-cli`
exists (Phase 2) — for now, copy it by hand to start a new module.
