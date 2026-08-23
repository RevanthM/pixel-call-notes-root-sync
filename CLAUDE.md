# Claude Code

@AGENTS.md

Read and follow the imported canonical policy before taking any action.

Start with `README.md` and `docs/ANDROID_SETUP.md`. Treat bootloader operations,
root-module installation, call artifacts, and AWS deployment as separate approval
boundaries. Never infer approval for an irreversible or external write from approval
of an earlier step.
