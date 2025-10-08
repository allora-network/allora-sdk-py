#!/usr/bin/env python3

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from collections import defaultdict

@dataclass
class WindowEvent:
    timestamp: datetime
    window_type: str
    action: str
    topic: int
    nonce: int
    height: int

@dataclass
class Window:
    window_type: str
    nonce: int
    start_time: datetime
    end_time: Optional[datetime] = None
    start_height: int = 0
    end_height: Optional[int] = None

def parse_log_line(line: str) -> Optional[WindowEvent]:
    # Pattern to match log lines
    pattern = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO [🚀✨] (Worker|Reputer) submission window (opened|closed) \(topic (\d+), nonce (\d+), height (\d+)\)'

    match = re.match(pattern, line)
    if not match:
        return None

    timestamp_str, window_type, action, topic, nonce, height = match.groups()
    timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')

    return WindowEvent(
        timestamp=timestamp,
        window_type=window_type.lower(),
        action=action,
        topic=int(topic),
        nonce=int(nonce),
        height=int(height)
    )

def parse_logs(log_text: str) -> List[WindowEvent]:
    events = []
    for line in log_text.strip().split('\n'):
        event = parse_log_line(line)
        if event:
            events.append(event)
    return sorted(events, key=lambda x: x.timestamp)

def create_windows(events: List[WindowEvent]) -> List[Window]:
    # Track open windows by (window_type, nonce)
    open_windows = {}
    completed_windows = []

    for event in events:
        key = (event.window_type, event.nonce)

        if event.action == "opened":
            # Create new window
            window = Window(
                window_type=event.window_type,
                nonce=event.nonce,
                start_time=event.timestamp,
                start_height=event.height
            )
            open_windows[key] = window

        elif event.action == "closed":
            # Close existing window
            if key in open_windows:
                window = open_windows.pop(key)
                window.end_time = event.timestamp
                window.end_height = event.height
                completed_windows.append(window)
            else:
                # Close event without matching open - create instantaneous window
                # This handles cases where close happens before open at same height
                window = Window(
                    window_type=event.window_type,
                    nonce=event.nonce,
                    start_time=event.timestamp,
                    end_time=event.timestamp,
                    start_height=event.height,
                    end_height=event.height
                )
                completed_windows.append(window)

    # Don't add remaining open windows - only plot windows that actually closed

    return completed_windows

def plot_windows(windows: List[Window]):
    if not windows:
        print("No windows to plot")
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 8), sharex=True)

    # Separate worker and reputer windows
    worker_windows = [w for w in windows if w.window_type == "worker"]
    reputer_windows = [w for w in windows if w.window_type == "reputer"]

    # Find height range
    all_heights = []
    for window in windows:
        all_heights.append(window.start_height)
        if window.end_height:
            all_heights.append(window.end_height)

    if not all_heights:
        print("No valid heights found")
        return

    min_height = min(all_heights)
    max_height = max(all_heights)
    height_range = max_height - min_height

    def find_available_lane(occupied_lanes, start_height, end_height, is_worker=False):
        """Find the first available lane (y-position) for a window that doesn't overlap with existing windows"""
        for lane in range(20):  # Check up to 20 lanes
            if lane not in occupied_lanes:
                occupied_lanes[lane] = []

            # Check if this lane has any overlapping windows
            overlap = False
            for existing_start, existing_end in occupied_lanes[lane]:
                if is_worker:
                    # For worker windows, add buffer zone of 10 blocks to prevent label overlap
                    buffer = 20
                    if not (end_height + buffer <= existing_start or start_height >= existing_end + buffer):
                        overlap = True
                        break
                else:
                    # For reputer windows, use exact overlap detection
                    if not (end_height <= existing_start or start_height >= existing_end):
                        overlap = True
                        break

            if not overlap:
                occupied_lanes[lane].append((start_height, end_height))
                return lane

        # If all lanes are occupied, use the first one (fallback)
        return 0

    def plot_window_lane(ax, windows_list, lane_name, color):
        if not windows_list:
            ax.set_ylim(0, 1)
            ax.set_yticks([0.5])
            ax.set_yticklabels([f"{lane_name} (empty)"])
            return

        # Track occupied space in each lane to avoid overlaps
        occupied_lanes = {}
        window_positions = []

        for window in windows_list:
            start_height = window.start_height

            if window.end_height:
                end_height = window.end_height
                width = end_height - start_height
                # Handle instantaneous windows (close before open at same height)
                if width == 0:
                    width = 1  # Make it a thin line
                    end_height = start_height + width
            else:
                # Default width for open windows (10% of total range)
                width = max(height_range * 0.1, 50)  # minimum 50 blocks
                end_height = start_height + width

            # Find an available lane for this window
            is_worker = lane_name.startswith("Worker")
            y_pos = find_available_lane(occupied_lanes, start_height, end_height, is_worker)
            window_positions.append((window, y_pos, start_height, width))

        # Draw all windows
        max_lane_used = 0
        for window, y_pos, start_height, width in window_positions:
            max_lane_used = max(max_lane_used, y_pos)

            # Create horizontal bar
            ax.barh(y_pos, width, left=start_height, height=0.8,
                   color=color, alpha=0.7, edgecolor='black', linewidth=0.5)

            # Add nonce and height range label in center of bar
            text_x = start_height + width/2
            nonce_short = str(window.nonce)[-5:]  # Last 5 digits
            start_short = str(start_height)[-5:]  # Last 5 digits
            end_short = str(int(start_height + width))[-5:]  # Last 5 digits
            label_text = f"{nonce_short}\n{start_short}–{end_short}"
            ax.text(text_x, y_pos, label_text, ha='center', va='center',
                   fontsize=7, fontweight='bold')

        # Set y-axis limits to use available space efficiently
        ax.set_ylim(-0.4, max_lane_used + 0.4)

        # Set y-axis ticks and labels
        if max_lane_used == 0:
            ax.set_yticks([0])
            ax.set_yticklabels([lane_name])
        else:
            # Show lane name at the middle of the used space
            middle_tick = max_lane_used // 2
            tick_positions = [i for i in range(max_lane_used + 1)]
            ax.set_yticks(tick_positions)
            labels = [""] * len(tick_positions)
            labels[middle_tick] = lane_name
            ax.set_yticklabels(labels)

        ax.grid(True, alpha=0.3)

    # Plot worker windows (top lane)
    plot_window_lane(ax1, worker_windows, "Worker", "lightblue")

    # Plot reputer windows (bottom lane)
    plot_window_lane(ax2, reputer_windows, "Reputer", "lightcoral")

    # Format x-axis for height values
    ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))

    # Set reasonable tick intervals based on height range
    if height_range < 100:
        tick_interval = 10
    elif height_range < 1000:
        tick_interval = 50
    else:
        tick_interval = 100

    # Set x-axis ticks
    start_tick = (min_height // tick_interval) * tick_interval
    end_tick = ((max_height // tick_interval) + 1) * tick_interval
    ax2.set_xticks(range(int(start_tick), int(end_tick), tick_interval))

    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)

    plt.xlabel('Block Height')
    plt.title('Allora Submission Windows by Block Height')
    plt.tight_layout()
    plt.show()

def run(logs: str):
    # Parse logs
    events = parse_logs(logs)
    print(f"Parsed {len(events)} events")

    # Create windows
    windows = create_windows(events)
    print(f"Created {len(windows)} windows")

    # Display window information
    for window in windows:
        status = "closed" if window.end_time else "open"
        duration = ""
        if window.end_time:
            duration = f" ({(window.end_time - window.start_time).total_seconds():.1f}s)"
        print(f"{window.window_type.title()} window {window.nonce}: {status}{duration}")

    # Plot windows
    plot_windows(windows)

def main():
    parser = argparse.ArgumentParser(
        "Plot a visualization of a topic's lifecycle over the given block range"
    )
    parser.add_argument(
        "--log_file",
        help="AlloraWorker log file",
        required=True,
    )
    args = parser.parse_args()
    try:
        with open(args.log_file, 'r') as f:
            logs = f.read()
        run(logs)
    except FileNotFoundError:
        print("Error: logs.txt file not found. Please create a logs.txt file with your log data.")
        return
    except Exception as e:
        print(f"Error reading logs.txt: {e}")
        return


if __name__ == "__main__":
    main()
