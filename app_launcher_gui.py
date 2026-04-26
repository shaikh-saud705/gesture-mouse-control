import tkinter as tk
import subprocess
import sys

# BUG #4 FIX: Flag to stop force_top from rescheduling itself once we start closing.
# Without this, after() keeps queuing calls even as the window is being torn down.
is_closing = False

def launch_app(command):
    global is_closing
    # Launch app detached
    subprocess.Popen(command, shell=True)
    # Mark closing so the force_top loop stops scheduling new calls
    is_closing = True
    # BUG #3 FIX: Properly destroy tkinter root before exit. Using sys.exit alone
    # raises SystemExit without tearing down the window, leaving a Wayland ghost
    # frame and confusing the parent process that's polling launcher_proc.poll().
    root.after(200, _shutdown)

def _shutdown():
    try:
        root.destroy()
    except tk.TclError:
        pass
    sys.exit(0)

root = tk.Tk()
root.title("Gesture OS Launcher")
# Take up a decent chunk of the screen and put it at the very top level so the camera doesn't overlap it!
root.geometry("800x400")
root.eval('tk::PlaceWindow . center')
root.attributes('-topmost', True)
root.configure(bg="#1e1e1e") # Sleek dark mode

lbl = tk.Label(root, text="Left Hand Activated", font=("Arial", 36, "bold"), fg="#00e5ff", bg="#1e1e1e")
lbl.pack(pady=40)

f = tk.Frame(root, bg="#1e1e1e")
f.pack(expand=True)

# Stylish UI Buttons
btn_style = {"font": ("Arial", 22, "bold"), "fg": "white", "bg": "#333333",
             "activebackground": "#00e5ff", "activeforeground": "black",
             "padx": 30, "pady": 15, "bd": 0, "cursor": "hand1"}

btn1 = tk.Button(f, text="🌐 Chrome", command=lambda: launch_app("google-chrome || chromium-browser || flatpak run com.google.Chrome"), **btn_style)
btn1.pack(side=tk.LEFT, padx=15)

btn2 = tk.Button(f, text="🎵 Spotify", command=lambda: launch_app("spotify || flatpak run com.spotify.Client"), **btn_style)
btn2.pack(side=tk.LEFT, padx=15)

btn3 = tk.Button(f, text="🤖 AI Terminal", command=lambda: launch_app("gnome-terminal -- kiro-cli || x-terminal-emulator -e kiro-cli"), **btn_style)
btn3.pack(side=tk.LEFT, padx=15)

# Keep the window fully on top even if clicked off
def force_top():
    # BUG #4 FIX: Stop rescheduling once we're in the closing state. Prevents
    # TclError on a destroyed window and ends the chain of after() calls cleanly.
    if is_closing:
        return
    try:
        root.lift()
        root.attributes('-topmost', True)
    except tk.TclError:
        return  # window already gone
    root.after(1000, force_top)

force_top()

root.mainloop()
