# Attribution

Skills imported from third-party projects (all MIT-licensed). Original content is preserved; only path/portability adjustments for OpenCode were made where noted.

| Skill | Source | Imported | Notes |
|-------|--------|----------|-------|
| `ui-ux-pro-max` | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) v2.15.0 | 2026-11-07 | Rewrote `${CLAUDE_PLUGIN_ROOT}` script paths to `~/.config/opencode/skills/ui-ux-pro-max/scripts/search.py` (matches the upstream installer's global layout for OpenCode) |
| `design`, `design-system`, `brand`, `banner-design`, `slides`, `ui-styling` | [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) v2.15.0 (sub-skills, `.claude/skills/`) | 2026-11-07 | Vendored as-is — script paths are already relative to the skill's base directory; sibling `../<skill>/scripts/...` refs resolve because all seven skills install side-by-side. Removed a `.coverage` test artifact from `ui-styling`. Logo/icon/CIP actions in `design` require external AI image provider keys (Gemini/Atlas Cloud/MuAPI); CSS/HTML/token work is standalone |
| `vercel-react-best-practices` | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices) | 2026-11-07 | Repo-level README/metadata files omitted; SKILL.md + rules/ kept |
| `vercel-react-view-transitions` | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-view-transitions) | 2026-11-07 | Repo-level README/metadata files omitted; SKILL.md + references/ kept |
| `sql-optimization-patterns` | [wshobson/agents](https://github.com/wshobson/agents/tree/main/plugins/developer-essentials/skills/sql-optimization-patterns) | 2026-11-07 | — |
| `react-state-management` | [wshobson/agents](https://github.com/wshobson/agents/tree/main/plugins/frontend-mobile-development/skills/react-state-management) | 2026-11-07 | — |
| `tailwind-design-system` | [wshobson/agents](https://github.com/wshobson/agents/tree/main/plugins/frontend-mobile-development/skills/tailwind-design-system) | 2026-11-07 | — |
| `responsive-design` | [wshobson/agents](https://github.com/wshobson/agents/tree/main/plugins/ui-design/skills/responsive-design) | 2026-11-07 | — |
| `modern-javascript-patterns` | [wshobson/agents](https://github.com/wshobson/agents/tree/main/plugins/javascript-typescript/skills/modern-javascript-patterns) | 2026-11-07 | — |
| `api-design-principles` | [wshobson/agents](https://github.com/wshobson/agents/tree/main/plugins/backend-development/skills/api-design-principles) | 2026-11-07 | — |
| `codehygiene` | [brianbell-x/codehygiene](https://github.com/brianbell-x/codehygiene) v0.7.0 | 2026-11-20 | Vendored as-is — the `codehygiene/` package directory is the skill directory (SKILL.md + pure-NumPy runtime + 8.2 KiB `weights.npz`), so no path rewrites were needed; runs via `python "<SKILL_DIR>" <files> --check`. Requires Python ≥3.10 + numpy. Optional 12-hourly release check phones home to GitHub; disable with `--no-update-check` or `CODEHYGIENE_NO_UPDATE_CHECK=1`. Upstream declares MIT in `pyproject.toml` but ships no `LICENSE` file as of v0.7.0 |
