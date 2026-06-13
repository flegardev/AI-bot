# Installed Codex Skills Report

Date: 2026-05-27
Codex home: `C:\Users\tinif\.codex`
Backup: `C:\Users\tinif\.codex\codex-skills-backup-20260527-100416`

## Summary

Installed 10 vetted skills into `C:\Users\tinif\.codex\skills`.
No unknown install scripts were run. Repositories were shallow-cloned to a temporary folder, inspected for `README`, `SKILL.md`, license, activity, and obvious unsafe instructions before copying selected skill folders.

`codex skills list` is not supported by `codex-cli 0.131.0-alpha.9`, but `codex debug prompt-input` confirmed all installed skills are model-visible.

## Installed Skills

| Skill | Source | What it improves | When Codex should use it | Install path | Safety notes | Verification |
|---|---|---|---|---|---|---|
| `playwright` | `openai/skills`, `skills/.curated/playwright` | Real-browser UI checks, snapshots, screenshots, console/network debugging | Browser automation, visual verification, UI flow debugging | `C:\Users\tinif\.codex\skills\playwright` | Official OpenAI skill. No unknown script run. Wrapper uses `npx` for `@playwright/cli`. | Detected by `codex debug prompt-input`; Playwright CLI captured desktop and mobile screenshots in smoke test. |
| `frontend-design` | `anthropics/skills`, `skills/frontend-design` | Distinctive non-generic UI direction and polish | Building or restyling web UI, dashboards, landing pages, components | `C:\Users\tinif\.codex\skills\frontend-design` | Official Anthropic skill. Copied with included license file. | Detected; nested `codex exec` loaded this skill and improved a Vite React page. |
| `frontend-ui-engineering` | `addyosmani/agent-skills`, `skills/frontend-ui-engineering` | Production UI architecture, accessibility, responsive layout, component quality | Building or modifying user-facing frontend code | `C:\Users\tinif\.codex\skills\frontend-ui-engineering` | MIT repo, high activity. Copied skill only plus license. | Detected by `codex debug prompt-input`. |
| `frontend-design-review` | `microsoft/skills`, `.github/skills/frontend-design-review` | Design system compliance, UI review, accessibility and responsive review | PR reviews, component reviews, design QA, UI code review | `C:\Users\tinif\.codex\skills\frontend-design-review` | MIT repo from Microsoft. Copied skill only plus license. | Detected by `codex debug prompt-input`. |
| `ui-audit` | `mblode/agent-skills`, `skills/ui-audit` | Concrete UI quality audit across a11y, forms, typography, layout, motion, microcopy | Pre-release UI audit, polish pass, accessibility review | `C:\Users\tinif\.codex\skills\ui-audit` | MIT repo, focused rule files. No executable install scripts used. | Detected by `codex debug prompt-input`. |
| `ux-audit` | `mblode/agent-skills`, `skills/ux-audit` | Feature-level UX readiness checks for React/Next.js flows | Before merging or shipping forms, dashboards, checkout, onboarding, modals | `C:\Users\tinif\.codex\skills\ux-audit` | MIT repo. Large but readable rule set. No unsafe commands required. | Detected by `codex debug prompt-input`. |
| `tailwind` | `kid-sid/codex-spellbook`, `skills/tailwind` | Tailwind utility composition, responsive layouts, variants, class cleanup | Tailwind/CSS polish and component styling | `C:\Users\tinif\.codex\skills\tailwind` | MIT repo. Skill is static guidance. Copied license. | Detected by `codex debug prompt-input`. |
| `code-review-and-quality` | `addyosmani/agent-skills`, `skills/code-review-and-quality` | Production-readiness review, maintainability, risk checks, test gaps | Code review before merge, cleanup and quality passes | `C:\Users\tinif\.codex\skills\code-review-and-quality` | MIT repo, no dangerous patterns found. Copied license. | Detected by `codex debug prompt-input`. |
| `supabase` | `supabase/agent-skills`, `skills/supabase` | Current Supabase product guidance, Auth/RLS/SSR/CLI/MCP safety | Any Supabase task involving Auth, DB, Edge Functions, Realtime, Storage, migrations | `C:\Users\tinif\.codex\skills\supabase` | Official Supabase repo, MIT. Includes explicit credential and destructive-SQL guardrails. | Detected by `codex debug prompt-input`. |
| `supabase-postgres-best-practices` | `supabase/agent-skills`, `skills/supabase-postgres-best-practices` | Postgres query, schema, index, RLS, and performance guidance | SQL review, Postgres optimization, schema design | `C:\Users\tinif\.codex\skills\supabase-postgres-best-practices` | Official Supabase repo, MIT. Static references only. | Detected by `codex debug prompt-input`. |

## Smoke Test

Created a temporary React/Vite app:

`C:\Users\tinif\AppData\Local\Temp\codex-skill-smoke-vite-20260527-100614`

Verification performed:

- Ran nested `codex exec` and explicitly requested `frontend-design`.
- Nested Codex loaded `C:\Users\tinif\.codex\skills\frontend-design\SKILL.md`.
- It edited only `src/App.tsx` and `src/App.css`.
- `npm run build` passed.
- `npm run lint` passed.
- Started Vite on `http://127.0.0.1:5177`.
- Captured screenshots with Playwright CLI:
  - `C:\Users\tinif\AppData\Local\Temp\codex-skill-smoke-vite-20260527-100614\smoke-desktop.png`
  - `C:\Users\tinif\AppData\Local\Temp\codex-skill-smoke-vite-20260527-100614\smoke-mobile.png`
- Checked browser console errors with Playwright CLI: 0 errors.

## Candidates Skipped

| Candidate | Reason |
|---|---|
| `Ilm-Alan/frontend-design` | Good and active, but duplicates the `frontend-design` skill name. I installed the more trusted official Anthropic source to avoid trigger conflicts. |
| `hursh-shah/codex-design-skill` | Codex-specific and readable, but very low activity/stars and overlaps installed UI design skills. |
| `yutori-ai/frontend-visualqa` | Interesting visual QA skill, but requires Yutori auth/API setup. I chose the official OpenAI Playwright skill for local screenshot verification. |
| `ComposioHQ/awesome-codex-skills/webapp-testing` | Useful but overlaps the official OpenAI Playwright skill. |
| Chrome extension skills found by search | No focused, trustworthy Chrome-extension Codex skill candidate surfaced during this pass. |

## Notes

- Existing `.codex` secret/auth/session/log files were not printed or copied into the report.
- Backup included the existing `skills` folder and non-secret config files (`config.toml`, `version.json` if present, and `AGENTS.md` if present).
- `C:\Users\tinif\.codex\AGENTS.md` was created with the requested UI, verification, dependency, and summary preferences.
