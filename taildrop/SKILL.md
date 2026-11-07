---
name: taildrop
description: >-
  Send and receive files to/from any device on the Tailscale tailnet using
  Taildrop. Use when the user asks to send or taildrop a file to a device or
  phone, ship a build to a device, or check/retrieve pending Taildrop files.
metadata:
  author: cobur
---

# Taildrop

Transfer files between any two devices on the tailnet with the Tailscale CLI
(`tailscale file`).

## Prerequisites

- Both sender and receiver must have Tailscale installed and connected to the
  same tailnet.
- The receiver must be online: check `tailscale status` (look for `active`).
  A device not listed is not connected, so the drop will wait.

## Finding device names

`tailscale status` lists every device with its tailnet name and IP. Commands
take the **device name**, not the IP.

## Send a file

```
tailscale file cp <source-file> <device-name>:
```

The trailing colon means "drop into the device's Taildrop inbox" — no remote
path needed. The receiver gets a notification; on Android the file appears in
the Files app (Taildrop/Downloads area).

Examples:

```
tailscale file cp app-debug.apk my-phone:
tailscale file cp notes.pdf my-laptop:
tailscale file cp ./build/out.zip my-phone:
```

On Windows, quote paths that contain spaces:
`tailscale file cp "C:\My Files\report.pdf" my-phone:`

## List / retrieve pending files

```
tailscale file ls            # files pending in this machine's Taildrop inbox
tailscale file get <name>    # download a file someone taildropped to this machine
```

## Gotchas

- The subcommand is **`file cp`**, not `file send` (old syntax fails on modern
  Tailscale).
- On success the command prints **nothing** and exits 0. No output = success.
- Large files (APKs, archives, media) are fine, but give the shell a generous
  timeout for big transfers.
- If the receiver is offline, the file stays queued on the sender and is
  delivered when the receiver reconnects (as long as the sender stays online).
