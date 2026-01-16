import socket
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox
import sys
import re

CARD_COLORS = {
    0: "#ff4d4d",
    1: "#4dff4d",
    2: "#4d4dff",
    3: "#ffff4d",
}

class NetworkClient:
    def __init__(self, host, port, on_receive, on_disconnect):
        self.host = host
        self.port = port
        self.on_receive = on_receive
        self.on_disconnect = on_disconnect

        self.sock = None
        self.running = False
        self.recv_thread = None
        self.connected = False

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        try:
            self.sock.connect((self.host, self.port))
            self.running = True
            self.connected = True
            self.recv_thread = threading.Thread(target=self._receiver_loop, daemon=True)
            self.recv_thread.start()
            return True
        except Exception as e:
            messagebox.showerror("Connection Error", f"Cannot connect to server:\n{e}")
            return False

    def _receiver_loop(self):
        buffer = ""
        try:
            while self.running:
                try:
                    data = self.sock.recv(4096)
                    if not data:
                        break
                    text = data.decode("utf-8", errors="replace")
                    buffer += text
                    
                    while '\n' in buffer:
                        line_end = buffer.find('\n')
                        line = buffer[:line_end].rstrip('\r')
                        buffer = buffer[line_end + 1:]
                        
                        if line:
                            self.on_receive(line, "server")
                except socket.timeout:
                    continue
                except Exception:
                    break
        except:
            pass
        finally:
            self.running = False
            self.connected = False
            self.on_disconnect()

    def send_line(self, line: str):
        if not line.endswith("\n"):
            line += "\n"
        try:
            self.sock.sendall(line.encode("utf-8"))
        except:
            pass

    def close(self):
        self.running = False
        self.connected = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except:
            pass
        try:
            self.sock.close()
        except:
            pass

class GameState:
    def __init__(self):
        self.turn = None
        self.players = []
        self.spectators = 0
        self.current_player_nick = None

    @staticmethod
    def parse(text: str):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return None

        state = GameState()
        state.players = []
        current_player = None

        for line in lines:
            if line.startswith("Turn "):
                try:
                    state.turn = int(line.split()[1])
                except:
                    state.turn = None
                continue

            if line.startswith("Current player:"):
                state.current_player_nick = line.split(":", 1)[1].strip()
                continue

            clean = line.strip()

            if clean.startswith("Player "):
                try:
                    m = re.match(
                        r"Player\s+(\S+)\s+has\s+(\d+)\s+cards in hand\s+and\s+(\d+)\s+cards on the table",
                        clean
                    )
                    if not m:
                        continue

                    nick = m.group(1)
                    hand = int(m.group(2))
                    table = int(m.group(3))

                    current_player = {
                        "nick": nick,
                        "hand": hand,
                        "table": table,
                        "color": None,
                        "shape": None,
                    }
                    state.players.append(current_player)

                except Exception:
                    continue

            elif clean.startswith("Currently on top-") and current_player:
                try:
                    m = re.search(r"color\s+(\d+),\s*shape\s+(\d+)", clean)
                    if m:
                        current_player["color"] = int(m.group(1))
                        current_player["shape"] = int(m.group(2))
                except:
                    pass

            elif "spectators watching" in clean:
                try:
                    state.spectators = int(clean.split()[0])
                except:
                    state.spectators = 0

        return state

class LobbyState:
    def __init__(self):
        self.rooms = []

    @staticmethod
    def parse(text: str):
        lobby = LobbyState()
        lobby.rooms = []

        lines = text.splitlines()
        current_room = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("Room ") and "players:" in line:
                if current_room:
                    lobby.rooms.append(current_room)

                try:
                    room_id = int(line.split()[1].split('-')[0])
                    current_room = {
                        "id": room_id,
                        "players": [],
                        "spectators": 0,
                        "state": "",
                    }
                except:
                    current_room = None
                continue

            if current_room is None:
                continue

            if line and not any(x in line for x in ["spectators", "Waiting", "Match"]):
                current_room["players"].append(line)
                continue

            if "spectators" in line:
                try:
                    current_room["spectators"] = int(line.split()[0])
                except:
                    current_room["spectators"] = 0
                continue

            if "Waiting to start the match." in line:
                current_room["state"] = "Waiting to start the match."
                continue
            elif "Match in progress." in line:
                current_room["state"] = "Match in progress."
                continue

        if current_room:
            lobby.rooms.append(current_room)

        return lobby

class TotemClientGUI:
    def __init__(self, root, host, port):
        self.root = root
        self.host = host
        self.port = port

        self.net = None
        self.msg_queue = queue.Queue()

        self.nickname = None
        self.nickname_set = False
        self.in_room = False
        self.current_room_id = None
        self.leaving_room = False
        self.is_spectator = False
        self.spectator_refresh_timer = None
        self.lobby_refresh_timer = None
        self.game_started = False

        self.game_buffer = ""
        self.lobby_buffer = ""
        self.in_lobby_response = False

        self.current_lobby = LobbyState()
        self.current_game = GameState()

        self._build_ui()
        self._schedule_poll()

    def _build_ui(self):
        self.root.title("Totem Client")

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True)

        self.tab_connect = ttk.Frame(self.tabs)
        self.tab_lobby = ttk.Frame(self.tabs)
        self.tab_game = ttk.Frame(self.tabs)
        self.tab_log = ttk.Frame(self.tabs)

        self.tabs.add(self.tab_connect, text="Connection")
        self.tabs.add(self.tab_lobby, text="Lobby")
        self.tabs.add(self.tab_game, text="Game")
        self.tabs.add(self.tab_log, text="Log")

        self.tabs.tab(1, state="disabled")
        self.tabs.tab(2, state="disabled")

        self._build_connect_tab()
        self._build_lobby_tab()
        self._build_game_tab()
        self._build_log_tab()

    def _build_connect_tab(self):
        f = self.tab_connect

        ttk.Label(f, text=f"Connecting to {self.host}:{self.port}").pack(pady=10)

        connect_frame = ttk.Frame(f)
        connect_frame.pack(pady=10)

        ttk.Button(connect_frame, text="Connect to Server", command=self.on_connect).pack(pady=5)

        self.connection_status = ttk.Label(f, text="Not connected", foreground="red")
        self.connection_status.pack(pady=5)

        nick_frame = ttk.Frame(f)
        nick_frame.pack(pady=10)

        ttk.Label(nick_frame, text="Nickname:").pack(side=tk.LEFT)
        self.nick_entry = ttk.Entry(nick_frame, width=20, state="disabled")
        self.nick_entry.pack(side=tk.LEFT, padx=5)
        self.nick_entry.insert(0, "Player")

        self.set_nick_button = ttk.Button(nick_frame, text="Set Nickname", command=self.on_set_nick, state="disabled")
        self.set_nick_button.pack(side=tk.LEFT, padx=5)

    def _build_lobby_tab(self):
        f = self.tab_lobby

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=5)

        ttk.Button(top, text="Refresh List", command=self.send_list).pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text="Room ID:").pack(side=tk.LEFT)
        self.room_entry = ttk.Entry(top, width=8)
        self.room_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Create", command=self.send_create).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Leave", command=self.send_leave).pack(side=tk.LEFT, padx=5)

        self.start_lobby_button = ttk.Button(top, text="Start Game", command=self.send_start, state=tk.DISABLED)
        self.start_lobby_button.pack(side=tk.LEFT, padx=5)

        columns = ("id", "players", "spectators", "state")
        self.lobby_tree = ttk.Treeview(f, columns=columns, show="headings", height=15)
        self.lobby_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        for col in columns:
            self.lobby_tree.heading(col, text=col.capitalize())
        self.lobby_tree.column("id", width=80)
        self.lobby_tree.column("players", width=200)
        self.lobby_tree.column("spectators", width=80)
        self.lobby_tree.column("state", width=200)

        bottom = ttk.Frame(f)
        bottom.pack(fill=tk.X, pady=5)

        ttk.Button(bottom, text="Join", command=self.on_join).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="Spectate", command=self.on_spectate).pack(side=tk.LEFT, padx=5)

    def _build_game_tab(self):
        f = self.tab_game

        columns = ("nick", "hand", "table", "card")
        self.game_tree = ttk.Treeview(f, columns=columns, show="headings", height=10)
        self.game_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        for col in columns:
            self.game_tree.heading(col, text=col.capitalize())
        self.game_tree.column("nick", width=120)
        self.game_tree.column("hand", width=80)
        self.game_tree.column("table", width=80)
        self.game_tree.column("card", width=100)

        self.normal_player_frame = ttk.Frame(f)
        
        status_frame = ttk.Frame(self.normal_player_frame)
        status_frame.pack(fill=tk.X, pady=5)
        
        self.status_label = ttk.Label(status_frame, text="Turn: -, Spectators: -")
        self.status_label.pack(side=tk.LEFT, padx=10)

        ttk.Label(status_frame, text="Turn #:").pack(side=tk.LEFT)
        self.turn_entry = ttk.Entry(status_frame, width=6)
        self.turn_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(status_frame, text="Refresh", command=self.send_refresh).pack(side=tk.LEFT, padx=5)

        cards_frame = ttk.Frame(self.normal_player_frame)
        cards_frame.pack(fill=tk.X, pady=10)
        
        left_frame = ttk.Frame(cards_frame)
        left_frame.pack(side=tk.LEFT, padx=10)

        self.left_cards = []
        self.left_nicks = []
        for r in range(2):
            for c in range(2):
                card_frame = ttk.Frame(left_frame)
                card_frame.grid(row=r, column=c, padx=3, pady=3)
                
                canvas = tk.Canvas(card_frame, width=60, height=80, bg="white")
                canvas.pack()
                
                label = ttk.Label(card_frame, text="", font=("Arial", 8))
                label.pack()
                
                self.left_cards.append(canvas)
                self.left_nicks.append(label)

        self.card_canvas = tk.Canvas(cards_frame, width=120, height=160, bg="white")
        self.card_canvas.pack(side=tk.LEFT, padx=20)

        right_frame = ttk.Frame(cards_frame)
        right_frame.pack(side=tk.LEFT, padx=10)

        self.right_cards = []
        self.right_nicks = []
        for r in range(2):
            for c in range(2):
                card_frame = ttk.Frame(right_frame)
                card_frame.grid(row=r, column=c, padx=3, pady=3)
                
                canvas = tk.Canvas(card_frame, width=60, height=80, bg="white")
                canvas.pack()
                
                label = ttk.Label(card_frame, text="", font=("Arial", 8))
                label.pack()
                
                self.right_cards.append(canvas)
                self.right_nicks.append(label)

        self.spectator_frame = ttk.Frame(f)
        
        ttk.Label(self.spectator_frame, text="Spectator View - Top Cards:").pack(pady=5)
        
        cards_container = ttk.Frame(self.spectator_frame)
        cards_container.pack()
        
        self.spectator_cards = []
        self.spectator_labels = []
        for r in range(2):
            for c in range(4):
                card_frame = ttk.Frame(cards_container)
                card_frame.grid(row=r, column=c, padx=5, pady=5)
                
                canvas = tk.Canvas(card_frame, width=50, height=70, bg="white")
                canvas.pack()
                
                label = ttk.Label(card_frame, text="", font=("Arial", 8))
                label.pack()
                
                self.spectator_cards.append(canvas)
                self.spectator_labels.append(label)

        self.bottom_frame = ttk.Frame(f)
        self.bottom_frame.pack(fill=tk.X, pady=10)

        self.draw_button = ttk.Button(self.bottom_frame, text="Draw", command=self.send_draw)
        self.draw_button.pack(side=tk.LEFT, padx=5)
        
        self.grab_button = ttk.Button(self.bottom_frame, text="Grab", command=self.send_grab)
        self.grab_button.pack(side=tk.LEFT, padx=5)
        
        self.leave_game_button = ttk.Button(self.bottom_frame, text="Leave", command=self.send_leave)
        self.leave_game_button.pack(side=tk.LEFT, padx=5)

        self._update_spectator_view()

    def _build_log_tab(self):
        f = self.tab_log

        log_frame = ttk.Frame(f)
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, height=25, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_config("client", foreground="#00aa00")
        self.log_text.tag_config("server", foreground="#0066ff")
        self.log_text.tag_config("error", foreground="#cc0000")
        self.log_text.tag_config("system", foreground="#888888")

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text["yscrollcommand"] = scrollbar.set

        cmd_frame = ttk.Frame(f)
        cmd_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

        ttk.Label(cmd_frame, text="Command:").pack(side=tk.LEFT, padx=5)
        self.cmd_entry = ttk.Entry(cmd_frame)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.cmd_entry.bind("<Return>", lambda e: self.send_manual_cmd())

        self.send_cmd_button = ttk.Button(cmd_frame, text="Send", command=self.send_manual_cmd)
        self.send_cmd_button.pack(side=tk.LEFT, padx=5)

    def _draw_small_card(self, canvas, color, shape, nick=""):
        canvas.delete("all")
        if color is None:
            canvas.create_rectangle(2, 2, 58, 78, fill="lightgray", outline="black")
            canvas.create_text(30, 30, text="?", font=("Arial", 12))
        else:
            fill = CARD_COLORS.get(color, "white")
            canvas.create_rectangle(5, 5, 55, 75, fill=fill, outline="black", width=2)
            canvas.create_text(30, 30, text=str(shape), font=("Arial", 14, "bold"))
        
        if nick:
            canvas.create_text(30, 60, text=nick[:6], font=("Arial", 8))

    def _draw_big_card(self, canvas, color, shape):
        canvas.delete("all")
        if color is None:
            canvas.create_rectangle(5, 5, 115, 155, fill="lightgray", outline="black", width=2)
            canvas.create_text(60, 80, text="?", font=("Arial", 48))
            return
        fill = CARD_COLORS.get(color, "white")
        canvas.create_rectangle(10, 10, 110, 150, fill=fill, outline="black", width=3)
        canvas.create_text(60, 80, text=str(shape), font=("Arial", 32, "bold"))

    def _draw_spectator_card(self, canvas, color, shape, player_nick):
        canvas.delete("all")
        if color is None:
            canvas.create_rectangle(2, 2, 48, 68, fill="lightgray", outline="black")
            canvas.create_text(25, 25, text="?", font=("Arial", 10))
        else:
            fill = CARD_COLORS.get(color, "white")
            canvas.create_rectangle(2, 2, 48, 68, fill=fill, outline="black", width=1)
            canvas.create_text(25, 25, text=str(shape), font=("Arial", 10, "bold"))

    def _clear_game_ui(self):
        for item in self.game_tree.get_children():
            self.game_tree.delete(item)
        
        self.status_label.configure(text="Turn: -, Spectators: -")
        self.turn_entry.delete(0, tk.END)
        
        self.card_canvas.delete("all")
        for canvas in self.left_cards + self.right_cards:
            canvas.delete("all")
        
        for label in self.left_nicks + self.right_nicks:
            label.config(text="")
        
        for canvas in self.spectator_cards:
            canvas.delete("all")
        
        for label in self.spectator_labels:
            label.config(text="")

    def _update_spectator_view(self):
        if self.is_spectator:
            self.normal_player_frame.pack_forget()
            self.spectator_frame.pack(fill=tk.X, pady=10)
            self.draw_button.pack_forget()
            self.grab_button.pack_forget()
            self.leave_game_button.pack(side=tk.LEFT, padx=5)
        else:
            self.normal_player_frame.pack(fill=tk.X, pady=5)
            self.spectator_frame.pack_forget()
            self.draw_button.pack(side=tk.LEFT, padx=5)
            self.grab_button.pack(side=tk.LEFT, padx=5)
            self.leave_game_button.pack(side=tk.LEFT, padx=5)

    def _start_spectator_refresh(self):
        if self.spectator_refresh_timer:
            self.root.after_cancel(self.spectator_refresh_timer)
        
        if self.is_spectator and self.in_room and self.net and self.net.connected:
            self.net.send_line("refresh")
            self.msg_queue.put((f"[CLIENT -> SERVER] refresh", "client"))
            self.spectator_refresh_timer = self.root.after(1000, self._start_spectator_refresh)

    def _stop_spectator_refresh(self):
        if self.spectator_refresh_timer:
            self.root.after_cancel(self.spectator_refresh_timer)
            self.spectator_refresh_timer = None

    def _start_lobby_refresh(self):
        if self.lobby_refresh_timer:
            self.root.after_cancel(self.lobby_refresh_timer)
        
        if self.game_started:
            self._stop_lobby_refresh()
            return
            
        if not self.is_spectator and self.in_room and self.net and self.net.connected:
            self.net.send_line("list")
            self.msg_queue.put((f"[CLIENT -> SERVER] list", "client"))
            self.lobby_refresh_timer = self.root.after(5000, self._start_lobby_refresh)

    def _stop_lobby_refresh(self):
        if self.lobby_refresh_timer:
            self.root.after_cancel(self.lobby_refresh_timer)
            self.lobby_refresh_timer = None

    def log(self, text, tag=None):
        self.log_text.configure(state=tk.NORMAL)
        if tag:
            self.log_text.insert(tk.END, text + "\n", tag)
        else:
            self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def on_connect(self):
        self.log(f"[SYSTEM] Connecting to {self.host}:{self.port}...", "system")

        self.net = NetworkClient(
            self.host,
            self.port,
            on_receive=lambda data, tag=None: self.msg_queue.put((data, tag)),
            on_disconnect=self.on_disconnect,
        )
        
        if self.net.connect():
            self.log("[SYSTEM] Connected to server", "system")
            self.connection_status.configure(text="Connected", foreground="green")
            self.nick_entry.configure(state="normal")
            self.set_nick_button.configure(state="normal")
            self.log("[SYSTEM] Choose and set your nickname", "system")

    def on_set_nick(self):
        if not self.net or not self.net.connected:
            messagebox.showerror("Error", "First connect to the server")
            return

        nick = self.nick_entry.get().strip()
        if len(nick) < 3:
            messagebox.showerror("Error", "Nickname must be at least 3 characters long.")
            return
        if len(nick) > 16:
            messagebox.showerror("Error", "Nickname cannot be longer than 16 characters.")
            return

        self.log(f"[SYSTEM] Trying to set nickname: {nick}", "system")
        self.net.send_line(nick)
        self.msg_queue.put((f"[CLIENT -> SERVER] {nick}", "client"))

    def on_disconnect(self):
        self.log("[SYSTEM] Disconnected from server.", "system")
        self._stop_spectator_refresh()
        self._stop_lobby_refresh()
        self.connection_status.configure(text="Not connected", foreground="red")
        self.nick_entry.configure(state="disabled")
        self.set_nick_button.configure(state="disabled")
        self.tabs.tab(1, state="disabled")
        self.tabs.tab(2, state="disabled")
        self.tabs.tab(0, state="normal")
        self.tabs.select(0)
        self.is_spectator = False
        self.in_room = False
        self.game_started = False

    def send_manual_cmd(self):
        cmd = self.cmd_entry.get().strip()
        if cmd and self.net:
            self.net.send_line(cmd)
            self.msg_queue.put((f"[CLIENT -> SERVER] {cmd}", "client"))
            self.cmd_entry.delete(0, tk.END)

    def send_list(self):
        if self.net and not self.game_started:
            self.net.send_line("list")
            self.msg_queue.put((f"[CLIENT -> SERVER] list", "client"))

    def send_create(self):
        rid = self.room_entry.get().strip()
        if rid.isdigit() and self.net:
            self.net.send_line(f"create {rid}")
            self.msg_queue.put((f"[CLIENT -> SERVER] create {rid}", "client"))
            self.root.after(1000, self.send_list)

    def send_leave(self):
        if self.net:
            self._stop_spectator_refresh()
            self._stop_lobby_refresh()
            self.game_started = False
            self.leaving_room = True
            self.net.send_line("leave")
            self.msg_queue.put((f"[CLIENT -> SERVER] leave", "client"))
            self.current_room_id = None
            self.in_room = False
            self.is_spectator = False
            
            self.tabs.tab(2, state="disabled")
            self.tabs.tab(1, state="normal")
            self.tabs.select(self.tab_lobby)
            
            self._clear_game_ui()
            self.game_buffer = ""
            
            self.root.after(2000, lambda: setattr(self, 'leaving_room', False))
            
            self.root.after(500, self.send_list)

    def send_start(self):
        if self.net and not self.is_spectator:
            self.net.send_line("start")
            self.msg_queue.put((f"[CLIENT -> SERVER] start", "client"))
            self._stop_lobby_refresh()
            self.game_started = True

    def send_refresh(self):
        if self.net:
            self.net.send_line("refresh")
            self.msg_queue.put((f"[CLIENT -> SERVER] refresh", "client"))

    def send_draw(self):
        if self.net and not self.is_spectator:
            t = self.turn_entry.get().strip()
            if t.isdigit():
                self.net.send_line(f"draw {t}")
                self.msg_queue.put((f"[CLIENT -> SERVER] draw {t}", "client"))

    def send_grab(self):
        if self.net and not self.is_spectator:
            t = self.turn_entry.get().strip()
            if t.isdigit():
                self.net.send_line(f"grab {t}")
                self.msg_queue.put((f"[CLIENT -> SERVER] grab {t}", "client"))

    def on_join(self):
        sel = self.lobby_tree.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select a room from the list")
            return
        rid = self.lobby_tree.item(sel[0], "values")[0]
        if self.net:
            self._stop_spectator_refresh()
            self._stop_lobby_refresh()
            self.game_started = False
            self.net.send_line(f"join {rid}")
            self.msg_queue.put((f"[CLIENT -> SERVER] join {rid}", "client"))
            self.is_spectator = False
            self.in_room = False
            self._update_spectator_view()
            self.root.after(1000, self.send_list)
            self.root.after(6000, self._start_lobby_refresh)

    def on_spectate(self):
        sel = self.lobby_tree.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select a room from the list")
            return
        rid = self.lobby_tree.item(sel[0], "values")[0]
        if self.net:
            self._stop_spectator_refresh()
            self._stop_lobby_refresh()
            self.game_started = False
            self.net.send_line(f"spectate {rid}")
            self.msg_queue.put((f"[CLIENT -> SERVER] spectate {rid}", "client"))
            self.is_spectator = True
            self.current_room_id = rid
            self.in_room = True
            self._update_spectator_view()
            self.root.after(500, self._start_spectator_refresh)

    def _return_to_lobby_after_game(self):
        if self.net:
            self.net.send_line("leave")
            self.msg_queue.put((f"[CLIENT -> SERVER] leave", "client"))
        
        self._stop_spectator_refresh()
        self._stop_lobby_refresh()
        self.game_started = False
        self.tabs.tab(2, state="disabled")
        self.tabs.tab(1, state="normal")
        self.tabs.select(self.tab_lobby)
        
        self.current_room_id = None
        self.in_room = False
        self.is_spectator = False
        
        self._clear_game_ui()
        self.game_buffer = ""
        
        self.root.after(500, self.send_list)

    def _schedule_poll(self):
        self.root.after(50, self._poll)

    def _poll(self):
        try:
            while True:
                data, tag = self.msg_queue.get_nowait()
                self._handle_data(data, tag)
        except queue.Empty:
            pass
        self._schedule_poll()

    def _handle_data(self, data, tag=None):
        if tag:
            self.log(data, tag)
        else:
            self.log(data, "server")

        if self.leaving_room:
            if "Currently not in a room" in data or "Not in a room" in data:
                self.leaving_room = False
            return

        if not self.nickname_set and "Nickname set successfully" in data:
            self.nickname_set = True
            self.nickname = self.nick_entry.get().strip()
            self.tabs.tab(0, state="disabled")
            self.tabs.tab(1, state="normal")
            self.tabs.select(self.tab_lobby)
            self.log("[SYSTEM] Nickname set successfully, switching to lobby", "system")
            self.root.after(1000, self.send_list)
            return

        if not self.nickname_set and "Nickname unavailable" in data:
            messagebox.showerror("Nickname taken", "This nickname is already taken. Choose another.")
            self.log("[SYSTEM] Nickname taken, choose another", "system")
            return

        if not self.nickname_set and "Nickname must be between" in data:
            messagebox.showerror("Invalid nickname", "Nickname must be between 3 and 16 characters.")
            self.log("[SYSTEM] Invalid nickname", "system")
            return

        if "Currently not in a room" in data or "Not in a room" in data:
            self._stop_spectator_refresh()
            self._stop_lobby_refresh()
            self.game_started = False
            self.current_room_id = None
            self.in_room = False
            self.is_spectator = False
            self.tabs.tab(2, state="disabled")
            self.tabs.tab(1, state="normal")
            self.tabs.select(self.tab_lobby)
            self._clear_game_ui()
            self.game_buffer = ""
            self.root.after(500, self.send_list)
            return

        if "Turn " in data and not self.in_lobby_response:
            self.game_buffer = data + "\n"
            if "spectators watching" in data:
                self.root.after(100, self._process_game_buffer)
            return
            
        if self.game_buffer and not self.in_lobby_response:
            self.game_buffer += data + "\n"
            
            end_indicators = [
                "spectators watching",
                "spectators\n",
                "cards on the table",
                "Match halted",
                "All players left"
            ]
            
            if any(indicator in data for indicator in end_indicators):
                self.root.after(100, self._process_game_buffer)
            return

        if "Match in progress." in data and self.in_room:
            self.log("[SYSTEM] Game started, switching to game tab", "system")
            self.tabs.tab(2, state="normal")
            self.tabs.select(self.tab_game)
            self._update_spectator_view()
            self._stop_lobby_refresh()
            self.game_started = True
            return

        error_keywords = [
            "permission", "Not in a room", "Invalid", "full", 
            "doesn't exist", "less than 2 players", "Error",
            "Already in a room", "Currently not in a room",
            "Command too long", "Unrecognized command",
            "Room already exists", "has already started"
        ]

        lines = data.split('\n')
        for line in lines:
            line = line.strip()
            if any(keyword in line for keyword in error_keywords):
                if "Available commands" not in line:
                    self.log(f"[ERROR] {line}", "error")

        if "You won the game!" in data:
            if not self.is_spectator:
                messagebox.showinfo("You won!", "Congratulations! You won the game!")
            self._return_to_lobby_after_game()
            return
            
        if "You lost the game." in data:
            if not self.is_spectator:
                messagebox.showinfo("You lost", "Unfortunately, you lost the game.")
            self._return_to_lobby_after_game()
            return

        if "Available rooms:" in data and not self.game_started:
            self.in_lobby_response = True
            self.lobby_buffer = data + "\n"
            return
            
        if self.in_lobby_response:
            self.lobby_buffer += data + "\n"
            
            if ("Waiting to start the match." in data or 
                "Match in progress." in data or
                "spectators\n" in data or
                "spectators watching" in data):
                self.root.after(100, self._process_lobby_buffer)
            return

        if "Room " in data and "players:" in data and not self.game_buffer and self.in_room:
            self._process_single_room(data)
            return

    def _process_lobby_buffer(self):
        if not self.lobby_buffer:
            return
            
        try:
            lobby = LobbyState.parse(self.lobby_buffer)
            if lobby and lobby.rooms:
                self.current_lobby = lobby
                
                for item in self.lobby_tree.get_children():
                    self.lobby_tree.delete(item)
                
                for room in lobby.rooms:
                    players_str = ", ".join(room["players"]) if room["players"] else "(empty)"
                    self.lobby_tree.insert("", tk.END, values=(
                        room["id"],
                        players_str,
                        room["spectators"],
                        room["state"],
                    ))
                
                self.current_room_id = None
                self.is_spectator = False
                for room in lobby.rooms:
                    if self.nickname in room["players"]:
                        self.current_room_id = room["id"]
                        self.in_room = True
                        self.is_spectator = False
                        break
                
                self.start_lobby_button.configure(state=tk.DISABLED)
                if self.current_room_id and not self.is_spectator:
                    for room in lobby.rooms:
                        if room["id"] == self.current_room_id:
                            if room["players"] and room["players"][0] == self.nickname:
                                if room["state"] == "Waiting to start the match.":
                                    self.start_lobby_button.configure(state=tk.NORMAL)
                            break
                
                if not self.game_started and not self.is_spectator and self.in_room and not self.lobby_refresh_timer:
                    self.root.after(1000, self._start_lobby_refresh)
                
        except Exception as e:
            self.log(f"[ERROR] Lobby parsing error: {e}", "error")
        finally:
            self.lobby_buffer = ""
            self.in_lobby_response = False

    def _process_game_buffer(self):
        if not self.game_buffer:
            return
            
        try:
            game = GameState.parse(self.game_buffer)
            if game and game.players:
                self.current_game = game
                
                self.tabs.tab(2, state="normal")
                self.tabs.select(self.tab_game)
                
                self._update_spectator_view()
                
                turn_str = str(game.turn) if game.turn is not None else "-"
                spectator_text = " (SPECTATOR)" if self.is_spectator else ""
                self.status_label.configure(
                    text=f"Turn: {turn_str}, Spectators: {game.spectators}{spectator_text}"
                )
                
                for item in self.game_tree.get_children():
                    self.game_tree.delete(item)
                
                for player in game.players:
                    card_str = f"({player['color']}, {player['shape']})" if player['color'] is not None else "-"
                    self.game_tree.insert("", tk.END, values=(
                        player["nick"],
                        player["hand"],
                        player["table"],
                        card_str
                    ))
                
                if game.turn is not None and not self.is_spectator:
                    self.turn_entry.delete(0, tk.END)
                    self.turn_entry.insert(0, str(game.turn))
                
                if self.is_spectator:
                    self._draw_spectator_cards(game)
                else:
                    self._draw_cards(game)
                
        except Exception as e:
            self.log(f"[ERROR] Game parsing error: {e}\nBuffer was:\n{self.game_buffer}", "error")
        finally:
            self.game_buffer = ""

    def _process_single_room(self, data):
        try:
            lines = data.splitlines()
            room_info = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                if line.startswith("Room ") and "players:" in line:
                    if room_info:
                        break
                    try:
                        room_id = int(line.split()[1].split('-')[0])
                        room_info = {
                            "id": room_id,
                            "players": [],
                            "spectators": 0,
                            "state": "",
                        }
                    except:
                        room_info = None
                    
                elif room_info:
                    if line and not any(x in line for x in ["spectators", "Waiting", "Match"]):
                        room_info["players"].append(line)
                    elif "spectators" in line:
                        try:
                            room_info["spectators"] = int(line.split()[0])
                        except:
                            pass
                    elif "Waiting to start the match." in line:
                        room_info["state"] = "Waiting to start the match."
                    elif "Match in progress." in line:
                        room_info["state"] = "Match in progress."
            
            if room_info:
                players_str = ", ".join(room_info["players"]) if room_info["players"] else "(empty)"
                msg = f"Room {room_info['id']}: {players_str}, {room_info['spectators']} spectators, {room_info['state']}"
                self.log(msg, "system")
                
                if self.nickname in room_info["players"]:
                    self.current_room_id = room_info["id"]
                    self.in_room = True
                    self.is_spectator = False
                    self.log(f"[SYSTEM] Joined room {self.current_room_id}", "system")
                elif self.is_spectator:
                    self.current_room_id = room_info["id"]
                    self.in_room = True
                    self.log(f"[SYSTEM] Joined as spectator to room {self.current_room_id}", "system")
                    
        except Exception as e:
            self.log(f"[ERROR] Room parsing error: {e}", "error")

    def _draw_cards(self, game):
        my_player = None
        for player in game.players:
            if player["nick"] == self.nickname:
                my_player = player
                break
        
        if my_player:
            self._draw_big_card(self.card_canvas, my_player["color"], my_player["shape"])
        else:
            self._draw_big_card(self.card_canvas, None, None)
        
        my_index = -1
        for i, player in enumerate(game.players):
            if player["nick"] == self.nickname:
                my_index = i
                break
        
        for canvas in self.left_cards + self.right_cards:
            canvas.delete("all")
        
        for label in self.left_nicks + self.right_nicks:
            label.config(text="")
        
        if my_index >= 0 and len(game.players) > 1:
            other_players = []
            for i in range(1, len(game.players)):
                idx = (my_index + i) % len(game.players)
                other_players.append(game.players[idx])
            
            for i in range(min(4, len(other_players))):
                player = other_players[i]
                if i < len(self.left_cards):
                    self._draw_small_card(self.left_cards[i], player["color"], player["shape"], player["nick"])
            
            for i in range(min(3, len(other_players) - 4)):
                player = other_players[i + 4]
                if i < len(self.right_cards):
                    self._draw_small_card(self.right_cards[i], player["color"], player["shape"], player["nick"])

    def _draw_spectator_cards(self, game):
        for canvas in self.spectator_cards:
            canvas.delete("all")
        
        for label in self.spectator_labels:
            label.config(text="")
        
        for i in range(min(8, len(game.players))):
            player = game.players[i]
            canvas = self.spectator_cards[i]
            label = self.spectator_labels[i]
            
            if player['color'] is not None:
                fill = CARD_COLORS.get(player['color'], "white")
                canvas.create_rectangle(5, 5, 45, 65, fill=fill, outline="black", width=2)
                canvas.create_text(25, 25, text=str(player['shape']), font=("Arial", 10, "bold"))
            else:
                canvas.create_rectangle(5, 5, 45, 65, fill="lightgray", outline="black", width=2)
                canvas.create_text(25, 25, text="?", font=("Arial", 10))
            
            label.config(text=player['nick'][:8] if player['nick'] else "")

    def force_switch_to_game(self):
        self.log("[SYSTEM] Forcing switch to game", "system")
        self.tabs.tab(2, state="normal")
        self.tabs.select(self.tab_game)
        self._update_spectator_view()
        self.send_refresh()


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 client.py <IP> <PORT>")
        print("Example: python3 client.py localhost 12345")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2])

    root = tk.Tk()
    root.geometry("1000x750")
    app = TotemClientGUI(root, host, port)

    def on_close():
        if app.net:
            app.net.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == "__main__":
    main()