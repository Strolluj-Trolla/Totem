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
            clean = line.strip()

            if clean.startswith("Player "):
                try:
                    # Player <nick> has <hand> cards in hand and <table> cards on the table.
                    m = re.match(
                        r"Player\s+(\S+)\s+has\s+(\d+)\s+cards in hand\s+and\s+(\d+)\s+cards on the table",
                        clean
                    )
                    if not m:
                        print("BAD PLAYER LINE:", clean)
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

                except Exception as e:
                    print("PARSE ERROR:", e, "LINE:", clean)
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

        self.game_buffer = ""

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

        # # graficzna karta
        # self.card_canvas = tk.Canvas(f, width=120, height=160, bg="white")
        # self.card_canvas.pack(pady=10)

        # --- RZĄD KART: LEWE 2x2, DUŻA, PRAWE 2x2 ---

        row = ttk.Frame(f)
        row.pack(fill=tk.X, pady=10)

        # LEWA STRONA (2x2)
        left_frame = ttk.Frame(row)
        left_frame.pack(side=tk.LEFT, padx=10)

        self.left_cards = []
        for r in range(2):
            for c in range(2):
                canvas = tk.Canvas(left_frame, width=60, height=80, bg="white")
                canvas.grid(row=r, column=c, padx=3, pady=3)
                self.left_cards.append(canvas)

        # DUŻA KARTA KLIENTA
        self.card_canvas = tk.Canvas(row, width=120, height=160, bg="white")
        self.card_canvas.pack(side=tk.LEFT, padx=20)

        # PRAWA STRONA (2x2, ostatnia komórka pusta)
        right_frame = ttk.Frame(row)
        right_frame.pack(side=tk.LEFT, padx=10)

        self.right_cards = []
        for r in range(2):
            for c in range(2):
                if r == 1 and c == 1:
                    tk.Label(right_frame, text="").grid(row=r, column=c, padx=3, pady=3)
                    continue
                canvas = tk.Canvas(right_frame, width=60, height=80, bg="white")
                canvas.grid(row=r, column=c, padx=3, pady=3)
                self.right_cards.append(canvas)

        bottom = ttk.Frame(f)
        bottom.pack(fill=tk.X, pady=10)

        ttk.Button(bottom, text="Draw", command=self.send_draw).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="Grab", command=self.send_grab).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="Leave", command=self.send_leave).pack(side=tk.LEFT, padx=5)

    def _build_log_tab(self):
        f = self.tab_log

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

        self.cmd_entry.bind("<Return>", send_manual_cmd)

        self.send_cmd_button = ttk.Button(cmd_frame, text="Send", command=send_manual_cmd)
        self.send_cmd_button.pack(side=tk.LEFT, padx=5)


    def _draw_small_card(self, canvas, color, shape):
        canvas.delete("all")
        if color is None:
            return
        fill = CARD_COLORS.get(color, "white")
        canvas.create_rectangle(5, 5, 55, 75, fill=fill, outline="black")
        canvas.create_text(30, 40, text=str(shape), font=("Arial", 14, "bold"))

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

    def _return_to_lobby_after_game(self):
        # wyślij leave do serwera
        if self.net:
            self.net.send_line("leave")

        # zablokuj zakładkę gry
        self.tabs.tab(2, state="disabled")

        # odblokuj lobby
        self.tabs.tab(1, state="normal")

        # przełącz na lobby
        self.tabs.select(self.tab_lobby)

        # odśwież listę pokoi po krótkiej chwili
        self.root.after(500, self.send_list)

    def _auto_refresh_lobby(self):
        current_tab = self.tabs.index(self.tabs.select())
        if current_tab == 1:
            self.send_list()

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
            self.tabs.tab(0, state="disabled")
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

        # 5. WYGRANA / PRZEGRANA
        if "You won the game!" in data:
            messagebox.showinfo("Wygrana!", "Gratulacje! Wygrałeś grę!")
            self._return_to_lobby_after_game()
            return

        if "You lost the game." in data:
            messagebox.showinfo("Przegrana", "Niestety, przegrałeś grę.")
            self._return_to_lobby_after_game()
            return

        # 5. LOBBY — serwer zawsze zaczyna od "Available rooms:"
        if "Available rooms:" in data:
            # zaczynamy NOWY blok lobby
            start = data.index("Available rooms:")
            self.lobby_buffer = data[start:]
            return

        # jeśli jesteśmy w trakcie odbierania lobby
        if hasattr(self, "lobby_buffer"):
            # dokładamy kolejne fragmenty
            self.lobby_buffer += data

            block = self.lobby_buffer

            # blok lobby jest kompletny TYLKO jeśli zawiera stan pokoju
            if "Waiting to start the match." not in block and \
               "Match in progress." not in block:
                # dalej czekamy na resztę danych
                return

            # mamy pełny blok → spróbuj sparsować
            try:
                lobby = LobbyState.parse(block)
            except Exception:
                # jeśli parser się wywali, nie psuj GUI
                del self.lobby_buffer
                return

            self.current_lobby = lobby

            # czyścimy tabelę DOPIERO TERAZ
            for item in self.lobby_tree.get_children():
                self.lobby_tree.delete(item)

            # wstawiamy pokoje
            for r in lobby.rooms:
                self.lobby_tree.insert("", tk.END, values=(
                    r["id"],
                    ", ".join(r["players"]),
                    r["spectators"],
                    r["state"],
                ))

            # ustal, w którym pokoju jest klient
            self.current_room_id = None
            for r in lobby.rooms:
                if self.nickname in r["players"]:
                    self.current_room_id = r["id"]
                    break

            # obsługa przycisku START
            self.start_lobby_button.configure(state=tk.DISABLED)
            for r in lobby.rooms:
                if self.nickname in r["players"]:
                    host = r["players"][0] if r["players"] else None
                    if host == self.nickname and r["state"].startswith("Waiting"):
                        self.start_lobby_button.configure(state=tk.NORMAL)

            # ten blok lobby jest już przetworzony
            del self.lobby_buffer
            return


        # 6. GRA — reszta danych trafia do bufora gry
        self.game_buffer += data

        # Czy mamy początek bloku?
        if "Turn " not in self.game_buffer:
            return

        # Czy mamy koniec bloku?
        if "spectators watching." not in self.game_buffer:
            return

        # Wytnij blok od Turn do spectators watching
        start = self.game_buffer.index("Turn ")
        end = self.game_buffer.index("spectators watching.") + len("spectators watching.")

        block = self.game_buffer[start:end]

        # Przekaż do parsera
        #DEBUG
        print("=== BLOCK SENT TO PARSER ===")
        print(repr(block))
        print("============================")


        self._parse_game(block)

        # Usuń przetworzoną część z bufora
        self.game_buffer = self.game_buffer[end:]

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

        current_player = game.current_player_nick


        # podświetlenie aktualnego gracza
        for item in self.game_tree.get_children():
            vals = self.game_tree.item(item, "values")
            if vals[0] == current_player:
                self.game_tree.selection_set(item)
                self.game_tree.see(item)
                break

        # rysowanie MOJEJ karty (karta klienta)
        self.card_canvas.delete("all")

        my_nick = self.nickname
        my_player = None

        for p in game.players:
            if p["nick"] == my_nick:
                my_player = p
                break

        if my_player and my_player["color"] is not None:
            color = CARD_COLORS.get(my_player["color"], "white")
            self.card_canvas.create_rectangle(
                10, 10, 110, 150, fill=color, outline="black"
            )
            self.card_canvas.create_text(
                60, 80, text=str(my_player["shape"]),
                font=("Arial", 32, "bold")
            )

        # --- RYSOWANIE KART INNYCH GRACZY ---

        # znajdź indeks klienta
        my_index = None
        for i, p in enumerate(game.players):
            if p["nick"] == self.nickname:
                my_index = i
                break

        # wyczyść małe canvasy
        for c in self.left_cards + self.right_cards:
            c.delete("all")

        if my_index is not None:
            # zbuduj listę graczy wokół klienta (bez klienta)
            others = []
            for offset in range(1, len(game.players)):
                idx = (my_index + offset) % len(game.players)
                player = game.players[idx]
                if player["nick"] != self.nickname:
                    others.append(player)

            # lewa strona: pierwsze 4
            left_players = others[:4]

            # prawa strona: kolejne 3
            right_players = others[4:7]

            # narysuj lewe
            for canvas, player in zip(self.left_cards, left_players):
                self._draw_small_card(canvas, player["color"], player["shape"])

            # narysuj prawe
            for canvas, player in zip(self.right_cards, right_players):
                self._draw_small_card(canvas, player["color"], player["shape"])

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