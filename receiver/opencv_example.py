#!/usr/bin/env python3
"""
ScoutCam OpenCV Example
Demonstrates how to receive the RTSP stream in Python/OpenCV for CV processing.

Requirements:
    pip install opencv-python numpy

Usage:
    python opencv_example.py [PI_IP]
    python opencv_example.py 192.168.1.100
    python opencv_example.py scoutcam.local
"""

import sys
import cv2
import numpy as np
from datetime import datetime

# Default Pi address
PI_IP = sys.argv[1] if len(sys.argv) > 1 else "scoutcam.local"
RTSP_URL = f"rtsp://{PI_IP}:8554/cam"


def main():
    print("=" * 50)
    print("  ScoutCam OpenCV Example")
    print("=" * 50)
    print()
    print(f"Connecting to: {RTSP_URL}")
    print()
    print("Controls:")
    print("  q - Quit")
    print("  s - Save screenshot")
    print("  r - Toggle recording")
    print("  d - Toggle detection overlay")
    print()

    # Open RTSP stream with optimized settings for low latency
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)

    # Set buffer size to minimum for lower latency
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print(f"ERROR: Failed to open stream at {RTSP_URL}")
        print()
        print("Troubleshooting:")
        print("  1. Is the Pi powered on and connected to network?")
        print("  2. Is scoutcam service running? SSH and run: scoutcam status")
        print(f"  3. Can you ping the Pi? ping {PI_IP}")
        return 1

    # Get stream properties
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"Stream: {width}x{height} @ {fps:.1f}fps")
    print()

    # State variables
    recording = False
    writer = None
    show_detection = False
    frame_count = 0
    start_time = datetime.now()

    # Simple color detection example (for game pieces)
    # Adjust these HSV ranges for your specific game piece colors
    ORANGE_LOWER = np.array([5, 100, 100])
    ORANGE_UPPER = np.array([25, 255, 255])

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Lost connection to stream, attempting to reconnect...")
            cap.release()
            cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            continue

        frame_count += 1
        display_frame = frame.copy()

        # Example CV processing: detect orange objects (game pieces)
        if show_detection:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, ORANGE_LOWER, ORANGE_UPPER)

            # Find contours
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                area = cv2.contourArea(contour)
                if area > 500:  # Filter small detections
                    x, y, w, h = cv2.boundingRect(contour)
                    cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

                    # Calculate center
                    cx, cy = x + w // 2, y + h // 2
                    cv2.circle(display_frame, (cx, cy), 5, (0, 0, 255), -1)

                    # Display info
                    cv2.putText(display_frame, f"({cx}, {cy})", (x, y - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Calculate FPS
        elapsed = (datetime.now() - start_time).total_seconds()
        actual_fps = frame_count / elapsed if elapsed > 0 else 0

        # Draw status overlay
        status_text = f"FPS: {actual_fps:.1f}"
        if recording:
            status_text += " [REC]"
        if show_detection:
            status_text += " [DET]"

        cv2.putText(display_frame, status_text, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        # Show frame
        cv2.imshow("ScoutCam", display_frame)

        # Recording
        if recording and writer is not None:
            writer.write(frame)

        # Handle key presses
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break

        elif key == ord('s'):
            # Save screenshot
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
            cv2.imwrite(filename, frame)
            print(f"Screenshot saved: {filename}")

        elif key == ord('r'):
            # Toggle recording
            if recording:
                recording = False
                if writer is not None:
                    writer.release()
                    writer = None
                print("Recording stopped")
            else:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"recording_{timestamp}.mp4"
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                writer = cv2.VideoWriter(filename, fourcc, fps, (width, height))
                recording = True
                print(f"Recording started: {filename}")

        elif key == ord('d'):
            # Toggle detection overlay
            show_detection = not show_detection
            print(f"Detection overlay: {'ON' if show_detection else 'OFF'}")

    # Cleanup
    cap.release()
    if writer is not None:
        writer.release()
    cv2.destroyAllWindows()

    print()
    print(f"Processed {frame_count} frames in {elapsed:.1f}s ({actual_fps:.1f} fps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
