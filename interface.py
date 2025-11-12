import tkinter as tk
from tkinter import messagebox, scrolledtext
import threading
import time
import datetime as dt
import random
import os
import pickle
import serial
import serial.tools.list_ports

#alarm sound
import pygame

pygame.mixer.init()

# --- 1. CONFIGURATION AND CONSTANTS ---
LOG_FILE = "alerts.log"
STATE_FILE = "system_state.pkl"
FONT_BOLD = ("Inter", 10, "bold")
FONT_NORMAL = ("Inter", 10)
COLOR_GREEN = "#10B981"  # Tailwind green-500 (Active/Normal)
COLOR_RED = "#EF4444"    # Tailwind red-500 (Alarm)
COLOR_BLUE = "#3B82F6"   # Tailwind blue-500 (IR Sensor)
COLOR_GRAY = "#D1D5DB"   # Tailwind gray-300 (Flicker OFF state)
COLOR_DARK = "#1F2937"   # Tailwind gray-800
COLOR_LIGHT = "#F9FAFB"  # Tailwind gray-50

class IntrusionDetectionSystem:
    # Define a mock sensor map layout for the Canvas (used as default if no state file exists)
    DEFAULT_SENSOR_MAP = {
        "IR_LivingRoom": {"x": 50, "y": 50, "type": "IR", "status": "Normal"},
        "Sound_Kitchen": {"x": 200, "y": 70, "type": "Sound", "status": "Normal"},
        "IR_Hallway": {"x": 100, "y": 200, "type": "IR", "status": "Normal"},
        "Sound_BackDoor": {"x": 350, "y": 150, "type": "Sound", "status": "Normal"},
    }

    def __init__(self, master):
        self.master = master
        master.title("🛡️ Home Intrusion Detection System")
        master.configure(bg=COLOR_LIGHT)
        
        #serial port from the arduino
        self.serial_port = None
        self.stop_serial_thread = threading.Event()
        self._init_serial_connection()


        # --- System State Variables ---
        self.is_active = False
        self.is_alarm_sounding = False
        self.schedule_start = dt.time(22, 0) # 10:00 PM
        self.schedule_stop = dt.time(7, 0)   # 7:00 AM
        self.sensor_data = self.DEFAULT_SENSOR_MAP.copy() # Initialized with defaults
        
        # Flicker State Variables
        self.flicker_id = None           # ID for the master.after loop
        self.flicker_state = False       # Toggles True/False for the ON/OFF visual state
        # Tracks the set of sensors causing the current alarm (supports multiple simultaneous triggers)
        self.triggered_sensor_names = set()

        
        # New State Variables for Drag and Edit/Add/Delete
        self._drag_data = {"item": None, "x": 0, "y": 0, "sensor_name": None}
        self._edit_entry = None
        
        # Variable for adding new sensors
        self.new_sensor_type = tk.StringVar(self.master)
        self.new_sensor_type.set("IR")

        # Threads
        self.schedule_thread = None
        self.sensor_monitor_thread = None
        self.stop_schedule_monitor = threading.Event()
        self.stop_sensor_monitor = threading.Event()

        # Load persistence and initialize
        self._load_state()
        self._init_pygame_alarm()
        self._create_widgets()
        self._load_log()
        self._start_schedule_monitor()
        self._update_ui_state()
        
        # suppression state variable "Stop Alarm" button
        self.suppression_until = None  # datetime until which alarms are ignored        
        
        # Set up cleanup on closing
        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    # --- SERIAL CONNECTION FUNCTION
    
    def _init_serial_connection(self):
        """Initialize serial connection to Arduino."""
        
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            if "Arduino" in p.description or "ttyACM" in p.device:
                try:
                    self.serial_port = serial.Serial(p.device, 9600, timeout=1)
                    print(f"Connected to Arduino on {p.device}")
                    self._start_serial_monitor()
                    return
                except Exception as e:
                    print(f"Failed to connect to {p.device}: {e}")
        print("Arduino not found. Running in simulation mode.")
    
    
    #SERIAL MONITORING THREAD
    
    def _start_serial_monitor(self):
        """Thread to continuously read from Arduino."""
    
        if not self.serial_port:
            return
        self.stop_serial_thread.clear()
        threading.Thread(target=self._serial_monitor_loop, daemon=True).start()

    # SERIAL MONITORING LOOP
    def _serial_monitor_loop(self):
        """Continuously read single characters from Arduino and handle triggers."""
        if not self.serial_port:
            return
        self.stop_serial_thread.clear()
        while not self.stop_serial_thread.is_set():
            try:
                if self.serial_port.in_waiting > 0:
                    # read a single byte (character)
                    ch = self.serial_port.read(1).decode('utf-8', errors='ignore')
                    if ch:
                        # strip any whitespace/newline
                        ch = ch.strip()
                        if ch:
                            # pass the raw character to handler
                            self._handle_serial_trigger(ch)
                # minor sleep to avoid busy loop
                time.sleep(0.02)
            except Exception as e:
                print(f"Serial read error: {e}")
                time.sleep(0.5)

        # TRIGGER HANDLER
    def _handle_serial_trigger(self, char):
        """Map Arduino serial characters to active sensors dynamically."""
        mapping = {
            'I': 'IR',
            'S': 'Sound',
            'B': 'Both'
        }

        if not char:
            return

        # keep only the first char (sometimes newline included)
        key = char[0]
        if key not in mapping:
            return

        trigger_type = mapping[key]

        # If suppression active, ignore incoming triggers
        try:
            import datetime as _dt
            if self.suppression_until is not None and _dt.datetime.now() < self.suppression_until:
                # print debug
                # print(f\"Ignored serial trigger {key} due to suppression until {self.suppression_until}\")
                return
        except Exception:
            pass

        if trigger_type == 'Both':
            # Trigger one IR and one Sound sensor if they exist
            ir_name = self.get_sensor_by_type("IR")
            sound_name = self.get_sensor_by_type("Sound")
            if ir_name:
                self.master.after(0, lambda: self.handle_intrusion("IR", ir_name))
            if sound_name:
                self.master.after(0, lambda: self.handle_intrusion("Sound", sound_name))
        else:
            sensor_name = self.get_sensor_by_type(trigger_type)
            if sensor_name:
                # Always pass both type and sensor name to handle_intrusion
                self.master.after(0, lambda t=trigger_type, s=sensor_name: self.handle_intrusion(t, s))

    # --- 1. CORE SYSTEM LOGIC & STATE ---


    def _load_state(self):
        """Loads system state (active status, schedules, and sensor data) from file."""
        try:
            with open(STATE_FILE, 'rb') as f:
                state = pickle.load(f)
                self.is_active = state.get('is_active', False)
                self.schedule_start = state.get('schedule_start', self.schedule_start)
                self.schedule_stop = state.get('schedule_stop', self.schedule_stop)
                # Load sensor data, falling back to current self.sensor_data (the default map) if key is missing
                self.sensor_data = state.get('sensor_data', self.sensor_data)
                
                print(f"State loaded: Active={self.is_active}, Start={self.schedule_start}, Stop={self.schedule_stop}, Sensors={len(self.sensor_data)}")
        except FileNotFoundError:
            print("No state file found. Using defaults.")
        except Exception as e:
            print(f"Error loading state: {e}")

    def _save_state(self):
        """Saves current system state to file, including sensor locations and names."""
        state = {
            'is_active': self.is_active,
            'schedule_start': self.schedule_start,
            'schedule_stop': self.schedule_stop,
            'sensor_data': self.sensor_data, # Sensor data is now saved
        }
        try:
            with open(STATE_FILE, 'wb') as f:
                pickle.dump(state, f)
                print("System state saved.")
        except Exception as e:
            print(f"Error saving state: {e}")

    def activate_system(self):
        """Manually activates the system (Manual Override)."""
        if not self.is_active:
            self.is_active = True
            self._start_sensor_monitor()
            self._update_ui_state()
            self._save_state()
            print("System Activated.")

    def deactivate_system(self):
        """Manually deactivates the system (Manual Override and Alarm Stop Control)."""
        if self.is_active:
            self.is_active = False
            self._stop_sensor_monitor()
            self._stop_alarm()
            self._reset_sensor_status()
            self._update_ui_state()
            self._save_state()
            print("System Deactivated.")

    def handle_intrusion(self, trigger_type, sensor_name):
        """Intrusion Trigger Handling: Activated when a simulated trigger occurs."""
        import datetime as _dt

        # If suppression window active, ignore triggers
        if self.suppression_until is not None and _dt.datetime.now() < self.suppression_until:
            # optional: print debug
            print(f"Ignored trigger due to suppression until {self.suppression_until}")
            return

        # If system inactive, ignore
        if not self.is_active:
            return

        # Add this sensor to the set of triggered sensors so multiple targets can flicker
        if not hasattr(self, "triggered_sensor_names") or self.triggered_sensor_names is None:
            self.triggered_sensor_names = set()
        self.triggered_sensor_names.add(sensor_name)

        # Only start alarm if not already sounding
        if not self.is_alarm_sounding:
            self._start_alarm()
            # log and alerts
            alert_msg = f"Intrusion detected by {sensor_name} ({trigger_type})!"
            self._log_alert(sensor_name, trigger_type, alert_msg)
            self._send_alert("Email", alert_msg)
            self._send_alert("SMS", alert_msg)
            self._update_sensor_map(sensor_name, "Triggered")
        else:
            # If alarm already sounding, still update map and log a small entry (optional)
            self._update_sensor_map(sensor_name, "Triggered")
            print(f"Alarm already sounding; added {sensor_name} to triggered set")


    def _reset_sensor_status(self):
        """Resets all sensor statuses visually and logically."""
        for name in self.sensor_data:
            self.sensor_data[name]["status"] = "Normal"
        # clear any triggered set
        try:
            self.triggered_sensor_names.clear()
        except Exception:
            self.triggered_sensor_names = set()
        self.master.after(0, self._draw_sensor_map)  # Update GUI thread


    # --- 2. ALARM AND NOTIFICATION SYSTEM (Omitted for brevity, unchanged) ---
    def _init_pygame_alarm(self):
        # Placeholder for Pygame/Sound initialization
        try:
            self.pygame_ready = False
        except Exception:
            self.pygame_ready = False
            print("Pygame/Sound initialization failed. Alarm will be text-only.")
            
        # this function is from SENSOR MAP section
    def _update_sensor_map(self, sensor_name, status):
        """
        Dynamic Highlighting: Update a single sensor's status logically and schedule
        a GUI redraw on the main Tk thread. This is safe to call from worker threads.
        """
        if sensor_name in self.sensor_data:
            self.sensor_data[sensor_name]["status"] = status
        else:
            # If name is unknown, attempt to log and ignore — do not crash GUI.
            print(f"_update_sensor_map: sensor '{sensor_name}' not found in sensor_data.")

        # Always redraw on the GUI thread to avoid Tkinter thread issues.
        try:
            self.master.after(0, self._draw_sensor_map)
        except Exception as e:
            # If master is already shutting down, just print the error
            print(f"_update_sensor_map: scheduling redraw failed: {e}")

    def _start_alarm(self):
        """Audible Alarm: Starts the alarm sound and the UI flicker."""
        if not self.is_alarm_sounding:
            self.is_alarm_sounding = True
            self._start_flicker() # Start the visual flicker loop
            pygame.mixer.music.load("alarm.mp3")
            pygame.mixer.music.play(-1)  # loop until stop
            
            if self.pygame_ready:
                 print("🔊 ALARM SOUNDING! (Pygame simulation)")
            else:
                 print("🔊 ALARM SOUNDING! (Text only)")

    def _stop_alarm(self):
        """Alarm Stop Control: Stops the alarm sound and UI flicker."""
        if self.is_alarm_sounding:
            self.is_alarm_sounding = False
            self._stop_flicker() # Stop the visual flicker loop and reset UI
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
            
            import datetime as dt
            self.suppression_until = dt.datetime.now() + dt.timedelta(seconds=5)
            
            if self.pygame_ready:
                 print("🔇 Alarm Stopped.")
            else:
                 print("🔇 Alarm Stopped. (Text only)")

    # --- 2b. FLICKER LOGIC (Mostly unchanged) ---
    
    def _start_flicker(self):
        """Initiates the periodic UI flickering for alarm label and sensor."""
        if self.flicker_id is None:
            self._flicker_ui()

    def _stop_flicker(self):
        """Stops the periodic UI flickering and resets state."""
        if self.flicker_id:
            try:
                self.master.after_cancel(self.flicker_id)
            except Exception:
                pass
            self.flicker_id = None
            self.flicker_state = False

        # Clear the set of triggered sensors (if any)
        try:
            self.triggered_sensor_names.clear()
        except Exception:
            self.triggered_sensor_names = set()

        # Ensure UI elements are reset to the standard active/inactive state
        self._reset_sensor_status()  # Resets all sensors to green/normal
        self._update_ui_state()      # Resets status label from flickering to solid (Active/Inactive)


    def _flicker_ui(self):
        """Toggles the status label and the triggered sensor(s)' color."""
        if not self.is_alarm_sounding:
            self._stop_flicker()
            return

        self.flicker_state = not self.flicker_state

        # 1. Flicker Status Label
        alarm_text = "SYSTEM ALARM: INTRUSION!"
        if self.flicker_state:
            self.status_label.config(text=alarm_text, bg=COLOR_RED, fg="white")
        else:
            self.status_label.config(text=alarm_text, bg=COLOR_DARK, fg=COLOR_RED)

        # 2. Flicker Sensor Map - redraw to apply the new flicker state
        # _draw_sensor_map now checks membership in self.triggered_sensor_names
        self._draw_sensor_map()

        # continue the loop
        self.flicker_id = self.master.after(300, self._flicker_ui)

    
    def _send_alert(self, medium, message):
        """Email/SMS Alert: Sends a notification (Placeholder)."""
        if medium == "Email":
             print(f"📧 Sending Email Alert (via Gmail SMTP placeholder): {message}")
        elif medium == "SMS":
             print(f"📱 Sending SMS Alert (via Twilio placeholder): {message}")

    def _log_alert(self, sensor, type, message):
        """Alert Logging: Writes the alert to a log file and updates the GUI."""
        timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] - {sensor} | {type} | {message}\n"
        
        try:
            with open(LOG_FILE, 'a') as f:
                f.write(log_entry)
            self.master.after(0, lambda: self.log_text.insert(tk.END, log_entry))
            self.master.after(0, lambda: self.log_text.see(tk.END))
            print(f"Logged: {log_entry.strip()}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to write to log file: {e}")

    def _load_log(self):
        """Loads and displays existing log entries."""
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                self.log_text.insert(tk.END, f.read())
            self.log_text.see(tk.END)

    # --- 3. SENSOR MAP (Tkinter Canvas) - MODIFIED FOR DRAG/EDIT/ADD/DELETE ---

    def _draw_sensor_map(self):
        """Graphical Map Display: Draws the sensor map on the canvas, applying flicker if active."""
        self.sensor_canvas.delete("all")
        
        # Draw placeholder 'floor plan'
        self.sensor_canvas.create_rectangle(10, 10, 390, 240, outline=COLOR_DARK, width=2, tags="floorplan")
        self.sensor_canvas.create_text(200, 20, text="Floor Plan (Drag/Double-Click to Edit)", fill=COLOR_DARK, font=FONT_BOLD, tags="floorplan_text")

        is_flickering_on = self.is_alarm_sounding and self.flicker_state # True when Red should be displayed
        
        for name, data in self.sensor_data.items():
            x, y = data["x"], data["y"]
            sensor_type = data["type"]
            status = data["status"]
            
            fill_color = COLOR_GREEN
            
            if status == "Triggered":
                # If this sensor is one of the active triggered sensors, apply flicker
                if self.is_alarm_sounding and name in getattr(self, "triggered_sensor_names", set()):
                    fill_color = COLOR_RED if is_flickering_on else COLOR_GRAY
                else:
                    # triggered but not currently in the flicker set — show steady red
                    fill_color = COLOR_RED


            outline_color = COLOR_BLUE if sensor_type == "IR" else COLOR_DARK

            # Draw sensor icon (circle)
            radius = 10
            # Use name as a group tag for moving both icon and label
            self.sensor_canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=fill_color, outline=outline_color, width=2, 
                tags=(name, name + "_icon") 
            )
            
            # Sensor Labels
            label = f"{name}\n({status})"
            self.sensor_canvas.create_text(
                x, y + radius + 10, text=label, font=("Inter", 8), fill=COLOR_DARK, 
                tags=(name, name + "_label")
            )

    # --- Sensor Map Interaction Logic (Drag/Edit/Add/Delete) ---
    # --- Helper: robustly find sensor at x,y ---
    def _find_sensor_at(self, x, y):
        """
        Return the sensor-name tag at canvas coordinates (x,y), or None.
        Uses find_overlapping to avoid hitting unrelated items like windows.
        """
        # small hitbox around the click point
        items = self.sensor_canvas.find_overlapping(x - 2, y - 2, x + 2, y + 2)
        for item in items:
            tags = self.sensor_canvas.gettags(item)
            sensor_name = next((tag for tag in tags if tag in self.sensor_data), None)
            if sensor_name:
                return sensor_name
        return None


    def _start_drag(self, event):
        """Prepares for dragging by identifying the sensor item."""
        if self._edit_entry:  # If in editing mode, save first
            try:
                self._save_new_name(None)
            except Exception:
                pass

        # use robust hit test
        sensor_name = self._find_sensor_at(event.x, event.y)
        if sensor_name:
            # find a visible item id for this sensor (icon is tagged name + "_icon")
            items = self.sensor_canvas.find_withtag(sensor_name + "_icon")
            item = items[0] if items else self.sensor_canvas.find_withtag(sensor_name)[0]
            self._drag_data["item"] = item
            self._drag_data["x"] = event.x
            self._drag_data["y"] = event.y
            self._drag_data["sensor_name"] = sensor_name
            self.sensor_canvas.config(cursor="fleur")

    def _do_drag(self, event):
        """Moves the sensor item(s) on the canvas."""
        sensor_name = self._drag_data["sensor_name"]
        if sensor_name:
            # Calculate how far to move
            delta_x = event.x - self._drag_data["x"]
            delta_y = event.y - self._drag_data["y"]
            
            # Move all items associated with this sensor name (icon and label)
            self.sensor_canvas.move(sensor_name, delta_x, delta_y)
            
            # Update position data for the next motion event
            self._drag_data["x"] = event.x
            self._drag_data["y"] = event.y

    def _stop_drag(self, event):
        """Updates the stored sensor coordinates in the data model and saves state."""
        sensor_name = self._drag_data["sensor_name"]
        if sensor_name:
            # Get the new coordinates of the sensor circle's center
            coords = self.sensor_canvas.coords(sensor_name + "_icon") 
            if coords:
                # Coords returns [x1, y1, x2, y2] for the oval. Center is average.
                new_x = (coords[0] + coords[2]) / 2
                new_y = (coords[1] + coords[3]) / 2
                
                # Update the logical data store
                self.sensor_data[sensor_name]["x"] = int(new_x)
                self.sensor_data[sensor_name]["y"] = int(new_y)
                
                self._save_state()
                self._draw_sensor_map() # Redraw for cleanup and label alignment
                
        # Reset drag state
        self._drag_data = {"item": None, "x": 0, "y": 0, "sensor_name": None}
        self.sensor_canvas.config(cursor="")

    def _start_edit(self, event):
        """Starts the editing process for the sensor name on double-click."""
        # Save any ongoing edit first
        if self._edit_entry:
            try:
                self._save_new_name(None)
            except Exception:
                pass

        sensor_name = self._find_sensor_at(event.x, event.y)
        if not sensor_name:
            return

        data = self.sensor_data[sensor_name]
        x, y = data["x"], data["y"]

        # Create a temporary entry widget for editing
        self._edit_entry = tk.Entry(self.sensor_canvas, font=("Inter", 8), width=15,
                                   bg=COLOR_LIGHT, fg=COLOR_DARK, bd=1, relief="solid")
        self._edit_entry.insert(0, sensor_name)
        # Bind return and focusout to save
        self._edit_entry.bind("<Return>", self._save_new_name)
        self._edit_entry.bind("<FocusOut>", self._save_new_name)

        # Place the entry widget near the sensor icon
        # remove any previous edit window first just in case
        try:
            self.sensor_canvas.delete("edit_window")
        except Exception:
            pass

        self.sensor_canvas.create_window(
            x, y + 25, window=self._edit_entry, tags="edit_window"
        )
        self._edit_entry.focus_set()
        self._edit_entry.old_name = sensor_name  # Store old name for replacement



    def _save_new_name(self, event):
        """
        Saves the new sensor name, updates data, and redraws.
        Robust to multiple rapid edits and ensures widget cleanup.
        """
        if not self._edit_entry:
            return

        try:
            new_name = self._edit_entry.get().strip()
            old_name = getattr(self._edit_entry, "old_name", None)

            # Remove the entry window from the canvas immediately
            try:
                self.sensor_canvas.delete("edit_window")
            except Exception:
                pass

            # Keep a local ref and clear class var (prevents re-entrancy)
            edit_ref = self._edit_entry
            self._edit_entry = None

            # Process renaming
            if old_name is None:
                # nothing to do
                edit_ref.destroy()
                return

            if new_name and new_name != old_name:
                if new_name not in self.sensor_data:
                    # move data to new key
                    self.sensor_data[new_name] = self.sensor_data.pop(old_name)

                    # optional: update any runtime references
                    self._update_simulation_logic_after_rename(old_name, new_name)

                    # persist and redraw (use after to ensure UI thread)
                    self._save_state()
                    self.master.after(0, self._draw_sensor_map)
                    print(f"Sensor renamed from '{old_name}' to '{new_name}'")
                else:
                    messagebox.showerror("Error", f"Sensor name '{new_name}' already exists.")
                    self.master.after(0, self._draw_sensor_map)
            else:
                # nothing changed or empty name -> redraw original
                self.master.after(0, self._draw_sensor_map)

        finally:
            # ensure widget is destroyed if it still exists
            try:
                edit_ref.destroy()
            except Exception:
                pass
            
            
    def _update_simulation_logic_after_rename(self, old_name, new_name):
        """Updates the hardcoded simulation logic (part 6) to recognize the new sensor name."""
        # Note: This is a placeholder for how a real system might adapt to configuration changes.
        # For this Tkinter simulation, we'll ensure if the old name was a trigger target, the new name is used.
        # However, since the monitor loop uses hardcoded keys, we'll only print a warning.
        
        # A proper fix would require dynamically mapping sensor types to names, but for now, 
        # we stick to the old names for the simulation trigger and let the user know.
        if "IR_LivingRoom" in [old_name, new_name]:
            print("WARNING: Simulated 'I' trigger remains linked to 'IR_LivingRoom' for simplicity in _monitor_sensors_loop.")
        if "Sound_Kitchen" in [old_name, new_name]:
            print("WARNING: Simulated 'S' trigger remains linked to 'Sound_Kitchen' for simplicity in _monitor_sensors_loop.")
        if "Sound_BackDoor" in [old_name, new_name]:
            print("WARNING: Simulated 'B' trigger remains linked to 'Sound_BackDoor' for simplicity in _monitor_sensors_loop.")
        if "IR_Hallway" in [old_name, new_name]:
            print("WARNING: Manual trigger remains linked to 'IR_Hallway' for simplicity in simulate_intrusion_cb.")
            
    def _generate_unique_sensor_name(self, base_type):
        """Generates a unique name (e.g., IR_2) for a new sensor."""
        i = 1
        # Use only 'IR' or 'Sound' as prefix for uniqueness check
        prefix = base_type
        if prefix not in ["IR", "Sound"]:
            prefix = "Sensor" # Fallback for unknown types
            
        while True:
            name = f"{prefix}_{i}"
            if name not in self.sensor_data:
                return name
            i += 1
            if i > 99:
                raise Exception("Too many sensors!")
                
    def _add_sensor_cb(self):
        """Adds a new sensor to the map at a random default position."""
        sensor_type = self.new_sensor_type.get()
        try:
            new_name = self._generate_unique_sensor_name(sensor_type)
            
            # Default placement within the floorplan bounds (10, 10, 390, 240)
            x_pos = random.randint(50, 350)
            y_pos = random.randint(50, 200)

            self.sensor_data[new_name] = {
                "x": x_pos,
                "y": y_pos,
                "type": sensor_type,
                "status": "Normal",
            }
            
            self._save_state()
            self._draw_sensor_map()
            print(f"Added new sensor: {new_name} ({sensor_type})")
            
        except Exception as e:
            messagebox.showerror("Error", f"Could not add sensor: {e}")

    def _delete_sensor_cb(self, event):
        """Deletes a sensor on right-click, if one is clicked."""
        sensor_name = self._find_sensor_at(event.x, event.y)
        if not sensor_name:
            return

        if messagebox.askyesno("Confirm Deletion", f"Are you sure you want to delete the sensor: '{sensor_name}'?"):
            # remove
            del self.sensor_data[sensor_name]

            # handle alarm state if needed
            if sensor_name in getattr(self, "triggered_sensor_names", set()):
                self.triggered_sensor_names.discard(sensor_name)
                # if no more triggered sensors remain while alarm sounding, stop alarm
                if self.is_alarm_sounding and not self.triggered_sensor_names:
                    self._stop_alarm()


            self._save_state()
            self._draw_sensor_map()
            print(f"Deleted sensor: {sensor_name}")
        else:
            print(f"Deletion cancelled for sensor: {sensor_name}")
                    
    # --- 4. SCHEDULING AND AUTOMATION (Unchanged) ---
    def _start_schedule_monitor(self):
        """Starts the Threaded Background Task for schedule monitoring."""
        if not self.schedule_thread or not self.schedule_thread.is_alive():
            self.stop_schedule_monitor.clear()
            self.schedule_thread = threading.Thread(target=self._check_schedule, daemon=True)
            self.schedule_thread.start()
            print("Schedule monitor started.")
            self._update_next_schedule_display()

    def _check_schedule(self):
        """Automatic Activation/Deactivation based on configured times."""
        while not self.stop_schedule_monitor.wait(5): # Check every 5 seconds
            now = dt.datetime.now().time()
            
            start = self.schedule_start
            stop = self.schedule_stop

            should_be_active = False

            if start < stop:
                # Simple schedule (e.g., 9am to 5pm)
                if start <= now < stop:
                    should_be_active = True
            else:
                # Overnight schedule (e.g., 10pm to 7am)
                if now >= start or now < stop:
                    should_be_active = True

            # Apply automatic action
            if should_be_active and not self.is_active:
                self.master.after(0, self.activate_system)
                print("Schedule: Auto-Activating System.")
            elif not should_be_active and self.is_active:
                self.master.after(0, self.deactivate_system)
                print("Schedule: Auto-Deactivating System.")
            
            self.master.after(0, self._update_next_schedule_display) # Keep display updated

    def _update_next_schedule_display(self):
        """Next Schedule Display: Calculates and displays the next activation/deactivation time."""
        now = dt.datetime.now()
        start = self.schedule_start
        stop = self.schedule_stop
        
        # Logic to find the next event
        if self.is_active:
            # Next event is Deactivation (Stop Time)
            next_event_time = dt.datetime.combine(now.date(), stop)
            if next_event_time < now:
                next_event_time += dt.timedelta(days=1)
            event_type = "Deactivate"
        else:
            # Next event is Activation (Start Time)
            next_event_time = dt.datetime.combine(now.date(), start)
            if next_event_time < now:
                next_event_time += dt.timedelta(days=1)
            event_type = "Activate"
            
        next_event_str = next_event_time.strftime("%a %H:%M")
        self.next_schedule_label.config(text=f"Next Event: {event_type} at {next_event_str}")

    def save_schedule(self):
        """GUI handler for saving schedule times."""
        try:
            start_str = self.start_time_entry.get()
            stop_str = self.stop_time_entry.get()
            
            # Parse H:M format
            start_h, start_m = map(int, start_str.split(':'))
            stop_h, stop_m = map(int, stop_str.split(':'))
            
            new_start = dt.time(start_h, start_m)
            new_stop = dt.time(stop_h, stop_m)
            
            self.schedule_start = new_start
            self.schedule_stop = new_stop
            
            self._save_state()
            self._update_schedule_display()
            self._update_next_schedule_display()
            messagebox.showinfo("Success", "Schedule updated successfully!")
            
        except ValueError:
            messagebox.showerror("Error", "Invalid time format. Use HH:MM (e.g., 22:00).")
        except Exception as e:
            messagebox.showerror("Error", f"An error occurred: {e}")

    # --- 5. GUI INTERFACE (Tkinter) (Mostly unchanged) ---

    def _create_widgets(self):
        """Creates and organizes the main GUI structure."""
        main_frame = tk.Frame(self.master, padx=15, pady=15, bg=COLOR_LIGHT)
        main_frame.pack(fill="both", expand=True)

        # Main Dashboard (Row 0)
        dashboard_frame = self._create_dashboard_frame(main_frame)
        dashboard_frame.grid(row=0, column=0, columnspan=2, pady=(0, 15), sticky="ew")

        # Left Column (Row 1, Col 0)
        left_column_frame = tk.Frame(main_frame, bg=COLOR_LIGHT)
        left_column_frame.grid(row=1, column=0, padx=(0, 15), sticky="nsew")
        self.master.grid_columnconfigure(0, weight=1)

        # Sensor Map (Left Top)
        sensor_map_frame = self._create_sensor_map_frame(left_column_frame)
        sensor_map_frame.pack(fill="both", pady=(0, 15), expand=True)
        
        # Schedule Settings (Left Bottom)
        schedule_frame = self._create_schedule_frame(left_column_frame)
        schedule_frame.pack(fill="x")

        # Right Column (Row 1, Col 1)
        right_column_frame = tk.Frame(main_frame, bg=COLOR_LIGHT)
        right_column_frame.grid(row=1, column=1, sticky="nsew")
        self.master.grid_columnconfigure(1, weight=1)

        # Log View Section (Right)
        log_frame = self._create_log_frame(right_column_frame)
        log_frame.pack(fill="both", expand=True)

        # Finalizing grid weights
        self.master.grid_rowconfigure(1, weight=1)

    def _create_dashboard_frame(self, parent):
        """Creates the Main Dashboard and control buttons."""
        frame = tk.LabelFrame(parent, text="Main Dashboard & Controls", font=FONT_BOLD, bg="white", padx=15, pady=10, borderwidth=1, relief="flat")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)
        frame.columnconfigure(3, weight=1)

        # Real-time Status Label
        self.status_label = tk.Label(frame, text="System Status", font=("Inter", 14, "bold"), fg="white", bg=COLOR_GRAY, pady=8)
        self.status_label.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 10))

        # Buttons: Activate / Deactivate / Simulate Intrusion
        tk.Button(frame, text="Activate (Manual Override)", command=self.activate_system, bg=COLOR_GREEN, fg="white", font=FONT_BOLD).grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        tk.Button(frame, text="Deactivate (Manual Override)", command=self.deactivate_system, bg=COLOR_RED, fg="white", font=FONT_BOLD).grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        #tk.Button(frame, text="Simulate Intrusion", command=self.simulate_intrusion_cb, bg=COLOR_BLUE, fg="white", font=FONT_BOLD).grid(row=1, column=2, padx=5, pady=5, sticky="ew")
        tk.Button(frame, text="Stop Alarm", command=self._stop_alarm, bg=COLOR_DARK, fg="white", font=FONT_BOLD).grid(row=1, column=2, padx=5, pady=5, sticky="ew")

        return frame

    def _create_sensor_map_frame(self, parent):
        """Creates the Sensor Map Canvas, sets up drag/edit bindings, and adds Add/Delete controls."""
        frame = tk.LabelFrame(parent, text="Sensor Location Map (Drag to Move / Double-Click to Rename)", font=FONT_BOLD, bg="white", padx=5, pady=5, borderwidth=1, relief="flat")
        
        # Sensor Map Canvas
        self.sensor_canvas = tk.Canvas(frame, width=400, height=250, bg=COLOR_LIGHT, highlightthickness=0)
        self.sensor_canvas.pack(fill="both", expand=True)
        self._draw_sensor_map()
        
        # Bindings for drag and drop
        self.sensor_canvas.bind("<Button-1>", self._start_drag)
        self.sensor_canvas.bind("<B1-Motion>", self._do_drag)
        self.sensor_canvas.bind("<ButtonRelease-1>", self._stop_drag)
        # Binding for renaming (Double-Click)
        self.sensor_canvas.bind("<Double-1>", self._start_edit)
        # Binding for deleting (Right-Click)
        self.sensor_canvas.bind("<Button-3>", self._delete_sensor_cb)
        
        # --- Sensor Management Controls ---
        control_frame = tk.Frame(frame, bg="white")
        control_frame.pack(fill="x", pady=(5, 0))
        
        # 1. Sensor Type Selection
        tk.Label(control_frame, text="New Sensor Type:", font=FONT_NORMAL, bg="white").pack(side="left", padx=(10, 5))
        
        type_options = ["IR", "Sound"]
        type_menu = tk.OptionMenu(control_frame, self.new_sensor_type, *type_options)
        type_menu.config(font=FONT_NORMAL, bg=COLOR_LIGHT, fg=COLOR_DARK, bd=1, relief="solid")
        type_menu["menu"].config(font=FONT_NORMAL, bg="white", fg=COLOR_DARK)
        type_menu.pack(side="left", padx=5)

        # 2. Add Button
        tk.Button(control_frame, text="Add Sensor", command=self._add_sensor_cb, bg=COLOR_BLUE, fg="white", font=FONT_BOLD).pack(side="left", padx=10, pady=5)
        
        tk.Label(control_frame, text="Right-Click on map item to Delete", font=("Inter", 8, "italic"), bg="white", fg=COLOR_DARK).pack(side="right", padx=10)

        return frame

    def _create_schedule_frame(self, parent):
        """Creates the Schedule Settings Section."""
        frame = tk.LabelFrame(parent, text="4. Scheduling and Automation", font=FONT_BOLD, bg="white", padx=15, pady=10, borderwidth=1, relief="flat")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=1)

        # Start Time
        tk.Label(frame, text="Activation Time (HH:MM):", font=FONT_NORMAL, bg="white").grid(row=0, column=0, sticky="w", pady=5, padx=5)
        self.start_time_entry = tk.Entry(frame, width=8, font=FONT_NORMAL, borderwidth=1, relief="solid")
        self.start_time_entry.grid(row=0, column=1, sticky="w", pady=5)
        
        # Stop Time
        tk.Label(frame, text="Deactivation Time (HH:MM):", font=FONT_NORMAL, bg="white").grid(row=1, column=0, sticky="w", pady=5, padx=5)
        self.stop_time_entry = tk.Entry(frame, width=8, font=FONT_NORMAL, borderwidth=1, relief="solid")
        self.stop_time_entry.grid(row=1, column=1, sticky="w", pady=5)

        # Save Button
        tk.Button(frame, text="Set Schedule", command=self.save_schedule, bg=COLOR_BLUE, fg="white", font=FONT_BOLD).grid(row=0, column=2, rowspan=2, sticky="ns", padx=10)
        
        # Next Schedule Display
        self.next_schedule_label = tk.Label(frame, text="Next Event: Calculating...", font=("Inter", 10), bg="white", fg=COLOR_DARK)
        self.next_schedule_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=5)

        self._update_schedule_display() # Populate initial values
        return frame

    def _update_schedule_display(self):
        """Populates schedule entries with current values."""
        self.start_time_entry.delete(0, tk.END)
        self.stop_time_entry.delete(0, tk.END)
        self.start_time_entry.insert(0, self.schedule_start.strftime("%H:%M"))
        self.stop_time_entry.insert(0, self.schedule_stop.strftime("%H:%M"))

    def _create_log_frame(self, parent):
        """Creates the Log View Section."""
        frame = tk.LabelFrame(parent, text="Alert Log View (Alert Logging)", font=FONT_BOLD, bg="white", padx=10, pady=5, borderwidth=1, relief="flat")
        frame.pack(fill="both", expand=True)

        # Log View Section (Scrolled Text)
        self.log_text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, width=50, height=20, font=("Courier", 8), bg=COLOR_LIGHT, fg=COLOR_DARK, borderwidth=1, relief="solid")
        self.log_text.pack(fill="both", expand=True)
        return frame

    def _update_ui_state(self):
        """Real-time Status Label: Updates UI elements based on system state (non-alarm state)."""
        if self.is_alarm_sounding:
            # If alarm is sounding, let _flicker_ui handle the label state
            return

        if self.is_active:
            text = "SYSTEM ACTIVE"
            color = COLOR_GREEN
        else:
            text = "SYSTEM INACTIVE"
            color = COLOR_RED
            
        self.status_label.config(text=text, bg=color, fg="white")

    # --- 6. SYSTEM LOGIC & SAFETY ---
    
    def simulate_intrusion_cb(self):
        """GUI handler to manually trigger an intrusion alarm."""
        if self.is_active:
            # Use the name of the first sensor in the list for the manual trigger
            target_sensor_name = next(iter(self.sensor_data.keys()), "IR_Hallway")
            
            self.handle_intrusion("Manual Trigger", target_sensor_name)
        else:
            messagebox.showwarning("Warning", "System must be ACTIVE to simulate an intrusion.")
            
    # def _start_sensor_monitor(self):
    #     """Starts a background thread to monitor real IR sensors via Arduino."""
    #     if self.sensor_monitor_thread and self.sensor_monitor_thread.is_alive():
    #         return

        self.stop_sensor_monitor.clear()
        self.sensor_monitor_thread = threading.Thread(target=self._monitor_ir_serial, daemon=True)
        self.sensor_monitor_thread.start()
        print("IR Sensor monitor thread started.")

    def _monitor_ir_serial(self):
        """Continuously reads Arduino IR data from serial."""
        try:
            ser = self.serial_port
            # ser = serial.Serial('COM6', 9600, timeout=1)  # Change COM6 to your correct port
            time.sleep(2)  # Allow Arduino to initialize
            print("Connected to Arduino IR receiver.")

            while not self.stop_sensor_monitor.is_set():
                if ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8').strip()
                    if "IR_TRIGGER" in line:
                        print("IR signal detected from Arduino.")
                        # Trigger alarm via system handler
                        self.master.after(0, lambda: self.handle_intrusion("IR", "IR_LivingRoom"))
                time.sleep(0.1)
            ser.close()

        except serial.SerialException as e:
            print(f"Serial Error: {e}")
        except Exception as e:
            print(f"IR monitor error: {e}")


    # def _stop_sensor_monitor(self):
    #     """Stops the sensor monitoring thread."""
    #     if self.sensor_monitor_thread and self.sensor_monitor_thread.is_alive():
    #         self.stop_sensor_monitor.set()
    #         self.sensor_monitor_thread.join(timeout=0.1)
    #         print("Sensor monitor stopped.")

    def _monitor_sensors_loop(self):
        """Simulates receiving data ('S', 'I', 'B') from the AVR via UART."""
        # NOTE: This simulation logic uses hardcoded sensor names for simplicity.
        
        while not self.stop_sensor_monitor.is_set():
            if self.is_active:
                # 1. Simulate Normal State (most of the time)
                if random.random() < 0.999: 
                    if not self.is_alarm_sounding:
                        self.master.after(0, self._reset_sensor_status)
                    time.sleep(0.5)
                    continue
                
                # 2. Simulate Intrusion (0.1% chance per cycle)
                # Mimic the AVR output: 'S' (Sound), 'I' (IR), 'B' (Both)
                intrusion_type = random.choice(['S', 'I', 'B'])
                
                # Hardcoded logic targets existing default sensors. 
                # If a default sensor is deleted, this part might need manual adjustment in a real system.
                sensor_name_map = {
                    'S': "Sound_Kitchen",
                    'I': "IR_LivingRoom",
                    'B': "Sound_BackDoor"
                }
                sensor_name = sensor_name_map.get(intrusion_type, None)
                
                if sensor_name:
                    # Find the current, possibly renamed, key corresponding to the default sensor
                    # This attempts to gracefully handle renames of the original sensors by checking types.
                    
                    target_type = "Sound" if intrusion_type in ['S', 'B'] else "IR"
                    
                    # Find a sensor of the target type that is *not* already triggered
                    available_sensors = [name for name, data in self.sensor_data.items() 
                                         if data["type"] == target_type and data["status"] != "Triggered"]
                    
                    if available_sensors:
                        # Deterministically pick the FIRST available sensor (preserves insertion order)
                        trigger_target = available_sensors[0]
                    else:
                        # Fallback to the first sensor in the dict (if any)
                        trigger_target = next(iter(self.sensor_data.keys()), None)


                    if trigger_target:
                        self.master.after(0, lambda target=trigger_target: self.handle_intrusion(intrusion_type, target))
                        time.sleep(5) # Wait before checking again after an intrusion
                    else:
                        time.sleep(0.5)

                else:
                    time.sleep(0.5)
            else:
                time.sleep(0.5)
                
    def get_sensor_by_type(self, sensor_type):
        """Return a sensor name of the given type.
        Prefer sensors not already 'Triggered'. Fallback to any sensor of that type.
        """
        # 1. Try to find non-triggered sensor of this type
        for name, data in self.sensor_data.items():
            if data.get("type") == sensor_type and data.get("status") != "Triggered":
                return name
        # 2. Fallback: return any sensor of this type
        for name, data in self.sensor_data.items():
            if data.get("type") == sensor_type:
                return name
        # 3. No sensor found
        return None


    # --- 6. ARDUINO SERIAL INTEGRATION ---
    def _start_sensor_monitor(self):
        """
        Starts the Arduino serial-monitor thread.
        Avoids multiple monitors; safe to call repeatedly.
        """
        # Check if the thread exists and is alive before starting a new one
        if (
            hasattr(self, "sensor_monitor_thread")
            and self.sensor_monitor_thread is not None
            and getattr(self.sensor_monitor_thread, "is_alive", lambda: False)()
        ):
            print("[INFO] Sensor monitor already running.")
            return

        # Ensure stop flag exists
        if not hasattr(self, "stop_sensor_monitor"):
            self.stop_sensor_monitor = threading.Event()
        else:
            self.stop_sensor_monitor.clear()

        # Start new monitoring thread
        self.sensor_monitor_thread = threading.Thread(
            target=self._monitor_arduino_loop, daemon=True
        )
        self.sensor_monitor_thread.start()
        print("[INFO] Sensor monitor thread started.")


    def _stop_sensor_monitor(self):
        """
        Stops the Arduino serial-monitor thread gracefully.
        """
        if not hasattr(self, "stop_sensor_monitor"):
            return

        self.stop_sensor_monitor.set()
        print("[INFO] Stopping sensor monitor...")

        try:
            if (
                hasattr(self, "sensor_monitor_thread")
                and self.sensor_monitor_thread is not None
                and getattr(self.sensor_monitor_thread, "is_alive", lambda: False)()
            ):
                self.sensor_monitor_thread.join(timeout=1.0)
                print("[INFO] Sensor monitor stopped cleanly.")
            else:
                print("[INFO] No active sensor monitor to stop.")
        except Exception as e:
            print(f"[WARN] Error stopping monitor: {e}")


    def _monitor_arduino_loop(self):
        """
        Robust Arduino serial monitor:
        - Uses existing self.serial_port if available (set by _init_serial_connection),
        otherwise attempts to open a port if a default (COM6) is desired.
        - Sends the raw char into the central _handle_serial_trigger() which already
        picks a real sensor name using get_sensor_by_type(...).
        This avoids hardcoded sensor names and keeps behavior consistent with simulation.
        """
        try:
            ser = self.serial_port
            # If no serial_port was set earlier, attempt to open COM6 as a fallback.
            if ser is None:
                try:
                    ser = serial.Serial('COM6', 9600, timeout=0.1)
                    print("Warning: opened fallback serial on COM6 inside _monitor_arduino_loop.")
                except Exception as e:
                    print(f"_monitor_arduino_loop: no self.serial_port and COM6 open failed: {e}")
                    return

            # slight startup delay to let Arduino reset (if needed)
            time.sleep(1.0)
            print("Arduino monitor loop running (monitoring for 'I','S','B').")

            while not self.stop_sensor_monitor.is_set():
                try:
                    if ser.in_waiting:
                        ch = ser.read(1).decode('utf-8', errors='ignore')
                        if ch:
                            # strip whitespace/newline and pass the first char
                            key = ch.strip()
                            if key:
                                # Use the safe central handler which maps to an actual sensor.
                                # Schedule it on the Tk thread for safety (handler itself also uses after).
                                self.master.after(0, lambda k=key: self._handle_serial_trigger(k))
                    # small sleep to avoid busy-looping
                    time.sleep(0.03)
                except Exception as e:
                    print(f"_monitor_arduino_loop: read error: {e}")
                    time.sleep(0.2)

            # if we opened a fallback serial inside this function, close it
            if ser is not None and ser is not self.serial_port:
                try:
                    ser.close()
                except Exception:
                    pass

        except serial.SerialException as e:
            print(f"_monitor_arduino_loop: Serial Error: {e}")
        except Exception as e:
            print(f"_monitor_arduino_loop: Unexpected error: {e}")


    def on_closing(self):
        """Handles graceful shutdown."""
        self.stop_schedule_monitor.set()
        self.stop_sensor_monitor.set()
        self._save_state()
        self.master.destroy()
        
        # Stop serial thread
        self.stop_serial_thread.set()
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()


if __name__ == "__main__":
    root = tk.Tk()
    app = IntrusionDetectionSystem(root)
    root.mainloop()
