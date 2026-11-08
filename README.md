# Opencode Skills

A collection of [OpenCode](https://opencode.ai) skills. Each folder is one skill, defined by a `SKILL.md` (with optional supporting files like `references/`). OpenCode auto-loads every skill it finds in its skills directory and triggers them by name matching.

## Install

Clone this repo into your global opencode config:

```sh
git clone https://github.com/Batman313v/skills.git ~/.config/opencode/skills
```

Or, if you already have a `skills/` folder with skills of your own, copy the skill folders you want into it:

```sh
cp -R <this-repo>/react-native-best-practices ~/.config/opencode/skills/
```

No restart needed — OpenCode watches the config directory and picks up new skills automatically.

## Layout

| Folder | Purpose |
|--------|---------|
| `_template` | Blank scaffold for writing a new skill |
| `<name>` | One skill per folder; `SKILL.md` frontmatter (`name`, `description`) drives auto-triggering |

## Writing a skill

Copy `_template/SKILL.md` into a new folder, fill in the frontmatter description (this is what OpenCode matches against when deciding to load it), and keep the body concise — skills are injected into the model's context.
