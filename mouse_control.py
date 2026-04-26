import cv2
import mediapipe as mp
import time
import math
import subprocess
import sys
import os
import threading
import json
import urllib.request

# ================= CONFIG =================
FRAME_WIDTH = 480
FRAME_HEIGHT = 360
PINCH_THRESHOLD = 0.4  # Ratio of thumb-index distance to palm size
SCROLL_THRESHOLD = 0.3 # Ratio
PAUSE_THRESHOLD = 1.0  # Ratio

# Path to the app launcher GUI script.
# By default this looks for app_launcher_gui.py in the SAME FOLDER as this script,
# so as long as you keep both files together you don't need to edit anything.
# If you keep the launcher elsewhere, change the path below to the full absolute path,
# e.g. APP_LAUNCHER_PATH = "/home/yourname/projects/gesture-os/app_launcher_gui.py"
APP_LAUNCHER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "app_launcher_gui.py"
)

# Camera mapping bounds to prevent edge reachability issues
CAM_X_MIN = 0.15
CAM_X_MAX = 0.85
CAM_Y_MIN = 0.15
CAM_Y_MAX = 0.85

# ================= INIT =================
hands = mp.solutions.hands.Hands(
    max_num_hands=2,
    model_complexity=1, # Maximizes accuracy (similar to using GPU model on backend)
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)
draw = mp.solutions.drawing_utils

session_type = os.environ.get('XDG_SESSION_TYPE', '').lower()
print(f"Display server: {session_type}")

try:
    result = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True)
    sw, sh = map(int, result.stdout.strip().split())
except:
    sw, sh = 1920, 1080
print(f"Screen: {sw}x{sh}")

# ================= 1-EURO FILTER =================
class OneEuroFilter:
    def __init__(self, mincutoff=1.0, beta=0.007, dcutoff=1.0):
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None

    def __call__(self, t, x):
        if self.t_prev is None:
            self.x_prev = x
            self.t_prev = t
            return x
        te = max(t - self.t_prev, 1e-5)
        ad = self.alpha(te, self.dcutoff)
        dx = (x - self.x_prev) / te
        dx_hat = ad * dx + (1 - ad) * self.dx_prev
        cutoff = self.mincutoff + self.beta * abs(dx_hat)
        a = self.alpha(te, cutoff)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        return x_hat

    def alpha(self, t_e, cutoff):
        r = 2 * math.pi * cutoff * t_e
        return r / (r + 1)

# ================= STATE =================
# Tuned 1-Euro filter mapping: lower mincutoff for rest stability, 
# heavily reduced beta because our coordinates are measured in hundreds of pixels.
filter_x = OneEuroFilter(mincutoff=0.01, beta=0.005, dcutoff=1.0)
filter_y = OneEuroFilter(mincutoff=0.01, beta=0.005, dcutoff=1.0)

last_ydotool_x = -1
last_ydotool_y = -1

is_mouse_down = False
is_dragging = False
pinch_start_time = None
last_right_click = 0
last_scroll_y = None

# Hysteresis states to prevent accidental click drop-offs
is_index_pinched = False
is_middle_pinched = False
is_ring_pinched = False
is_pinky_pinched = False

is_ctrl_down = False
is_shift_down = False
last_ai_trigger = 0

is_locked = False
last_lock_toggle = 0
current_volume = -1
last_super_tab = 0

is_volume_unlocked = False
left_hand_first_seen = 0
last_peace_toggle = 0
is_peace_held = False
vol_history = []
is_launcher_open = False
launcher_proc = None
last_launcher_toggle = 0

# NEW FEATURE: Track when right hand was last visible. Initialized to a long-ago time
# so that the FIRST time a hand appears (program startup), the cursor snaps to center.
right_hand_last_seen = -10.0
RECENTRE_DEBOUNCE = 0.5  # seconds of absence required before re-entry triggers a snap

# ================= HELPERS FOR OLLAMA =================
def get_clipboard():
    try:
        return subprocess.run(["wl-paste"], capture_output=True, text=True).stdout.strip()
    except:
        try:
            return subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True).stdout.strip()
        except:
            return ""

def query_ollama_thread():
    print("OLLAMA: Grabbing clipboard & querying...")
    subprocess.run(["ydotool", "key", "29:1", "46:1", "46:0", "29:0"])
    time.sleep(0.3)
    
    text = get_clipboard()
    if not text:
        subprocess.run(["notify-send", "Ollama AI", "Nothing highlighted!"])
        return
        
    subprocess.run(["notify-send", "-t", "3000", "Ollama AI", f"Processing: {text[:30]}..."])
    
    prompt = f"Briefly explain, summarize, or fix the following text in a few short sentences:\n\n{text}"
    data = json.dumps({
        "model": "llama3.1:latest",
        "prompt": prompt,
        "stream": False
    }).encode('utf-8')
    
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=30) as response:
            res_body = response.read()
            ai_text = json.loads(res_body).get("response", "No response.")
            # Sending as notification. Long text might truncate based on notify-osd setup, but it's seamless
            subprocess.run(["notify-send", "Ollama Response", ai_text])
            print(f"OLLAMA Response: {ai_text}")
    except Exception as e:
        print("OLLAMA ERROR:", e)
        # subprocess.run(["notify-send", "Ollama Error", str(e)])

# ================= HELPERS =================
def dist(a, b):
    # Using true 3D distance to allow users to move their hands naturally in 3D 
    # without breaking logic. The hysteresis handles the Z-axis noise!
    return math.sqrt((a.x - b.x)**2 + (a.y - b.y)**2 + (a.z - b.z)**2)

def detect_right(lm):
    global is_index_pinched, is_middle_pinched, is_ring_pinched, is_pinky_pinched
    
    # Scale invariant palm measurement! This makes gestures perfectly immune to varying camera distances.
    palm_size = dist(lm[0], lm[9])
    if palm_size < 0.001: palm_size = 0.001
    
    r_thumb_index = dist(lm[4], lm[8]) / palm_size
    r_thumb_middle = dist(lm[4], lm[12]) / palm_size
    r_index_middle = dist(lm[8], lm[12]) / palm_size
    
    r_wrist_index = dist(lm[0], lm[8]) / palm_size
    r_wrist_middle = dist(lm[0], lm[12]) / palm_size
    r_wrist_ring = dist(lm[0], lm[16]) / palm_size
    r_wrist_pinky = dist(lm[0], lm[20]) / palm_size
    
    # Hysteresis for stable click/drag recognition
    if is_index_pinched:
        # Widen release threshold massively (1.0 ratio). Solves edge-distortion when dragging arm to the bottom!
        if r_thumb_index > 1.0 or r_wrist_index > 2.0:
            is_index_pinched = False
    else:
        # Index finger must be anatomically curled (ratio < 1.4) to prevent false pinches when pointing straight!
        if r_thumb_index < PINCH_THRESHOLD and r_wrist_index < 1.4:
            is_index_pinched = True
            
    if is_middle_pinched:
        if r_thumb_middle > 1.2 or r_wrist_middle > 2.0:
            is_middle_pinched = False
    else:
        # Increased pinch threshold to 0.55 for Middle finger to make Right Clicking much easier!
        if r_thumb_middle < 0.55 and r_wrist_middle < 1.4:
            is_middle_pinched = True
            
    if is_ring_pinched:
        if r_wrist_ring > 2.0: 
            is_ring_pinched = False
    else:
        if r_wrist_ring < 1.4 and dist(lm[4], lm[16]) / palm_size < 0.55:
            is_ring_pinched = True
            
    if is_pinky_pinched:
        if r_wrist_pinky > 2.0:
            is_pinky_pinched = False
    else:
        if r_wrist_pinky < 1.4 and dist(lm[4], lm[20]) / palm_size < 0.55:
            is_pinky_pinched = True
    
    # DRAG has ultra-priority! If physical grip is maintained, it cannot be aborted by random finger stretching or overlapping gestures.
    if is_index_pinched:
        return "DRAG"

    # Secondary actions only evaluated if not dragging/clicking
    if is_middle_pinched and not is_index_pinched:
        return "RIGHT_CLICK"
        
    if is_ring_pinched and not is_index_pinched and not is_middle_pinched:
        return "MIDDLE_CLICK"
        
    if r_index_middle < SCROLL_THRESHOLD and not is_index_pinched and not is_middle_pinched and not is_ring_pinched:
        return "SCROLL"

    if r_wrist_index < PAUSE_THRESHOLD and r_wrist_middle < PAUSE_THRESHOLD and r_wrist_ring < PAUSE_THRESHOLD:
        return "PAUSE"
        
    return "MOVE"

def detect_left(lm):
    palm_size = dist(lm[0], lm[9])
    if palm_size < 0.001: palm_size = 0.001
    
    r_thumb_index = dist(lm[4], lm[8]) / palm_size
    r_thumb_middle = dist(lm[4], lm[12]) / palm_size
    r_thumb_pinky = dist(lm[4], lm[20]) / palm_size
    r_wrist_index = dist(lm[0], lm[8]) / palm_size
    r_wrist_middle = dist(lm[0], lm[12]) / palm_size
    r_wrist_ring = dist(lm[0], lm[16]) / palm_size
    r_wrist_pinky = dist(lm[0], lm[20]) / palm_size
    
    # APP LAUNCHER GESTURE: OK Sign (Thumb+Index pinched. Middle, Ring, Pinky rigidly fanned UP)
    if r_thumb_index < 0.55 and r_wrist_middle > 1.4 and r_wrist_ring > 1.4 and r_wrist_pinky > 1.4:
        return "OK_SIGN"

    # PASSWORD GESTURE: Sign of Peace (Index & Middle UP. Ring/Pinky DOWN. Thumb holding Ring/Pinky down)
    # Evaluated at the top to perfectly intercept!
    if r_wrist_index > 1.4 and r_wrist_middle > 1.4:
        if r_wrist_ring < 1.2 and r_wrist_pinky < 1.2:
            if dist(lm[4], lm[16]) / palm_size < 1.0: # Thumb must be folded inwards, NOT spread wide out!
                return "PEACE"

    # Strict 'Security Lock' configured to the "Shaka" (Surfer) sign to COMPLETELY isolate it from Peace/Tab transitions!
    # Pinky UP. Index, Middle, Ring all DOWN tightly into palm. 
    if r_wrist_pinky > 1.4 and r_wrist_index < 1.2 and r_wrist_middle < 1.2 and r_wrist_ring < 1.2:
        return "TOGGLE_LOCK"
            
    # Strict 'Super+Tab' (Thumb + Middle Pinch, requiring Index to be straight)
    if r_thumb_middle < 0.55 and r_wrist_middle < 1.4:
        if r_wrist_index > 1.4 and r_wrist_pinky > 1.2: # Enforce pinky strictness just to be safe
            return "SUPER_TAB"
        
    # Effortless Volumetric Control: If the system is unlocked and no other gestures hit,
    # simply map whatever the Thumb and Index distance is doing!
    return "VOL_CONTROL"

# ================= CONTROL =================
target_mouse_dx = 0
target_mouse_dy = 0
target_scroll_dy = 0
mouse_updated = threading.Event()

def mouse_worker():
    global target_scroll_dy, target_mouse_dx, target_mouse_dy
    while True:
        mouse_updated.wait()
        mouse_updated.clear()
        
        # Process Scroll Accumulation First! 
        sys_scroll = target_scroll_dy
        if sys_scroll != 0:
            target_scroll_dy = 0 
            subprocess.run(["ydotool", "mousemove", "-w", "-x", "0", "-y", str(sys_scroll)])
        
        # Process Mouse Movement (Relative mapping!)
        # Sending relative vectors instead of absolute positions ensures flawless Wayland GTK hover-states
        sys_dx = target_mouse_dx
        sys_dy = target_mouse_dy
        if sys_dx != 0 or sys_dy != 0:
            target_mouse_dx = 0
            target_mouse_dy = 0
            # Removed the '-a' flag!
            subprocess.run(["ydotool", "mousemove", "-x", str(sys_dx), "-y", str(sys_dy)])

threading.Thread(target=mouse_worker, daemon=True).start()

last_target_x = None
last_target_y = None

def move_mouse(x, y):
    global last_target_x, last_target_y, target_mouse_dx, target_mouse_dy
    
    if last_target_x is not None and last_target_y is not None:
        dx = x - last_target_x
        dy = y - last_target_y
        if dx != 0 or dy != 0:
            target_mouse_dx += dx
            target_mouse_dy += dy
            mouse_updated.set()
            
    last_target_x = x
    last_target_y = y

def reset_mouse_reference():
    global last_target_x, last_target_y
    last_target_x = None
    last_target_y = None

def scroll_mouse(amount):
    global target_scroll_dy
    # The 'amount' is directly the number of wheel ticks we want
    target_scroll_dy += amount
    mouse_updated.set()

def snap_to_center(screen_w, screen_h):
    """
    Recenter cursor using ONLY small relative moves — the SAME primitive
    that drives this program's normal cursor tracking and is proven to work.
    
    Why this approach: ydotool's `-a` (absolute) flag is unreliable on Wayland.
    Large single relative moves like -30000 can silently fail or overflow
    internal counters on some ydotool/compositor combinations. By breaking
    the movement into many small chunks (-200 each), we use values that the
    rest of this program already sends successfully thousands of times per
    minute during normal hand-tracking.
    
    Method:
      Phase 1: Send N chunks of (-200, -200) to clamp cursor at top-left (0, 0)
      Phase 2: Walk cursor in chunks of up to (200, 200) until it reaches (cx, cy)
    """
    global last_target_x, last_target_y, target_mouse_dx, target_mouse_dy
    cx = screen_w // 2
    cy = screen_h // 2
    CHUNK = 200
    
    print(f"R -> SNAP: starting recenter to ({cx}, {cy})")
    
    # Phase 1: push to top-left corner. Send enough chunks to overshoot screen
    # diagonal so we GUARANTEE clamping at (0, 0) from any starting position.
    push_total = max(screen_w, screen_h) + 400  # safety margin
    push_chunks = (push_total // CHUNK) + 1
    for _ in range(push_chunks):
        subprocess.run(["ydotool", "mousemove", "-x", str(-CHUNK), "-y", str(-CHUNK)])
    print(f"R -> SNAP: phase 1 done ({push_chunks} push-to-corner moves sent)")
    
    # Phase 2: walk from (0, 0) toward (cx, cy) in small steps.
    remaining_x = cx
    remaining_y = cy
    walk_count = 0
    while remaining_x > 0 or remaining_y > 0:
        step_x = min(remaining_x, CHUNK) if remaining_x > 0 else 0
        step_y = min(remaining_y, CHUNK) if remaining_y > 0 else 0
        subprocess.run(["ydotool", "mousemove", "-x", str(step_x), "-y", str(step_y)])
        remaining_x -= step_x
        remaining_y -= step_y
        walk_count += 1
    print(f"R -> SNAP: phase 2 done ({walk_count} walk-to-center moves sent)")
    
    # CRITICAL: set last_target to None so the next move_mouse() call does NOT
    # immediately drag the cursor toward the hand position (which would land it
    # at the bottom-right since hands enter from below the camera).
    last_target_x = None
    last_target_y = None
    target_mouse_dx = 0
    target_mouse_dy = 0
    
    # Reset 1-Euro filter so smoothing starts fresh from the current hand position
    filter_x.x_prev = None
    filter_x.t_prev = None
    filter_y.x_prev = None
    filter_y.t_prev = None
    print(f"R -> SNAP: complete. Cursor should be at center ({cx}, {cy})")




# ================= MAIN =================
if __name__ == "__main__":
    # Try opening external webcam (usually index 2 or 1) then fallback to 0
    cap = cv2.VideoCapture(2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(0)
        
    if not cap.isOpened():
        print("ERROR: No camera found!")
        sys.exit(1)

    cap.set(3, FRAME_WIDTH)
    cap.set(4, FRAME_HEIGHT)
    print("Advanced Gesture Control w/ AI running... Press 'q' to quit")
    print("GPU settings maxed via model_complexity=1")
    
    # State resetters for clicks
    right_click_start_time = None
    right_click_triggered = False
    middle_click_start_time = None
    middle_click_triggered = False
    scroll_accumulator = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.flip(frame, 1)

        # Removed the artificial frame limiting! MediaPipe can process 30+ FPS directly!
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res = hands.process(rgb)
        
        t = time.time()

        # BUG #1 FIX: Force cleanup when ZERO hands detected, so dragging/modifiers
        # cannot get stuck if the user yanks both hands out of the camera frame
        if not (res.multi_hand_landmarks and res.multi_handedness):
            if is_mouse_down:
                subprocess.run(["ydotool", "click", "0x80"])
                is_mouse_down = False
                print("SAFETY -> MOUSE UP (no hands detected)")
            is_dragging = False
            pinch_start_time = None
            right_click_start_time = None
            right_click_triggered = False
            middle_click_start_time = None
            middle_click_triggered = False
            last_scroll_y = None
            scroll_accumulator = 0.0
            if is_ctrl_down:
                subprocess.run(["ydotool", "key", "29:0"])
                is_ctrl_down = False
            if is_shift_down:
                subprocess.run(["ydotool", "key", "42:0"])
                is_shift_down = False
            left_hand_first_seen = 0
            is_volume_unlocked = False
            is_peace_held = False
            reset_mouse_reference()
            cv2.putText(frame, "NO HANDS", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        if res.multi_hand_landmarks and res.multi_handedness:
            right_lm = None
            left_lm = None
            
            for idx, hand_handedness in enumerate(res.multi_handedness):
                lm_list = res.multi_hand_landmarks[idx].landmark
                # Anti-Human Isolation: Ignore background hands physically far from the camera.
                # The user's palm_size will naturally be huge (close). Background users will be tiny (<0.04).
                palm_depth = dist(lm_list[0], lm_list[9])
                if palm_depth < 0.04:
                    continue
                
                label = hand_handedness.classification[0].label
                # Fix for Hand Swap issue:
                if label == "Right":
                    right_lm = res.multi_hand_landmarks[idx]
                else:
                    left_lm = res.multi_hand_landmarks[idx]

            # NEW FEATURE: Auto-recenter cursor on right hand re-entry.
            # If right hand has been gone for more than RECENTRE_DEBOUNCE seconds
            # and is back now, snap to center. Skipped during launcher mode because
            # in that mode the LEFT hand is the mouse, not the right.
            if right_lm:
                if (t - right_hand_last_seen > RECENTRE_DEBOUNCE) and not is_launcher_open:
                    snap_to_center(sw, sh)
                right_hand_last_seen = t

            # Security Hard-disable: Ensure right hand does nothing when locked.
            if is_locked:
                right_lm = None

            # Left Hand Logic
            if left_lm:
                # BUG #2 FIX: Initialize lg here so we can reuse it for the on-screen
                # label below instead of calling detect_left() a second time
                lg = "STABILIZING"
                # 0.4s Tracking Debouncer: Eradicates glitches when the hand first pops into camera
                if left_hand_first_seen == 0:
                    left_hand_first_seen = t
                    
                if t - left_hand_first_seen < 0.4:
                    # Ignore all gestures entirely for the first half second to let tracking physically stabilize!
                    pass
                else:
                    if is_launcher_open and launcher_proc:
                        if launcher_proc.poll() is not None:
                            is_launcher_open = False
                            print("L -> LAUNCHER EXITED AUTOMATICALLY. Baton returned to Right Hand.")
                            subprocess.run(["notify-send", "-t", "1000", "Baton Transfer", "Right Hand Activated"])

                    lg = detect_left(left_lm.landmark)
                    
                    if lg == "OK_SIGN":
                        if t - last_launcher_toggle > 1.5:
                            is_launcher_open = not is_launcher_open
                            if is_launcher_open:
                                print("L -> LAUNCHER OPENED! Baton passed to Left Hand.")
                                launcher_proc = subprocess.Popen(["python3", APP_LAUNCHER_PATH])
                                subprocess.run(["notify-send", "-t", "1000", "Baton Transfer", "App Launcher - Left Hand Active"])
                            else:
                                print("L -> LAUNCHER CLOSED! Baton returned to Right Hand.")
                                if launcher_proc: launcher_proc.terminate()
                                subprocess.run(["notify-send", "-t", "1000", "Baton Transfer", "Right Hand Activated"])
                            last_launcher_toggle = t
                            
                    if is_launcher_open:
                        pass # Left Hand skips other tools while acting as the OS mouse!
                    elif lg == "TOGGLE_LOCK":
                        if t - last_lock_toggle > 1.5:
                            is_locked = not is_locked
                            last_lock_toggle = t
                            print(f"L -> LOCK TOGGLED: {is_locked}")
                            subprocess.run(["notify-send", "-t", "1000", "Security Mode", "Hand Controls Locked" if is_locked else "Controls Unlocked"])
                    
                    if not is_locked:
                        if lg == "PEACE":
                            if not is_peace_held:
                                is_volume_unlocked = not is_volume_unlocked
                                print(f"L -> VOLUME UNLOCKED: {is_volume_unlocked}")
                                
                                # GLITCH SOLVER: Rollback the volume to what it was 0.5s ago BEFORE the user started forming the Peace sign!
                                if not is_volume_unlocked and len(vol_history) > 0:
                                    safe_vol = vol_history[0]
                                    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{safe_vol}%"])
                                    current_volume = safe_vol
                                    print(f"L -> GLITCH PREVENTED. Rolled volume back to {safe_vol}%")
                                vol_history.clear()
                                
                                subprocess.run(["notify-send", "-t", "500", "Audio Control", "Volume Slider UNLOCKED!" if is_volume_unlocked else "Volume LOCKED"])
                                is_peace_held = True
                        else:
                            is_peace_held = False
                                
                        if lg == "SUPER_TAB":
                            if t - last_super_tab > 1.0:
                                subprocess.run(["ydotool", "key", "125:1", "15:1", "15:0", "125:0"])
                                print("L -> SUPER TAB (Workspace Switch)")
                                last_super_tab = t
                                
                        elif lg == "VOL_CONTROL" and is_volume_unlocked:
                            # Map thumb-index physical distance dynamically to volume! 
                            vol_ratio = dist(left_lm.landmark[4], left_lm.landmark[8]) / dist(left_lm.landmark[0], left_lm.landmark[9])
                            vol_pct = max(0, min(100, int((vol_ratio - 0.4) / 1.5 * 100))) # scales ratio 0.4-1.9 to 0-100%
                            
                            # Log history to prevent transition glitches!
                            vol_history.append(vol_pct)
                            if len(vol_history) > 15:
                                vol_history.pop(0)
                            
                            if abs(vol_pct - current_volume) > 2:
                                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol_pct}%"])
                                print(f"L -> VOLUME {vol_pct}%")
                                current_volume = vol_pct
                                
                            # Draw visual reference line between active trigger fingers
                            fh, fw, _ = frame.shape
                            ix, iy = int(left_lm.landmark[8].x * fw), int(left_lm.landmark[8].y * fh)
                            tx, ty = int(left_lm.landmark[4].x * fw), int(left_lm.landmark[4].y * fh)
                            cv2.line(frame, (ix, iy), (tx, ty), (0, 255, 255), 4)
                            cv2.putText(frame, f"{vol_pct}%", (ix, iy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

                draw.draw_landmarks(frame, left_lm, mp.solutions.hands.HAND_CONNECTIONS)
                # BUG #2 FIX: Reuse lg from above instead of calling detect_left() a second time
                cv2.putText(frame, f"L: {lg}", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
            else:
                # If hand leaves frame, heavily secure by instantly resetting lock!
                left_hand_first_seen = 0
                is_peace_held = False
                if is_volume_unlocked:
                    print("L -> VOLUME LOCKED (Hand Out of Bounds)")
                    is_volume_unlocked = False

            # Right Hand Logic (or Left Hand if Baton is passed!)
            control_lm = left_lm if is_launcher_open else right_lm
            
            if control_lm:
                lm = control_lm.landmark
                g = detect_right(lm)
                
                # Edge-reachability scaling: crop bounding box so edge buttons can be clicked easily
                raw_x = lm[9].x
                raw_y = lm[9].y
                
                mapped_x = (raw_x - CAM_X_MIN) / (CAM_X_MAX - CAM_X_MIN)
                mapped_y = (raw_y - CAM_Y_MIN) / (CAM_Y_MAX - CAM_Y_MIN)
                mapped_x = max(0.0, min(mapped_x, 1.0))
                mapped_y = max(0.0, min(mapped_y, 1.0))
                
                # Map point to the middle base knuckle to stop the cursor from jumping
                hx = mapped_x * sw
                hy = mapped_y * sh
                
                if g == "PAUSE":
                    reset_mouse_reference()
                    if is_mouse_down:
                        subprocess.run(["ydotool", "click", "0x80"])
                        is_mouse_down = False
                    is_dragging = False
                    pinch_start_time = None 
                    right_click_start_time = None
                    right_click_triggered = False
                    middle_click_start_time = None
                    middle_click_triggered = False
                
                elif g == "SCROLL":
                    reset_mouse_reference()
                    if is_mouse_down:
                        subprocess.run(["ydotool", "click", "0x80"])
                        is_mouse_down = False
                    is_dragging = False
                    pinch_start_time = None
                    right_click_start_time = None
                    right_click_triggered = False
                    middle_click_start_time = None
                    middle_click_triggered = False
                    
                    current_y = (lm[8].y + lm[12].y) / 2
                    if last_scroll_y is not None:
                        dy = current_y - last_scroll_y
                        scroll_accumulator += dy
                        
                        # Requires at least 4% vertical hand movement to trigger a scroll step.
                        # This perfectly prevents the jitter/lag from automatically going up/down!
                        if scroll_accumulator > 0.04:
                            scroll_mouse(3) # scroll down
                            scroll_accumulator = 0.0
                        elif scroll_accumulator < -0.04:
                            scroll_mouse(-3) # scroll up
                            scroll_accumulator = 0.0
                            
                        # Continually update reference to follow hand precisely
                        last_scroll_y = current_y
                    else:
                        last_scroll_y = current_y
                        scroll_accumulator = 0.0
                    
                elif g == "MOVE":
                    last_scroll_y = None
                    scroll_accumulator = 0.0
                    
                    if is_mouse_down:
                        subprocess.run(["ydotool", "click", "0x80"]) # Physical mouse UP
                        is_mouse_down = False
                        print("R -> MOUSE UP")
                        
                    is_dragging = False
                    pinch_start_time = None
                    right_click_start_time = None
                    right_click_triggered = False
                    middle_click_start_time = None
                    middle_click_triggered = False
                        
                    x = int(filter_x(t, hx))
                    y = int(filter_y(t, hy))
                    move_mouse(x, y)
                    
                elif g == "DRAG":
                    last_scroll_y = None
                    scroll_accumulator = 0.0
                    right_click_start_time = None
                    right_click_triggered = False
                    middle_click_start_time = None
                    middle_click_triggered = False
                    
                    if pinch_start_time is None:
                        pinch_start_time = t
                        
                    elapsed = t - pinch_start_time
                    
                    # Fire physical mouse DOWN as soon as noise filter clears (0.05s)
                    if elapsed > 0.05 and not is_mouse_down:
                        subprocess.run(["ydotool", "click", "0x40"]) # Physical mouse DOWN
                        is_mouse_down = True
                        print("R -> MOUSE DOWN")
                        
                    if elapsed > 0.35 and not is_dragging:
                        is_dragging = True
                        print("R -> DRAG MOVEMENT ENABLED")

                    # FREEZE CURSOR during pinch wind-up! This guarantees you can click "small things" 
                    # without your hand's physical 'pinching' motion pulling the mouse off the target!
                    if is_dragging:
                        x = int(filter_x(t, hx))
                        y = int(filter_y(t, hy))
                        move_mouse(x, y)
                    else:
                        # Keep filter updated silently so it doesn't snap when released
                        filter_x(t, hx)
                        filter_y(t, hy)
                        
                elif g == "RIGHT_CLICK":
                    if is_mouse_down:
                        subprocess.run(["ydotool", "click", "0x80"])
                        is_mouse_down = False
                    is_dragging = False
                    pinch_start_time = None
                    last_scroll_y = None
                    scroll_accumulator = 0.0
                    middle_click_start_time = None
                    middle_click_triggered = False
                    
                    x = int(filter_x(t, hx))
                    y = int(filter_y(t, hy))
                    move_mouse(x, y)
                    
                    # Fire Right Click EXACTLY ONCE per gesture activation to stop menus from auto-closing!
                    if right_click_start_time is None:
                        right_click_start_time = t
                    
                    elapsed = t - right_click_start_time
                    if elapsed > 0.15 and not right_click_triggered:
                        # Use explicit explicit DOWN wait UP so the desktop registers it faithfully
                        subprocess.run(["ydotool", "click", "0x41"])
                        time.sleep(0.05)
                        subprocess.run(["ydotool", "click", "0x81"])
                        print("R -> RIGHT CLICK")
                        right_click_triggered = True

                elif g == "MIDDLE_CLICK":
                    if is_mouse_down:
                        subprocess.run(["ydotool", "click", "0x80"])
                        is_mouse_down = False
                    is_dragging = False
                    pinch_start_time = None
                    last_scroll_y = None
                    scroll_accumulator = 0.0
                    right_click_start_time = None
                    right_click_triggered = False
                    
                    x = int(filter_x(t, hx))
                    y = int(filter_y(t, hy))
                    move_mouse(x, y)
                    
                    if middle_click_start_time is None:
                        middle_click_start_time = t
                        
                    elapsed = t - middle_click_start_time
                    if elapsed > 0.15 and not middle_click_triggered:
                        subprocess.run(["ydotool", "click", "0x42"])
                        time.sleep(0.05)
                        subprocess.run(["ydotool", "click", "0x82"])
                        print("R -> MIDDLE CLICK (Scroll Button)")
                        middle_click_triggered = True

                draw.draw_landmarks(frame, right_lm, mp.solutions.hands.HAND_CONNECTIONS)
                cv2.putText(frame, f"R: {g}", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        else:
            left_hand_first_seen = 0
            is_volume_unlocked = False
            
            if is_mouse_down:
                subprocess.run(["ydotool", "click", "0x80"])
                is_mouse_down = False
            is_dragging = False
            pinch_start_time = None
            right_click_start_time = None
            right_click_triggered = False
            middle_click_start_time = None
            middle_click_triggered = False
            if is_ctrl_down:
                subprocess.run(["ydotool", "key", "29:0"])
                is_ctrl_down = False
            if is_shift_down:
                subprocess.run(["ydotool", "key", "42:0"])
                is_shift_down = False
            cv2.putText(frame, "NO HANDS", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        if is_locked:
            cv2.putText(frame, "SYSTEM LOCKED", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)

        cv2.imshow("Advanced Gesture Control", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    if is_mouse_down:
        subprocess.run(["ydotool", "click", "0x80"])
    if is_ctrl_down:
        subprocess.run(["ydotool", "key", "29:0"])
    if is_shift_down:
        subprocess.run(["ydotool", "key", "42:0"])
        
    cap.release()
    cv2.destroyAllWindows()
    print("Stopped.")
