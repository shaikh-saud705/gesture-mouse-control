# Gesture OS Control 🤚🖱️

**Two-handed gesture mouse and system controller for Linux. Use your webcam to move the cursor, click, drag, scroll, adjust volume, switch workspaces, and launch apps — all without touching anything.**

Right hand controls the mouse. Left hand controls system functions. Built on MediaPipe hand-tracking, with engineering tricks to make gestures stable, low-latency, and immune to camera distance.

> *Tested on Pop!_OS 22.04 with Wayland. Should work on any Linux distro with X11 or Wayland.*

---

## ✨ Features

**Right hand — mouse control**
- Move the cursor with an open hand
- Pinch thumb + index → click & drag
- Pinch thumb + middle → right click
- Pinch thumb + ring → middle click (scroll button)
- Bring index + middle close → scroll up/down
- Closed fist → pause everything

**Left hand — system control**
- 👌 OK sign → toggle app launcher
- ✌️ Peace sign → unlock/lock volume slider
- 🤙 Shaka sign → lock all hand controls (security)
- Thumb + middle pinch (with index up) → Super+Tab (workspace switch)
- Open hand (after unlock) → adjust volume by thumb-index distance

**Engineering smarts that make it actually usable**
- **1-Euro filter** — no jitter at rest, no lag during fast moves
- **Hysteresis on every gesture** — separate engage/release thresholds prevent flicker
- **Scale-invariant detection** — works the same at any camera distance
- **Cursor freeze on pinch** — prevents pull-off when clicking small UI targets
- **Edge-reachability scaling** — camera area 15–85% maps to full screen, so corners are reachable
- **Anti-background-hand filter** — ignores tiny/distant hands so passersby can't take over your mouse
- **Volume rollback safety** — cancel a volume gesture and it reverts to where it was 0.5s before you started
- **Baton transfer** — left hand becomes the mouse during launcher mode, then hands control back

---

## 📋 Requirements

- **OS:** Linux (Wayland or X11)
- **Python:** 3.10+
- **Hardware:** any webcam — built-in or USB
- **System packages:** `ydotool`, `xdotool`, `wireplumber` (`wpctl`), `libnotify` (`notify-send`), `wl-clipboard`

---

## 🛠️ Installation

### 1. Install system tools

```bash
# Ubuntu / Debian / Pop!_OS
sudo apt install ydotool xdotool wireplumber libnotify-bin wl-clipboard

# Fedora
sudo dnf install ydotool xdotool wireplumber libnotify wl-clipboard

# Arch
sudo pacman -S ydotool xdotool wireplumber libnotify wl-clipboard
```

### 2. Set up the ydotool daemon

`ydotool` needs a background daemon (`ydotoold`) to send input events:

```bash
systemctl --user enable --now ydotoold.service
systemctl --user status ydotoold
```

If you get permission errors, add your user to the `input` group, then log out and back in:

```bash
sudo usermod -aG input $USER
```

### 3. Install Python dependencies

```bash
pip install opencv-python mediapipe
```

### 4. Clone and run

```bash
git clone https://github.com/YOUR_USERNAME/gesture-mouse-control.git
cd gesture-mouse-control
python3 mouse_control.py
```

Press **q** in the camera preview window to quit.

---

## 🤲 Gesture Reference

### Right hand → mouse

| Gesture | Action | Notes |
|---|---|---|
| Open hand 👋 | Move cursor | Point with whole hand |
| Pinch thumb + index 🤏 | Click / Drag | Hold to drag, release to click |
| Pinch thumb + middle | Right click | Fires once per gesture |
| Pinch thumb + ring | Middle click | The scroll-wheel button |
| Index + middle close together | Scroll | Move hand up/down to scroll |
| Closed fist ✊ | Pause | Cursor freezes, no clicks fire |

### Left hand → system

| Gesture | Action | Notes |
|---|---|---|
| OK sign 👌 | Open/close app launcher | Switches "mouse" to left hand |
| Peace sign ✌️ | Unlock/lock volume | Hold ring + pinky down with thumb |
| Shaka 🤙 | Toggle hand-control lock | Pinky up only — disables all controls |
| Pinch thumb + middle (index up) | Super+Tab | Workspace switcher |
| Open hand (after Peace unlock) | Adjust volume | Thumb-index distance maps to 0–100% |

---

## ⚙️ Configuration

All tunables are at the top of `mouse_control.py`:

```python
FRAME_WIDTH = 480              # webcam capture width
FRAME_HEIGHT = 360             # webcam capture height
PINCH_THRESHOLD = 0.4          # how close fingers must be to register a pinch
SCROLL_THRESHOLD = 0.3         # closeness for scroll gesture
PAUSE_THRESHOLD = 1.0          # closeness for pause gesture

# Camera mapping bounds — crops the camera area so screen edges are reachable
CAM_X_MIN = 0.15
CAM_X_MAX = 0.85
CAM_Y_MIN = 0.15
CAM_Y_MAX = 0.85
```

### App launcher path

By default, `mouse_control.py` looks for `app_launcher_gui.py` in the **same folder** as itself. If you keep them apart, edit this near the top of `mouse_control.py`:

```python
APP_LAUNCHER_PATH = "/full/path/to/app_launcher_gui.py"
```

### Customizing launcher buttons

The default launcher has three buttons: Chrome, Spotify, and AWS Kiro CLI. To change them, edit `app_launcher_gui.py`:

```python
btn1 = tk.Button(f, text="🌐 Chrome",
                 command=lambda: launch_app("google-chrome"),
                 **btn_style)

btn2 = tk.Button(f, text="🎵 Spotify",
                 command=lambda: launch_app("spotify"),
                 **btn_style)

btn3 = tk.Button(f, text="🤖 AI Terminal",
                 command=lambda: launch_app("gnome-terminal -- kiro-cli"),
                 **btn_style)
```

Replace the shell command inside each `launch_app("...")` with whatever app you want to open. You can also add or remove buttons.

### About the AWS Kiro CLI button

The third button (`🤖 AI Terminal`) opens AWS Kiro CLI in a new terminal. **If you don't have Kiro installed, the button does nothing.** You have two options:

1. **Install Kiro CLI** — see [AWS Kiro documentation](https://docs.aws.amazon.com/kiro/) for instructions
2. **Replace the button** — edit `app_launcher_gui.py` and change `kiro-cli` to whatever AI tool or terminal command you prefer

---

## ⚠️ Known Issues

- **Auto-recenter is unreliable.** When the right hand re-enters the frame after being absent for 0.5s+, the program tries to snap the cursor to screen center. On some Wayland/ydotool combinations the cursor lands at the bottom-right corner instead. The rest of the gesture system is unaffected. If this annoys you, comment out the `snap_to_center(sw, sh)` call inside the main loop.
- **Linux only.** Depends on `ydotool`, `wpctl`, and `wl-paste`. macOS and Windows are not supported.
- **First-frame jitter.** MediaPipe needs ~0.4s to stabilize when a hand enters the frame. The program already debounces the left hand for this reason.

---

## 🩹 Troubleshooting

### `ydotool` fails with permission denied
Make sure the daemon is running and your user is in the `input` group:
```bash
systemctl --user status ydotoold
groups | grep input
```

### Camera not opening
The program tries camera index 2 (external webcam) first, then falls back to 0 (built-in). If you have a different setup, edit lines 326–328 in `mouse_control.py`.

### Cosmetic warnings on startup
You may see these warnings — **they are all harmless**:

- `AttributeError: 'MessageFactory' object has no attribute 'GetPrototype'` → MediaPipe / protobuf version mismatch. Doesn't affect functionality.
- `qt.qpa.plugin: Could not find the Qt platform plugin "wayland"` → OpenCV's Qt rendering on Wayland. Camera preview still works.
- `QFontDatabase: Cannot find font directory` → Cosmetic. Silence with `sudo apt install fonts-dejavu`.

### Cursor moves erratically
- Lighting matters — MediaPipe needs to see your hand clearly
- Try adjusting `PINCH_THRESHOLD` and other CONFIG values
- Lower webcam framerate makes the 1-Euro filter underperform; try a different camera if available

### Quitting with Ctrl+C leaves modifier keys held
Use the **q** key in the camera preview window instead of Ctrl+C. The 'q' path runs cleanup; Ctrl+C bypasses it.

---

## 🧰 Tech Stack

- [MediaPipe Hands](https://google.github.io/mediapipe/solutions/hands.html) — 21-landmark hand tracking
- [OpenCV](https://opencv.org/) — webcam capture and preview rendering
- [ydotool](https://github.com/ReimuNotMoe/ydotool) — uinput-based input control (Wayland-compatible)
- [xdotool](https://www.semicomplete.com/projects/xdotool/) — X11 display geometry
- [WirePlumber](https://gitlab.freedesktop.org/pipewire/wireplumber) — PipeWire volume control
- [tkinter](https://docs.python.org/3/library/tkinter.html) — app launcher GUI

---

## 📁 Project Structure

```
gesture-mouse-control/
├── mouse_control.py       # Main program — gesture detection + cursor control
├── app_launcher_gui.py    # Tkinter launcher (opened by 👌 OK sign)
├── README.md              # This file
├── .gitignore
└── LICENSE
```

---

## 📜 License

MIT — see [LICENSE](LICENSE) for details.

---

Built by **Sheikh Saud** — B.Tech Data Science student, embedded systems & maker hobbyist.
