---
name: save-session
description: Document session content and create handoff notes for the next session
argument-hint: "[topic]"
---

# Session Documentation + Handoff

Analyze the current session's discussions and save as a markdown file.

**Key principle**: Write so the next session (or another agent) can continue work by reading only this document.

## Save Location

All sessions are saved to a **global iCloud-synced directory**, organized by project:

```
~/Documents/claude-sessions/{project-name}/{category}/{filename}.md
```

- **project-name**: Detect from git repo name (`git rev-parse --show-toplevel | xargs basename`), or current directory name if not a git repo
- **category**: Auto-classified from session content (see table below)
- This directory syncs across MacBooks via iCloud (Desktop & Documents sync)

## Usage

```
/save-session           # Document full session + handoff
/save-session [topic]   # Document specific topic only
```

## Directory Classification

Analyze conversation content and auto-select category:

| Category | Keywords/Topics | Examples |
|----------|----------------|---------|
| `setup` | setup, environment, install, init, dotfiles | Dev environment setup guide |
| `architecture` | design, structure, pattern, architecture, migration | System design decisions |
| `features` | feature, requirements, spec, API design | Feature planning |
| `operations` | outage, monitoring, alerts, deployment, rollback | Operational issue response |
| `security` | incident, leak, attack, abuse, vulnerability, credential | Incident response |
| `infra` | GCP, AWS, cloud, IAM, terraform, CI/CD | Infrastructure work |
| `work-logs` | daily work, progress | Work logs |
| `notes` | anything not matching above | General notes |

**Priority**: If multiple categories match, pick the most specific one.

## Filename Rules

- Date-prefixed kebab: `2026-02-18-session-topic.md`
- Or uppercase kebab: `SESSION-TOPIC.md`
- Follow similar naming if related files exist in the directory

## Workflow

1. **Detect project name**: From git root or current directory
2. **Analyze session**: Identify main topics, conclusions, action items
3. **Select category**: Auto-determine based on criteria above
4. **Generate filename**: Based on topic
5. **Write document**: In structured format (see template below)
6. **Save**: Create directory tree if needed, then save
7. **Output result**: Notify with save location and summary

## Document Template

```markdown
# {Title}

## Goal
{One-sentence final goal of this work}

## Overview
{One paragraph summary - what was done and why}

## Progress

### Completed
- {Checklist format}

### Attempted but Failed (if any)
- {Failed approaches and reasons}

### Pending/Unresolved (if any)
- {Undecided items}

## Details
{Core content - tables, code blocks, commands}
{Specific information for the next session}

## Conclusions/Decisions
{What was decided}

## Next Steps (Handoff)
{What the next session/agent should do}
{Listed by priority}

## Context for Next Session
{Background info the next session needs}
{Related file paths, commands, caveats}

## Related Documents
{Links to related docs in the same directory}
```

## Output Format

```
## Session Documentation Complete

Location: `~/Documents/claude-sessions/dotfiles/setup/2026-02-18-permission-patterns.md`
Project: dotfiles
Category: Setup

### Summary
- Applied good permission patterns for Claude Code settings
- Split settings.json (committed) vs settings.local.json (personal)
- Added global deny rules for sensitive files

Save another topic?
```
