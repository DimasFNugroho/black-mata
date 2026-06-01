# Config-Based Default Drive Mode — Plan

Allow the operator (via the Ackermann UI) to set which drive mode — **WASD**
or **Touchpad** — the dashboard uses when a user first opens it. The chosen
default is stored in `ackermann_config.json` and applied every time the
dashboard loads, without the user having to manually toggle.

---

## Design Decision — config is authoritative; localStorage removed

Two options were considered:

| Option | On page load | In-session toggle |
|---|---|---|
| **Config authoritative (chosen)** | always starts at the config default | ephemeral; resets on next load |
| Config + localStorage | localStorage wins if present, else config | persists in browser |

The robot dashboard is a shared operator tool, not a personal app. The operator
who configures the robot (via ackermann_ui) should be able to set the intended
drive mode for *all* users who open the dashboard on that robot — not have it
overridden by a previous browser session. `localStorage` is removed from the
drive mode path entirely.

In-session toggles still work: switching mode mid-session is fine and takes
effect immediately. It simply resets to the config default on the next page
load, which is the expected operator-controlled behaviour.

---

## Config field

```json
{
  "default_drive_mode": "wasd"
}
```

- Values: `"wasd"` (default) or `"touchpad"`.
- Stored in `tools/ackermann_ui/ackermann_config.json` alongside the other
  robot parameters.
- If absent or unrecognised, the dashboard falls back to `"wasd"` (unchanged
  from current behaviour — no regression for existing deployments).

---

## Files touched

| File | Change |
|---|---|
| `tools/ackermann_ui/server.py` | Add `default_drive_mode` to `_default_config()`; pass it through the `/config` GET so the dashboard can read it |
| `tools/ackermann_ui/server.py` | Add a drive-mode toggle control in the ackermann_ui HTML (segmented button, same style as the dashboard toggle); wire to the `/config` POST save |
| `tools/dashboard/static/app.js` | On `loadConfig()` callback: call `setDriveMode(_cfg.default_drive_mode \|\| 'wasd')` only on the **first** load (use a `_modeInitialised` flag so the 5 s re-poll doesn't forcibly reset an in-session toggle); remove `localStorage.getItem/setItem` from the drive-mode path |

No HTML or CSS changes to the dashboard needed — `setDriveMode()` already
exists and works.

---

## Implementation Phases

**Phase C-1 — Config field + server-side default**
- Add `'default_drive_mode': 'wasd'` to `_default_config()` in
  `tools/ackermann_ui/server.py`.
- Verify the field appears in the `/config` GET response consumed by the
  dashboard.

**Phase C-2 — Dashboard reads config on first load**
- In `loadConfig()` callback: if `!_modeInitialised`, call
  `setDriveMode(_cfg.default_drive_mode || 'wasd')` and set
  `_modeInitialised = true`.
- Remove `localStorage.getItem('driveMode')` initialisation and
  `localStorage.setItem('driveMode', mode)` from `setDriveMode()`.

**Phase C-3 — Ackermann UI control**
- Add a "Default drive mode" segmented toggle (WASD / Touchpad) to the
  ackermann_ui settings panel.
- On click, POST the updated config to `/config` (same pattern as all other
  ackermann_ui saves).
- Show a brief "Saved" confirmation (matches existing ackermann_ui UX).

**Phase C-4 — Docs**
- Update README "Tuning with the Ackermann UI" section to mention the drive
  mode default setting.
- Update `docs/software_architecture/index.html` Feature 4 row.

---

## Test Checklist

Implemented in commits `f9559d7`+ (config plan) and the drive-mode-config commit. Pending hardware verification:

- [ ] Fresh dashboard load uses WASD when config has `default_drive_mode: "wasd"`.
- [ ] Fresh dashboard load uses Touchpad when config has `default_drive_mode: "touchpad"`.
- [ ] Toggling mid-session works; next page reload reverts to config default.
- [ ] Config missing `default_drive_mode` field → dashboard defaults to WASD (no error).
- [ ] Ackermann UI toggle saves to file; dashboard picks it up on next load.
- [ ] 5 s config re-poll does NOT reset the mode mid-session.
