import socket
import threading
import tkinter as tk
from tkinter import simpledialog, messagebox, scrolledtext
import sys


class TotemClient:
    def __init__(self, ip, port):
        self.server_ip = ip
        self.server_port = port

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False
        self.nick_set = False

        # GUI
        self.root = tk.Tk()
        self.root.title(f"Totem Client — {ip}:{port}")

        self.text_area = scrolledtext.ScrolledText(self.root, width=60, height=20)
        self.text_area.pack()

        self.cmd_entry = tk.Entry(self.root, width=40)
        self.cmd_entry.pack()

        tk.Button(self.root, text="Send Command", command=self.send_cmd).pack()
        tk.Button(self.root, text="Create Lobby", command=self.create_lobby).pack()
        tk.Button(self.root, text="Refresh Lobby List", command=self.refresh_lobbies).pack()

        self.lobby_frame = tk.Frame(self.root)
        self.lobby_frame.pack()

        threading.Thread(target=self.connect).start()
        self.root.mainloop()

    # ---------------- CONNECTION ----------------

    def connect(self):
        try:
            self.sock.connect((self.server_ip, self.server_port))
            self.connected = True
            threading.Thread(target=self.receive_loop, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Error", f"Connection failed: {e}")

    # ---------------- RECEIVING ----------------

    def receive_loop(self):
        buffer = ""

        while True:
            try:
                data = self.sock.recv(2048).decode()
                if not data:
                    break

                buffer += data
                self.text_area.insert(tk.END, data + "\n")
                self.text_area.see(tk.END)

                # Nickname request
                if "Choose your nickname" in buffer and not self.nick_set:
                    self.ask_nick()

                # Detect full lobby list
                if "Available rooms:" in buffer:
                    # Check if the buffer ends with "spectators" or contains "Room "
                    if buffer.strip().endswith("spectators") or buffer.count("Room ") > 0:
                        self.parse_lobbies(buffer)
                        buffer = ""

            except:
                break

    # ---------------- NICKNAME ----------------

    def ask_nick(self):
        nick = simpledialog.askstring("Nick", "Enter your nickname:")
        if nick:
            self.sock.send((nick + "\n").encode())
            self.nick_set = True

    # ---------------- COMMANDS ----------------

    def send_cmd(self):
        cmd = self.cmd_entry.get()
        if cmd:
            self.sock.send((cmd + "\n").encode())
            self.cmd_entry.delete(0, tk.END)

    def create_lobby(self):
        room_id = simpledialog.askinteger("New Lobby", "Enter room ID:")
        if room_id is not None:
            self.sock.send(f"create {room_id}\n".encode())

    def refresh_lobbies(self):
        self.sock.send(b"list\n")

    # ---------------- LOBBY PARSING ----------------

    def parse_lobbies(self, text):
        for widget in self.lobby_frame.winfo_children():
            widget.destroy()

        lines = text.split("\n")

        for line in lines:
            if line.startswith("Room "):
                parts = line.split()
                room_id = parts[1].split("-")[0]

                frame = tk.Frame(self.lobby_frame)
                frame.pack(anchor="w")

                tk.Label(frame, text=f"Lobby {room_id}").pack(side="left")

                tk.Button(
                    frame,
                    text="Join",
                    command=lambda rid=room_id: self.join_lobby(rid)
                ).pack(side="left")

    def join_lobby(self, room_id):
        self.sock.send(f"join {room_id}\n".encode())


# ---------------- RUN ----------------

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 client.py <IP_ADDR> <PORT>")
        sys.exit(1)

    ip = sys.argv[1]
    port = int(sys.argv[2])

    TotemClient(ip, port)