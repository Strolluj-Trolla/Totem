import socket
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox
import sys
import re

# ================== KOLORY KART ==================

CARD_COLORS = {
    0: "#ff4d4d",   
    1: "#4dff4d",
    2: "#4d4dff",   
    3: "#ffff4d",   
}

# ================== WARSTWA SIECIOWA ==================

class NetworkClient:
    def __init__(self, host, port, on_receive, on_disconnect):
        self.host = host
        self.port = port
        self.on_receive = on_receive
        self.on_disconnect = on_disconnect

        self.sock = None
        self.running = False
        self.recv_thread = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.running = True
        self.recv_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        self.recv_thread.start()

    def _receiver_loop(self):
        try:
            while self.running:
                data = self.sock.recv(4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                self.on_receive(text)
        except:
            pass
        finally:
            self.running = False
            self.on_disconnect()

    def send_line(self, line: str):
        if not line.endswith("\n"):
            line += "\n"

        self.on_receive(f"[CLIENT -> SERVER] {line}", "client")

        try:
            self.sock.sendall(line.encode("utf-8"))
        except:
            pass

    def close(self):
        self.running = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except:
            pass
        try:
            self.sock.close()
        except:
            pass

# ================== PARSER STANU GRY ==================

class GameState:
    def __init__(self):
        self.turn = None
        self.players = []
        self.spectators = 0

    @staticmethod
    def parse(text: str):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            return None

        state = GameState()
        state.players = []

        current_player = None

        # Turn X
        if lines[0].startswith("Turn "):
            try:
                state.turn = int(lines[0].split()[1])
            except:
                state.turn = None

        # Current player
        if len(lines) > 1 and lines[1].startswith("Current player:"):
            state.current_player_nick = lines[1].split(":", 1)[1].strip()
        else:
            state.current_player_nick = None

        # Parsowanie graczy
        for line in lines[2:]:
            if line.startswith("Player "):
                # nowy gracz
                try:
                    rest = line[len("Player "):]
                    nick, rest = rest.split(" has ", 1)
                    hand = int(rest.split(" cards in hand")[0])

                    rest2 = rest.split("cards on the table")[0]
                    table = int(rest2.split("and ")[1])

                    current_player = {
                        "nick": nick.strip(),
                        "hand": hand,
                        "table": table,
                        "color": None,
                        "shape": None,
                    }
                    state.players.append(current_player)
                except:
                    continue

            elif line.startswith("Currently on top-") and current_player:
                # karta na wierzchu stołu
                try:
                    frag = line.split("color ")[1]
                    color = int(frag.split(",")[0])
                    shape = int(frag.split("shape ")[1])
                    current_player["color"] = color
                    current_player["shape"] = shape
                except:
                    pass

            elif "spectators watching" in line:
                try:
                    state.spectators = int(line.split()[0])
                except:
                    state.spectators = 0

        return state

# ================== PARSER LOBBY ==================

class LobbyState:
    def __init__(self):
        self.rooms = []

    @staticmethod
    def parse(block):
        lobby = LobbyState()

        lines = block.splitlines()
        current_room = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # --- POCZĄTEK POKOJU ---
            # akceptujemy format serwera: "Room 2- players:"
            m = re.match(r"Room\s+(\d+)-\s*players:", line)
            if m:
                # zapisz poprzedni pokój
                if current_room:
                    lobby.rooms.append(current_room)

                room_id = int(m.group(1))
                current_room = {
                    "id": room_id,
                    "players": [],
                    "spectators": 0,
                    "state": "",
                }
                continue

            # --- GRACZ ---
            # gracz to linia, która NIE jest spectators i NIE jest stanem
            if current_room:
                if line.endswith("spectators"):
                    # np. "0 spectators"
                    try:
                        current_room["spectators"] = int(line.split()[0])
                    except:
                        current_room["spectators"] = 0
                    continue

                if "Waiting" in line or "progress" in line:
                    current_room["state"] = line
                    continue

                # jeśli to nie spectators i nie stan → to GRACZ
                current_room["players"].append(line)
                continue

        # dodaj ostatni pokój
        if current_room:
            lobby.rooms.append(current_room)

        return lobby
    
# ================== GUI ==================

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

        self.buffer = ""

        self.current_lobby = LobbyState()
        self.current_game = GameState()

        self._build_ui()
        self._schedule_poll()

    # ---------- UI ----------

    def _build_ui(self):
        self.root.title("Totem Client")

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True)

        self.tab_connect = ttk.Frame(self.tabs)
        self.tab_lobby = ttk.Frame(self.tabs)
        self.tab_game = ttk.Frame(self.tabs)
        self.tab_log = ttk.Frame(self.tabs)

        self.tabs.add(self.tab_connect, text="Połączenie")
        self.tabs.add(self.tab_lobby, text="Lobby")
        self.tabs.add(self.tab_game, text="Gra")
        self.tabs.add(self.tab_log, text="Log")

        # zakładki lobby i gra są zablokowane na starcie
        self.tabs.tab(1, state="disabled")
        self.tabs.tab(2, state="disabled")

        self._build_connect_tab()
        self._build_lobby_tab()
        self._build_game_tab()
        self._build_log_tab()

    def _build_connect_tab(self):
        f = self.tab_connect

        ttk.Label(f, text=f"Łączenie z {self.host}:{self.port}").pack(pady=10)

        nick_frame = ttk.Frame(f)
        nick_frame.pack(pady=10)

        ttk.Label(nick_frame, text="Nick:").pack(side=tk.LEFT)
        self.nick_entry = ttk.Entry(nick_frame, width=20)
        self.nick_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(nick_frame, text="Połącz i ustaw nick", command=self.on_connect).pack(pady=10)

    def _build_lobby_tab(self):
        f = self.tab_lobby

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=5)

        ttk.Button(top, text="Odśwież listę", command=self.send_list).pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text="Room ID:").pack(side=tk.LEFT)
        self.room_entry = ttk.Entry(top, width=8)
        self.room_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Create", command=self.send_create).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="Leave", command=self.send_leave).pack(side=tk.LEFT, padx=5)

        # NOWY PRZYCISK STARTU W LOBBY
        self.start_lobby_button = ttk.Button(top, text="Start game", command=self.send_start, state=tk.DISABLED)
        self.start_lobby_button.pack(side=tk.LEFT, padx=5)

        columns = ("id", "players", "spectators", "state")
        self.lobby_tree = ttk.Treeview(f, columns=columns, show="headings", height=15)
        self.lobby_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        for col in columns:
            self.lobby_tree.heading(col, text=col.capitalize())

        bottom = ttk.Frame(f)
        bottom.pack(fill=tk.X, pady=5)

        ttk.Button(bottom, text="Join", command=self.on_join).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="Spectate", command=self.on_spectate).pack(side=tk.LEFT, padx=5)

    def _build_game_tab(self):
        f = self.tab_game

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=5)

        self.status_label = ttk.Label(top, text="Turn: -, Spectators: -")
        self.status_label.pack(side=tk.LEFT, padx=10)

        ttk.Label(top, text="Turn #:").pack(side=tk.LEFT)
        self.turn_entry = ttk.Entry(top, width=6)
        self.turn_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Refresh", command=self.send_refresh).pack(side=tk.LEFT, padx=5)

        columns = ("nick", "hand", "table", "card")
        self.game_tree = ttk.Treeview(f, columns=columns, show="headings", height=10)
        self.game_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        for col in columns:
            self.game_tree.heading(col, text=col.capitalize())

        # graficzna karta
        self.card_canvas = tk.Canvas(f, width=120, height=160, bg="white")
        self.card_canvas.pack(pady=10)

        bottom = ttk.Frame(f)
        bottom.pack(fill=tk.X, pady=10)

        ttk.Button(bottom, text="Draw", command=self.send_draw).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="Grab", command=self.send_grab).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="Leave", command=self.send_leave).pack(side=tk.LEFT, padx=5)

    def _build_log_tab(self):
        f = self.tab_log

        # Górna część: log
        log_frame = ttk.Frame(f)
        log_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, height=25, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.log_text.tag_config("client", foreground="#00aa00")
        self.log_text.tag_config("server", foreground="#0066ff")
        self.log_text.tag_config("error",  foreground="#cc0000")

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text["yscrollcommand"] = scrollbar.set

        # Dolna część: ręczne komendy
        cmd_frame = ttk.Frame(f)
        cmd_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

        ttk.Label(cmd_frame, text="Command:").pack(side=tk.LEFT, padx=5)

        self.cmd_entry = ttk.Entry(cmd_frame)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        def send_manual_cmd(event=None):
            cmd = self.cmd_entry.get().strip()
            if cmd and self.net:
                self.net.send_line(cmd)
                self.cmd_entry.delete(0, tk.END)

        # Enter wysyła komendę
        self.cmd_entry.bind("<Return>", send_manual_cmd)

        # Przycisk wysyłania
        self.send_cmd_button = ttk.Button(cmd_frame, text="Send", command=send_manual_cmd)
        self.send_cmd_button.pack(side=tk.LEFT, padx=5)

    # ---------- LOG ----------

    def log(self, text, tag=None):
        self.log_text.configure(state=tk.NORMAL)
        if tag:
            self.log_text.insert(tk.END, text + "\n", tag)
        else:
            self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ---------- POŁĄCZENIE ----------

    def on_connect(self):
        nick = self.nick_entry.get().strip()
        if len(nick) < 3:
            messagebox.showerror("Error", "Nickname must be at least 3 characters long.")
            return
        
        if len(nick) > 16:
            messagebox.showerror("Error", "Nickname cannot be longer than 16 characters.")
            return


        self.nickname = nick

        self.net = NetworkClient(
            self.host,
            self.port,
            on_receive=lambda data, tag=None: self.msg_queue.put((data, tag)),
            on_disconnect=self.on_disconnect,
        )
        self.net.connect()
        self.net.send_line(nick)

    def on_disconnect(self):
        self.log("[INFO] Rozłączono z serwerem.")

    # ---------- KOMENDY ----------

    def _auto_refresh_lobby(self):
        # odświeżaj tylko, jeśli aktualna zakładka to LOBBY (index 1)
        current_tab = self.tabs.index(self.tabs.select())
        if current_tab == 1:
            self.send_list()

        # zaplanuj kolejne odświeżenie za 10 sekund
        self.root.after(10000, self._auto_refresh_lobby)

    def send_list(self):
        self.net.send_line("list")

    def send_create(self):
        rid = self.room_entry.get().strip()
        if rid.isdigit():
            self.net.send_line(f"create {rid}")
            self.root.after(1000, self.send_list)

    def send_leave(self):
        self.net.send_line("leave")
        # automatyczne odświeżenie listy po 1 sekundzie
        self.root.after(1000, self.send_list)

    def send_start(self):
        self.net.send_line("start")

    def send_refresh(self):
        self.net.send_line("refresh")

    def send_draw(self):
        t = self.turn_entry.get().strip()
        if t.isdigit():
            self.net.send_line(f"draw {t}")

    def send_grab(self):
        t = self.turn_entry.get().strip()
        if t.isdigit():
            self.net.send_line(f"grab {t}")

    def on_join(self):
        sel = self.lobby_tree.selection()
        if not sel:
            return
        rid = self.lobby_tree.item(sel[0], "values")[0]
        self.net.send_line(f"join {rid}")

    def on_spectate(self):
        sel = self.lobby_tree.selection()
        if not sel:
            return
        rid = self.lobby_tree.item(sel[0], "values")[0]
        self.net.send_line(f"spectate {rid}")

    # ---------- PĘTLA ODBIORU ----------

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

    # ---------- PRZETWARZANIE DANYCH ----------


    def _handle_data(self, data, tag):

        # 1. Logowanie
        if tag:
            self.log(data, tag)
        else:
            self.log(data, "server")

        # 2. Wiadomości klienta nie są parsowane
        if tag == "client":
            return

        # 3. Nick ustawiony
        if "Nickname set successfully" in data and not self.nickname_set:
            self.nickname_set = True
            self.tabs.tab(1, state="normal")
            self.tabs.select(self.tab_lobby)
            messagebox.showinfo("Nick", "Nick ustawiony.")
            self._auto_refresh_lobby()

            return

        # 4. Błędy
        error_keywords = [
            "permission",
            "Not in a room",
            "Invalid",
            "full",
            "doesn't exist",
            "less than 2 players",
            "Error",
            "Already in a room.",
            "Currently not in a room.",
        ]
        if any(k in data for k in error_keywords):
            self.log(data, "error")
            return

        # 5. LOBBY — serwer zawsze zaczyna od "Available rooms:"
        if "Available rooms:" in data:
            # wycinamy blok od nagłówka
            start = data.index("Available rooms:")
            self.lobby_buffer = data[start:]

            # NATYCHMIAST czyścimy tabelę
            for item in self.lobby_tree.get_children():
                self.lobby_tree.delete(item)
            self.current_lobby = None

            # próbujemy sparsować to, co już mamy
            self._parse_lobby(self.lobby_buffer)
            return

        # jeśli jesteśmy w trakcie odbierania lobby
        if hasattr(self, "lobby_buffer"):
            self.lobby_buffer += data
            self._parse_lobby(self.lobby_buffer)
            # return

        # 6. GRA — reszta danych trafia do bufora gry
        self.buffer += data

        # Czy mamy początek bloku?
        if "Turn " not in self.buffer:
            return

        # Czy mamy koniec bloku?
        if "spectators watching." not in self.buffer:
            return

        # Wytnij blok od Turn do spectators watching
        start = self.buffer.index("Turn ")
        end = self.buffer.index("spectators watching.") + len("spectators watching.")

        block = self.buffer[start:end]

        # Przekaż do parsera
        self._parse_game(block)

        # Usuń przetworzoną część z bufora
        self.buffer = self.buffer[end:]

    # ---------- PARSOWANIE LOBBY ----------

    def _parse_lobby(self, block):
        # blok lobby jest kompletny TYLKO jeśli zawiera stan pokoju
        if "Waiting to start the match." not in block and \
        "Match in progress." not in block:
            return  # czekamy na resztę danych

        try:
            lobby = LobbyState.parse(block)
        except Exception:
            return

        self.current_lobby = lobby

        # ZAWSZE czyścimy tabelę
        for item in self.lobby_tree.get_children():
            self.lobby_tree.delete(item)

        # Wstawiamy pokoje
        for r in lobby.rooms:
            self.lobby_tree.insert("", tk.END, values=(
                r["id"],
                ", ".join(r["players"]),
                r["spectators"],
                r["state"],
            ))

        # Ustal, w którym pokoju jest klient
        self.current_room_id = None
        for r in lobby.rooms:
            if self.nickname in r["players"]:
                self.current_room_id = r["id"]
                break

        # Obsługa przycisku START
        self.start_lobby_button.configure(state=tk.DISABLED)

        for r in lobby.rooms:
            if self.nickname in r["players"]:
                host = r["players"][0] if r["players"] else None
                if host == self.nickname and r["state"].startswith("Waiting"):
                    self.start_lobby_button.configure(state=tk.NORMAL)

    # ---------- PARSOWANIE GRY ----------

    def _parse_game(self, block):

        print(">>> PARSING GAME IN GUI")

        try:
            game = GameState.parse(block)
        except:
            return

        if not game:
            return

        print("Players:", game.players)
        print("Current:", game.current_player_nick)

        self.current_game = game

        # odblokuj zakładkę gry
        self.tabs.tab(2, state="normal")
        self.tabs.select(self.tab_game)

        # wyłącz start w lobby
        self.start_lobby_button.configure(state=tk.DISABLED)

        # status
        self.status_label.configure(
            text=f"Turn: {game.turn}, Spectators: {game.spectators}"
        )

        # tabela graczy
        for item in self.game_tree.get_children():
            self.game_tree.delete(item)

        for p in game.players:
            card = "-"
            if p["color"] is not None:
                card = f"({p['color']}, {p['shape']})"

            self.game_tree.insert("", tk.END, values=(
                p["nick"], p["hand"], p["table"], card
            ))

        # heurystyka: gracz z najmniejszą sumą hand+table = aktualny gracz
        min_cards = None
        current_player = None

        for p in game.players:
            total = p["hand"] + p["table"]
            if min_cards is None or total < min_cards:
                min_cards = total
                current_player = p["nick"]

        # podświetlenie aktualnego gracza
        for item in self.game_tree.get_children():
            vals = self.game_tree.item(item, "values")
            if vals[0] == current_player:
                self.game_tree.selection_set(item)
                self.game_tree.see(item)
                break

        # rysowanie karty aktualnego gracza
        self.card_canvas.delete("all")

        for p in game.players:
            if p["nick"] == current_player:
                if p["color"] is not None:
                    color = CARD_COLORS.get(p["color"], "white")
                    self.card_canvas.create_rectangle(
                        10, 10, 110, 150, fill=color, outline="black"
                    )
                    self.card_canvas.create_text(
                        60, 80, text=str(p["shape"]),
                        font=("Arial", 32, "bold")
                    )
                break

        # podpowiedź turn #
        if game.turn is not None:
            self.turn_entry.delete(0, tk.END)
            self.turn_entry.insert(0, str(game.turn))

# ================== MAIN ==================

def main():
    if len(sys.argv) != 3:
        print("Usage: python3 client.py <IP> <PORT>")
        sys.exit(1)

    host = sys.argv[1]
    port = int(sys.argv[2])

    root = tk.Tk()
    app = TotemClientGUI(root, host, port)

    def on_close():
        if app.net:
            app.net.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()