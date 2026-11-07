---
name: ssh-mcp
description: Configure and troubleshoot the ssh-mcp MCP server (npm package ssh-mcp by tufantunc) for remote SSH command execution, SFTP, and persistent sessions. ALWAYS set it up as a project-local MCP, never global. Use when the user asks to connect a remote host via ssh-mcp, add an SSH MCP server to a project, or when ssh-mcp fails to start.
---

## ssh-mcp

Security-first MCP server (npm: `ssh-mcp`) that gives the agent controlled SSH access to a remote host: command classification, approval gates, sudo elevation, SFTP, and named sessions. This skill covers setup as a **project-local** MCP and the startup failure modes that actually occur.

### Core rule: project-local only

Never add ssh-mcp to the global OpenCode config (`~/.config/opencode/opencode.jsonc`) and never use the platform-global config path (`%APPDATA%\ssh-mcp\config.toml` on Windows, `~/.config/ssh-mcp/config.toml` on Linux). Each project gets its own MCP entry and its own TOML, so host credentials and authorization policy stay scoped to that project and other projects can target different hosts.

Two files, both inside the project:

1. `<project>/.opencode/opencode.json` — MCP entry
2. `<project>/.opencode/ssh-config.toml` — connection profile(s)

### MCP entry (.opencode/opencode.json)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ssh-mcp": {
      "type": "local",
      "command": ["npx", "-y", "ssh-mcp@latest", "--config=.opencode/ssh-config.toml"]
    }
  }
}
```

- The `--config` flag **must use the equals form**: `--config=<path>`. The space form (`--config <path>`) makes the server exit immediately with `--config needs a path: --config=<path>`.
- The relative path resolves against the project directory (the MCP server's working directory), so the TOML stays inside the project and travels with it.

### Connection profile (.opencode/ssh-config.toml)

```toml
[defaults]
defaultProfile = "<profile-name>"
approvalMode = "ask-destructive"

[[profiles]]
name = "<profile-name>"
host = "<host-or-ip>"
port = 22
user = "<user>"
auth = "key"
keyRef = "<absolute-path-to-private-key>"
role = "admin"
approvalPolicy = "auto"
```

- **Never point it at a root account.** Use a regular user with passwordless sudo; the `privileged-command` tool handles elevation through the approval gate.
- **Set `group` explicitly.** A profile without a `group` gets its host tier inferred from the profile name, defaulting to `prod` — where the `admin` role is denied `privileged` commands (`POLICY_DENIED ... cannot run "privileged" commands`). Development/IoT boxes need `group = "dev"` on the profile or `privileged-command` will be blocked.
- The server reads the TOML **at startup only** — after editing it, restart OpenCode (or the server process) before expecting the change to apply.
- `approvalPolicy`: `auto` = no prompts (permissive, fine for a dedicated home/IoT box); omit it to inherit `ask-destructive` (destructive/privileged commands prompt for human approval).
- Multiple hosts: add more `[[profiles]]` blocks; each tool call takes an optional `profile` argument.
- Password auth (if ever needed) goes through the `SSH_MCP_PASSWORD` environment variable in the MCP entry, never a CLI argument.
- Host keys: trust-on-first-use, held in memory for the process lifetime; a later mismatch fails the connection.

### Windows ACL requirement (most common startup failure)

On Windows the server reads the NTFS ACL of the config file **and its directory** and refuses to start (exit 2) if any account other than the owner, SYSTEM, and Administrators can modify them. Files created under a normal drive folder inherit broad entries (Authenticated Users, BUILTIN\Users) and will fail this check.

Fix by restricting the TOML file first, then its directory (the server checks in that order and prints the exact commands; run them as printed, in order):

```
icacls "<config-file>" /inheritance:d
icacls "<config-file>" /grant:r "<owner>:(OI)(CI)F" /remove:g *S-1-5-11 /remove:g *S-1-5-32-545
icacls "<config-dir>"  /inheritance:d
icacls "<config-dir>"  /grant:r "<owner>:(OI)(CI)F" /remove:g *S-1-5-11 /remove:g *S-1-5-32-545
```

`*S-1-5-11` = Authenticated Users, `*S-1-5-32-545` = BUILTIN\Users. Step 1 of each pair converts inherited entries into explicit ones (inherited entries cannot be removed while inherited). Restricting the directory affects sibling files in it — only do this for a project-owned folder.

On Linux/macOS the equivalent is a mode check: `chmod 700 <config-dir> && chmod 600 <config.toml>`, or the server refuses to start.

### Verification

1. From the project directory, run the server directly with a short timeout:
   `npx -y ssh-mcp@latest --config=.opencode/ssh-config.toml`
   It blocks on stdio, so a timeout/kill after ~15 s is expected. Success prints `SSH MCP Server v2 running on stdio`. Any other output is the failure reason (missing `=` form, ACL rejection, bad TOML).
2. Restart OpenCode (or start a new session) so the MCP server is (re)spawned with the corrected command.
3. Call the `list-connections` tool to confirm the profile and its connection status.

### Exit statuses

- `0` clean shutdown
- `1` server defect (stack trace; report upstream)
- `2` invocation/config problem (message, no stack) — the flag-form and ACL failures exit 2

### Tools (14)

- `list-connections` — configured profiles + status (read-only)
- `list-sessions` / `open-session` / `close-session` / `read-session-output` — named interactive and background sessions (e.g. `tail -f`)
- `read-command` — allowlisted read-only commands (`ls`, `cat`, `grep`, ...)
- `run-command` — arbitrary commands; destructive/privileged go through the approval gate
- `privileged-command` — sudo elevation
- `sftp-upload` / `sftp-download` / `sftp-list` / `sftp-upload-file` — file transfer (`sftp-upload` replaces the destination unconditionally)

### Best Practices

- Keep the TOML in `.opencode/` with the MCP entry so the whole setup is visible in the project and reviewable in one place.
- Pin the remote host's address in your router/DHCP (reservation) rather than making the host static, if the host's OS manages its own network config — the profile just needs a stable address to dial.
- After changing the TOML or the MCP entry, re-run the direct verification step before restarting OpenCode; it fails fast and prints the exact problem.
- If the server starts but every tool call is refused with a message naming a config path, the `--config` path did not resolve from the server's working directory.
