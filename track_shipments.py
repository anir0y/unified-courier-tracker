import urllib.request
import urllib.error
import json
import sys
import argparse
import os
import curses
import time
from html.parser import HTMLParser

# --- TRACKER CLASSES ---

class Tracker:
    def get_details(self, tracking_number):
        raise NotImplementedError

class BlueDartParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.output = {
            "status": None,
            "delivery_details": {},
            "scans": []
        }
        self._current_tag = None
        self._in_status_label = False
        self._capture_next_p_as_status = False
        self._in_shipment_tab = False
        self._in_scan_tab = False
        self._current_row = []
        self._in_td = False
        self._in_th = False

    def handle_starttag(self, tag, attrs):
        self._current_tag = tag
        attrs_dict = dict(attrs)
        if tag == "label": self._in_status_label = True
        if tag == "div":
            if attrs_dict.get("id", "").startswith("SHIP"):
                self._in_shipment_tab = True; self._in_scan_tab = False
            elif attrs_dict.get("id", "").startswith("SCAN"):
                self._in_scan_tab = True; self._in_shipment_tab = False
        if tag == "tr": self._current_row = []
        if tag in ["td", "th"]: self._in_td = True; self._in_th = True

    def handle_endtag(self, tag):
        if tag == "label": self._in_status_label = False
        if tag in ["td", "th"]: self._in_td = False; self._in_th = False
        if tag == "tr":
            if self._in_shipment_tab and len(self._current_row) >= 2:
                raw_key = self._current_row[0]
                key = " ".join(raw_key.split()).strip(" :")
                if len(self._current_row) > 1:
                    value = self._current_row[1]
                    self.output["delivery_details"][key] = value
                    if "Status" in key and not self.output["status"]:
                        self.output["status"] = value.split("\n")[0].strip()
            elif self._in_scan_tab and len(self._current_row) >= 3:
                location = self._current_row[0]
                if "Location" not in location and "24 Hr Format" not in location and "Feedback By" not in location:
                    scan = {
                        "location": location,
                        "details": self._current_row[1] if len(self._current_row) > 1 else "",
                        "date": self._current_row[2] if len(self._current_row) > 2 else "",
                        "time": self._current_row[3] if len(self._current_row) > 3 else ""
                    }
                    self.output["scans"].append(scan)

    def handle_data(self, data):
        clean_data = data.strip()
        if not clean_data: return
        if self._in_status_label and "Status" in clean_data: self._capture_next_p_as_status = True
        if self._capture_next_p_as_status and self._current_tag == "p":
            self.output["status"] = clean_data; self._capture_next_p_as_status = False
        if (self._in_td or self._in_th): self._current_row.append(clean_data)

class BlueDartTracker(Tracker):
    def get_details(self, tracking_number):
        url = f"https://www.bluedart.com/trackdartresultthirdparty?trackFor=0&trackNo={tracking_number}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                html_content = response.read().decode('utf-8')
            parser = BlueDartParser()
            parser.feed(html_content)
            result = parser.output
            result["courier"] = "Blue Dart"
            result["tracking_number"] = tracking_number
            return result
        except Exception as e:
            return {"error": str(e), "courier": "Blue Dart"}

class DTDCTracker(Tracker):
    def get_details(self, tracking_number):
        url = "https://www.dtdc.com/wp-json/custom/v1/domestic/track"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        }
        payload = json.dumps({"trackType": "cnno", "trackNumber": tracking_number}).encode('utf-8')
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            # Parse DTDC specific response
            events = []
            status = "Unknown"
            if data.get("statuses") and isinstance(data["statuses"], list):
                for event in data["statuses"]:
                     # Clean remarks (remove HTML tags)
                     desc = event.get("statusDescription", "")
                     # Quick and dirty HTML tag removal
                     desc = desc.replace("<br>", " ").replace("<b>", "").replace("</b>", "")
                     
                     events.append({
                         "location": event.get("actCityName") or event.get("actBranchName") or "N/A",
                         "details": desc,
                         "date": event.get("statusTimestamp", "").split()[0] if event.get("statusTimestamp") else "",
                         "time": event.get("statusTimestamp", "").split()[1] if event.get("statusTimestamp") and len(event.get("statusTimestamp").split()) > 1 else ""
                     })
                if events:
                    status = events[0].get("details", "Unknown") # Latest event is usually first? OR user provided code says first?
                    # "events.length > 0 ? events[0].status" suggests first one is latest
            
            header = data.get("header", {})
            current_status = header.get("currentStatusDescription") or status
            
            # DTDC checks
            is_delivered = "Delivered" in current_status or "Successful" in current_status
            if is_delivered and "Delivered" not in current_status:
                 current_status += " (Delivered)" # Normalize for UI check
            
            delivery_details = {
                "Origin": header.get("originCity"),
                "Destination": header.get("destinationCity"),
                "Pieces": header.get("noOfPieces"),
                "Service": header.get("serviceName"),
                "Date of Delivery": "N/A", # DTDC might not provide this explicitly in header
                "Recipient": "N/A"
            }
            
            return {
                "status": current_status,
                "delivery_details": delivery_details,
                "scans": events,
                "courier": "DTDC",
                "tracking_number": tracking_number
            }
        except Exception as e:
             return {"error": str(e), "courier": "DTDC"}

class DelhiveryTracker(Tracker):
    def get_details(self, tracking_number):
        # NOTE: The provided doc says `https://dlv-api.delhivery.com/v3/unified-tracking`
        # But also mentions a proxy in supabase. Let's try direct API first as per doc?
        # Actually doc "DELHIVERY_API_INTEGRATION.md" says:
        # Headers: Host: dlv-api.delhivery.com ...
        # Endpoint: https://dlv-api.delhivery.com/v3/unified-tracking?wbn=...
        
        url = f"https://dlv-api.delhivery.com/v3/unified-tracking?wbn={tracking_number}"
        headers = {
            "Host": "dlv-api.delhivery.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
            "Referer": "https://www.delhivery.com/",
            "Origin": "https://www.delhivery.com",
            "Accept": "application/json, text/plain, */*"
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            # Parse Delhivery response
            # data.data[0] contains shipment info
            if not data.get("data"):
                 return {"error": "No data found", "courier": "Delhivery"}
            
            shipment = data["data"][0]
            events = []
            
            # Parsing logic adapted from typescript file
            if shipment.get("trackingStates"):
                 for ts in shipment["trackingStates"]:
                     if ts.get("scans"):
                         for scan in ts["scans"]:
                             events.append({
                                 "location": scan.get("scannedLocation") or scan.get("cityLocation") or "N/A",
                                 "details": scan.get("scanNslRemark") or scan.get("scan") or "Scan",
                                 "date": (scan.get("scanDateTime") or "").split("T")[0],
                                 "time": (scan.get("scanDateTime") or "").split("T")[-1][:5] # Simple slice
                             })
            
            # Sort events desc? They might come sorted.
            
            delivery_details = {
                "Origin": shipment.get("consignor"),
                "Destination": shipment.get("destination"),
                "Expected Delivery": shipment.get("deliveryDate"),
                "Recipient": shipment.get("consignee")
            }
            
            status = "Unknown"
            if shipment.get("status"):
                 status = shipment["status"].get("status") or shipment["status"].get("statusType")
            
            return {
                "status": status,
                "delivery_details": delivery_details,
                "scans": events,
                "courier": "Delhivery",
                "tracking_number": tracking_number
            }
        except Exception as e:
            return {"error": str(e), "courier": "Delhivery"}

# --- MAIN APP LOGIC ---

TRACKING_FILE = "tracking_list_v2.json"

def get_tracker(courier):
    if courier == "Blue Dart": return BlueDartTracker()
    if courier == "DTDC": return DTDCTracker()
    if courier == "Delhivery": return DelhiveryTracker()
    return None

def load_tracking_list():
    # Migration logic: Check if old file exists
    if os.path.exists("tracking_list.json") and not os.path.exists(TRACKING_FILE):
        try:
            with open("tracking_list.json", 'r') as f:
                old_data = json.load(f)
            new_data = {}
            for tid, info in old_data.items():
                new_data[tid] = {
                    "courier": "Blue Dart", # Assume old are Blue Dart
                    "status": info.get("status"),
                    "last_checked": info.get("last_checked"),
                    "summary": info.get("summary")
                }
            save_tracking_list(new_data)
            print("Migrated old tracking data to v2 format.")
        except: pass

    if not os.path.exists(TRACKING_FILE):
        return {}
    try:
        with open(TRACKING_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_tracking_list(data):
    with open(TRACKING_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# --- TUI IMPLEMENTATION ---

def run_tui(stdscr):
    # Setup
    curses.curs_set(0)
    stdscr.nodelay(1)
    stdscr.timeout(100)
    
    curses.start_color()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Header
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK) # Delivered
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)# Pending
    curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE) # Selected
    curses.init_pair(5, curses.COLOR_CYAN, curses.COLOR_BLACK)  # Courier Badge
    
    saved_list = load_tracking_list()
    items_list = []
    
    def refresh_data_list():
        nonlocal items_list
        items_list = []
        for tid, info in saved_list.items():
             items_list.append({"id": tid, "info": info})
        items_list.sort(key=lambda x: 0 if x["info"]["status"] != "Delivered" else 1)

    refresh_data_list()
    
    def draw_box(msg):
        h, w = stdscr.getmaxyx()
        box_h, box_w = 5, 50
        start_y, start_x = (h - box_h) // 2, (w - box_w) // 2
        win = curses.newwin(box_h, box_w, start_y, start_x)
        win.box()
        msg_x = (box_w - len(msg)) // 2
        win.addstr(2, msg_x if msg_x>0 else 1, msg[:box_w-2], curses.A_BOLD)
        win.refresh()
        return win

    def ask_courier():
        h, w = stdscr.getmaxyx()
        win = curses.newwin(10, 40, (h-10)//2, (w-40)//2)
        win.box()
        win.keypad(True)
        options = ["Blue Dart", "DTDC", "Delhivery"]
        sel = 0
        while True:
            win.addstr(1, 2, "Select Courier:", curses.A_BOLD)
            for i, opt in enumerate(options):
                style = curses.A_REVERSE if i == sel else curses.A_NORMAL
                win.addstr(3+i, 4, f" {opt} ", style)
            win.refresh()
            key = win.getch()
            if key == curses.KEY_UP and sel > 0: sel -= 1
            elif key == curses.KEY_DOWN and sel < len(options)-1: sel += 1
            elif key == 10: return options[sel] # Enter
            elif key == 27: return None # Esc

    current_row = 0
    message = "Welcome"
    
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        
        # Header
        header = f" MULTI-CARRIER TRACKING | {len(items_list)} Parcels "
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(0, 0, header + " " * (width - len(header) - 1))
        stdscr.attroff(curses.color_pair(1))
        
        # List
        list_pad_y = 2
        for idx, item in enumerate(items_list):
            if list_pad_y + idx >= height - 2: break
            
            tid = item['id']
            info = item['info']
            courier = info.get('courier', 'Unknown')
            # Shorten courier name
            c_code = "BD" if courier == "Blue Dart" else "DT" if courier == "DTDC" else "DL"
            
            summary = info.get('summary') or {}
            status = summary.get('status', info.get('status', 'Unknown'))
            is_delivered = "Delivered" in status or "delivered" in status.lower()
            
            style = curses.A_NORMAL
            if idx == current_row: style = curses.color_pair(4) | curses.A_BOLD
            
            try:
                # Format: [BD] 123456... | Status
                stdscr.addstr(list_pad_y + idx, 0, f" [{c_code}] {tid:<15} | {status[:40]:<40} ", style)
                # Color hacks if not selected
                if idx != current_row:
                    stdscr.addstr(list_pad_y + idx, 2, c_code, curses.color_pair(5))
                    status_color = curses.color_pair(2) if is_delivered else curses.color_pair(3)
                    stdscr.addstr(list_pad_y + idx, 24, f"{status[:40]:<40}", status_color)
            except: pass

        # Controls
        controls = "[Q]uit [A]dd [D]el [R]efresh [Enter]Details"
        try:
            stdscr.addstr(height-1, 0, controls, curses.color_pair(1))
            stdscr.addstr(height-1, width - len(message) - 2, message, curses.color_pair(1))
        except: pass

        key = stdscr.getch()
        
        if key in [ord('q'), ord('Q')]: break
        elif key == curses.KEY_DOWN and current_row < len(items_list)-1: current_row += 1
        elif key == curses.KEY_UP and current_row > 0: current_row -= 1
        
        elif key in [ord('a'), ord('A')]:
            # 1. Ask Courier
            courier = ask_courier()
            stdscr.clear()
            if courier:
                # 2. Ask ID
                stdscr.nodelay(0) # Blocking input
                curses.echo(); curses.curs_set(1)
                stdscr.addstr(height-2, 0, f"Enter {courier} ID: ")
                try:
                    inp = stdscr.getstr(height-2, 20, 20).decode('utf-8').strip()
                    if inp:
                        if inp not in saved_list:
                            saved_list[inp] = {"courier": courier, "status": "Pending"}
                            save_tracking_list(saved_list)
                            refresh_data_list()
                            message = f"Added {inp}"
                        else: message = "Exists!"
                except: pass
                curses.noecho(); curses.curs_set(0)
                stdscr.nodelay(1) # Restore non-blocking
        
        elif key in [ord('d'), ord('D')]: 
            # Delete Logic (simplified from previous)
            if items_list:
                tid = items_list[current_row]['id']
                del saved_list[tid]
                save_tracking_list(saved_list)
                refresh_data_list()
                if current_row >= len(items_list): current_row = max(0, len(items_list)-1)
                message = f"Deleted {tid}"
        
        elif key in [ord('r'), ord('R')]:
            loader = draw_box("Refreshing all...")
            for i, item in enumerate(items_list):
                tid = item['id']
                courier = item['info'].get("courier", "Blue Dart")
                loader.addstr(2, 2, f"Checking {courier} {tid}"[:46]); loader.refresh()
                
                tracker = get_tracker(courier)
                if tracker:
                    data = tracker.get_details(tid)
                    if not data.get("error"):
                        status = data.get("status", "Unknown")
                        summary = {
                            "status": status,
                            "recipient": data.get("delivery_details", {}).get("Recipient", "N/A")
                        }
                        saved_list[tid].update({
                            "status": "Delivered" if "Delivered" in status else "Pending",
                            "last_checked": "Now",
                            "summary": summary
                        })
            save_tracking_list(saved_list)
            del loader
            refresh_data_list()
            message = "Refreshed!"

        elif key == 10: # Enter (Details)
            if items_list:
                tid = items_list[current_row]['id']
                info = items_list[current_row]['info']
                courier = info.get("courier", "Blue Dart")
                
                loader = draw_box(f"Fetching {courier}...")
                tracker = get_tracker(courier)
                data = tracker.get_details(tid)
                del loader
                
                # Show details window
                win = curses.newwin(height-4, width-4, 2, 2)
                win.box()
                win.addstr(1, 2, f"{courier} | {tid}", curses.A_BOLD)
                
                if "error" in data:
                    win.addstr(3, 2, f"Error: {data['error']}", curses.color_pair(3))
                else:
                    r = 3
                    # Details
                    for k, v in data.get("delivery_details", {}).items():
                        if r < height-8 and v:
                            win.addstr(r, 2, f"{k}: {v}")
                            r += 1
                    
                    r += 1
                    win.addstr(r, 2, "HISTORY:", curses.A_BOLD); r+=1
                    for s in data.get("scans", []):
                        if r < height-6:
                            line = f"{s.get('date')} {s.get('time')} {s.get('location')} - {s.get('details')}"
                            win.addstr(r, 2, line[:width-10]); r+=1
                
                win.addstr(height-6, 2, "Press Any Key")
                win.refresh()
                win.getch()
                del win
                stdscr.touchwin() # Redraw background

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tracking_number", nargs="?", help="Optional Tracking Number")
    parser.add_argument("--add", help="Add ID")
    parser.add_argument("--delete", help="Delete ID")
    parser.add_argument("--courier", choices=["Blue Dart", "DTDC", "Delhivery"], help="Courier Name")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--force", action="store_true", help="Force Refresh")
    parser.add_argument("--test-file", help="Test HTML file")
    args = parser.parse_args()
    
    saved_list = load_tracking_list()

    # Case 1: Add new ID
    if args.add:
        courier = args.courier or "Blue Dart" # Default
        if args.add not in saved_list:
            saved_list[args.add] = {"courier": courier, "status": "Pending"}
            save_tracking_list(saved_list)
            print(f"Added {args.add} ({courier})")
        else:
            print(f"Tracking number {args.add} already exists.")
        sys.exit(0)

    # Case 2: Delete ID
    if args.delete:
        if args.delete in saved_list:
            del saved_list[args.delete]
            save_tracking_list(saved_list)
            print(f"Deleted {args.delete}")
        else:
            print(f"ID {args.delete} not found")
        sys.exit(0)

    # Case 3: Single ID Track
    if args.tracking_number:
        courier = args.courier or "Blue Dart"
        tracker = get_tracker(courier)
        if tracker:
            data = tracker.get_details(args.tracking_number) # TODO: Pass test-file if supported by tracker
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                # Basic print for now since print_pretty was not fully migrated yet? 
                # Or we can just print JSON for CLI single track in this version or generic print.
                print(json.dumps(data, indent=2)) 
        sys.exit(0)

    # Case 4: Batch/JSON Mode
    if args.json or args.force:
        results = {}
        for tid, info in saved_list.items():
            courier = info.get("courier", "Blue Dart")
            status = info.get("status")
            
            # Skip if delivered and not forced
            if status == "Delivered" and not args.force:
                results[tid] = info
                continue
            
            tracker = get_tracker(courier)
            if tracker:
                data = tracker.get_details(tid)
                # Update saved list
                if not data.get("error"):
                    status_txt = data.get("status", "Unknown")
                    summary = {
                        "status": status_txt,
                        "recipient": data.get("delivery_details", {}).get("Recipient", "N/A")
                    }
                    saved_list[tid].update({
                        "status": "Delivered" if "Delivered" in status_txt else "Pending",
                        "last_checked": "Now",
                        "summary": summary
                    })
                    results[tid] = data # Return full data in JSON output
                else:
                    results[tid] = {"error": data.get("error"), "courier": courier}
        
        save_tracking_list(saved_list)
        if args.json:
            print(json.dumps(results, indent=2))
        sys.exit(0)

    # Default: TUI
    try:
        curses.wrapper(run_tui)
    except Exception as e:
        print(f"TUI Error: {e}")
