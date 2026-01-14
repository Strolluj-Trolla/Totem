#include <netdb.h>
#include <cstdio>
#include <arpa/inet.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/epoll.h>
#include <thread>
#include <vector>
#include <iostream>
#include <boost/interprocess/managed_shared_memory.hpp>
#include <boost/interprocess/allocators/allocator.hpp>
#include <boost/interprocess/sync/named_mutex.hpp>
#include <boost/interprocess/ipc/message_queue.hpp>
#include <boost/container/vector.hpp>
#include <boost/container/string.hpp>
#include <signal.h>
#include <ctime>
#include <algorithm>
#include <random>

#define buff_size 1400
#define shm "TotemMem"
#define clMut "TotemClientMut"
#define rmMut "TotemRoomMut"
#define MQ_NAME "TotemQueue"
#define timeoutLen 30

using namespace boost::interprocess;

typedef managed_shared_memory::segment_manager segman;
typedef allocator<void, segman> alloc;
typedef allocator<char, segman> charAlloc;
typedef boost::container::basic_string<char, std::char_traits<char>,  charAlloc> string;
enum roomState{
    IDLE,
    INPROGRESS
};
typedef enum roomState roomState;
struct client{
    using allocator_type=alloc;
    int fd;
    long roomId;
    string nick;
    client(int fd_, long roomId_, const char* nick_, const allocator_type& allocate): fd(fd_), roomId(roomId_), nick(nick_, charAlloc(allocate)) {}
};
struct room{
    long id;
    client players[8];
    time_t joinTimes[8];
    int spectatorCount;
    roomState state;
    room(const alloc& allocate): id(-1),
        players {client(-1, -1, "", allocate),
            client(-1, -1, "", allocate),
            client(-1, -1, "", allocate),
            client(-1, -1, "", allocate),
            client(-1, -1, "", allocate),
            client(-1, -1, "", allocate),
            client(-1, -1, "", allocate),
            client(-1, -1, "", allocate)},
        joinTimes {-1,-1,-1,-1,-1,-1,-1,-1},
        spectatorCount(0),
        state(IDLE) {}
};
typedef struct client client;
typedef struct room room;
typedef allocator<client, segman> clientAlloc;
typedef allocator<room, segman> roomAlloc;
typedef boost::container::vector<client, clientAlloc> clientVector;
typedef boost::container::vector<room, roomAlloc> roomVector;

struct message{
    int sender;
    char cmd[50];
};
typedef struct message message;

std::string describeRoom(room *temp){
    std::string roomDesc = "Room " + std::to_string(temp->id) + "- players:\n";

    for (int j = 0; j < 8; j++) {
        if (temp->players[j].fd == -1) continue;
        roomDesc += std::string(temp->players[j].nick.c_str()) + "\n";
    }

    roomDesc += std::to_string(temp->spectatorCount) + " spectators\n";
    if(temp->state==IDLE)roomDesc+="Waiting to start the match.\n";
    else roomDesc+="Match in progress.\n";
    return roomDesc;
}

int getArgument(const char* cmd, int startIndex){
    std::string arg="";
    for(int i=startIndex; cmd[i]!='\000'; i++){
        arg+=cmd[i];
    }
    try{
        int num=std::stoi(arg);
        if(num<0)return -1;
        return num;
    }
    catch(...){
        return -1;
    }
}

void handleComms(int clientSocket, sockaddr_in clientAddress){
    managed_shared_memory segment(open_only, shm);
    clientVector* clients = segment.find<clientVector>("clients").first;
    roomVector* rooms     = segment.find<roomVector>("rooms").first;
    named_mutex client_mutex(open_only, clMut);
    named_mutex room_mutex(open_only, rmMut);
    message_queue mq(open_only, MQ_NAME);
    if((clients==0)||(rooms==0)){
        shutdown(clientSocket, SHUT_RDWR);
        close(clientSocket);
        return;
    }

    // bufor danych od klienta
    std::string inputBuffer; 

    char buff[buff_size] = "Connected to the \"Totem\" game server. Choose your nickname:\n";
    printf("Connection from %s\n", inet_ntoa(clientAddress.sin_addr));
    write(clientSocket, buff, strlen(buff));

    unsigned int i;
    bool alreadySet = false;
    message cmd;

    while (true) {
        int readBytes = read(clientSocket, buff, buff_size - 1);
        if (readBytes <= 0) {
            // error lub zamknięcie połączenia przez klienta
            break;
        }

        buff[readBytes] = '\0';
        inputBuffer += buff;

        // przetwarzenie pełnych linii (komend) z bufora
        size_t pos;
        while ((pos = inputBuffer.find('\n')) != std::string::npos) {
            std::string line = inputBuffer.substr(0, pos);
            inputBuffer.erase(0, pos + 1);

            // Kompatybilność z klientami Windows
            if (!line.empty() && line.back() == '\r')
                line.pop_back();

            if (line.empty())
                continue;

            //DEBUG
            printf("[DEBUG] From %s: line='%s', len=%zu\n",
                inet_ntoa(clientAddress.sin_addr),
                line.c_str(),
                line.length());

            // --------- NICKNAME ---------

            if (!alreadySet) {
                std::string nick = line;

                if (nick.length() < 3 || nick.length() > 16) {
                    const char* msg = "Nickname must be between 3 and 16 characters\n";
                    write(clientSocket, msg, strlen(msg));
                    continue;
                }

                //DEBUG
                printf("[DEBUG] Nick attempt from %s: '%s' (len=%zu)\n",
                    inet_ntoa(clientAddress.sin_addr),
                    nick.c_str(),
                    nick.length());

                client_mutex.lock();
                bool available = true;
                unsigned int clientIndex = 0;

                // przeszukiwanie klientów w celu sprawdzenia dostępności nicku
                for (i = 0; i < clients->size(); i++) {
                    if (clients->at(i).fd == clientSocket) {
                        clientIndex = i;
                        if (clients->at(i).nick != "") {
                            alreadySet = true;
                            break;
                        }
                    }
                    if (clients->at(i).nick == nick.c_str()) {
                        available = false;
                        break;
                    }
                }

                if (alreadySet) {
                    const char* msg = "Nickname already set\n";
                    write(clientSocket, msg, strlen(msg));
                    client_mutex.unlock();
                    continue;
                }

                if (available) {
                    clients->at(clientIndex).nick.assign(nick.c_str());
                    const char* ok =
                        "Nickname set successfully.\n"
                        "Available commands: list, create roomId, join roomId, spectate roomId, start, "
                        "draw turnNum, grab turnNum, refresh, leave\n";
                    write(clientSocket, ok, strlen(ok));
                    alreadySet = true;
                } else {
                    const char* msg = "Nickname unavailable, choose another.\n";
                    write(clientSocket, msg, strlen(msg));
                }

                client_mutex.unlock();
            }

            // --------- KOMENDY ---------

            else {
                std::string cmdStr = line;

                cmd.sender = clientSocket;
                memset(cmd.cmd, '\0', 50);
                if (snprintf(cmd.cmd, 50, "%s", cmdStr.c_str()) >= (int)sizeof(cmd.cmd)) {
                    const char* msg = "Command too long.\n";
                    write(clientSocket, msg, strlen(msg));
                    continue;
                }

                // list
                if (cmdStr.rfind("list", 0) == 0) {   // starts_with "list"
                    room_mutex.lock();

                    const char* header = "Available rooms:\n";
                    write(clientSocket, header, strlen(header));

                    for (i = 0; i < rooms->size(); i++) {
                        room temp = rooms->at(i);
                        std::string roomDesc = describeRoom(&temp);
                        write(clientSocket, roomDesc.c_str(), roomDesc.length());
                    }

                    room_mutex.unlock();
                }
                else {
                    // create / join / start / leave - priorytet 0
                    if (cmdStr.rfind("create ", 0) == 0 ||
                        cmdStr.rfind("join ",   0) == 0 ||
                        cmdStr.rfind("start",   0) == 0 ||
                        cmdStr.rfind("leave",   0) == 0 ||
                        cmdStr.rfind("spectate",   0) == 0)
                    {
                        mq.send(&cmd, sizeof(cmd), 0);
                    }
                    // draw / grab / refresh - priorytet 1
                    else if (cmdStr.rfind("draw ",    0) == 0 ||
                             cmdStr.rfind("grab ",    0) == 0 ||
                             cmdStr.rfind("refresh", 0) == 0)
                    {
                        mq.send(&cmd, sizeof(cmd), 1);
                    }
                    else {
                        const char* msg = "Unrecognized command.\n";
                        write(clientSocket, msg, strlen(msg));
                    }
                }
            }
        }
    }

    // wyjście z pętli = klient kończy -> zostaje wysłany "leave" do kolejki
    cmd.sender = clientSocket;
    memset(cmd.cmd, '\0', 50);
    sprintf(cmd.cmd, "leave");
    mq.send(&cmd, sizeof(cmd), 0);
    sleep(1); //odczekać, żeby główny wątek mógł zareagować na komendę

    // usunięcie klienta ze wspólnej listy
    client_mutex.lock();
    i = 0;
    while (i < clients->size()) {
        if (clients->at(i).fd == clientSocket) break;
        i++;
    }
    if (i < clients->size()) {
        printf("Client %s timed out.\n",
               clients->at(i).nick.c_str()[0] ? clients->at(i).nick.c_str() : "unnamed");
        clients->erase(clients->begin() + i);
    }
    client_mutex.unlock();

    shutdown(clientSocket, SHUT_RDWR);
    close(clientSocket);
    return;
}

std::vector<int> updateRoomVars(long roomId, named_mutex& mut, roomVector* rooms, room& gameRoom, int& pCount, std::vector<client>& players){
    mut.lock();
    unsigned int i;
    bool found=false;
    for(i=0; i<rooms->size(); i++){
        if(rooms->at(i).id==roomId){
            found=true;
            break;
        }
    }
    if(!found){
        mut.unlock();
        std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
        printf("[error] Internal error - trying to serve game in room %ld, which doesn't exist.", roomId);
        return std::vector<int>(-1);
    }
    std::vector<client> oldPlayers(players);
    gameRoom=rooms->at(i);
    pCount=0;
    for(i=0; i<8;i++){
        if(gameRoom.players[i].fd!=-1) pCount++;
    }
    players.clear();
    for(i=0; i<8;i++){
        if(gameRoom.players[i].fd!=-1) players.push_back(gameRoom.players[i]);
    };
    mut.unlock();
    std::vector<int> whoLeft;
    for(i=0; i<oldPlayers.size(); i++){
        int cntr=0;
        for(client cl : players){
            if(cl.fd==oldPlayers.at(i).fd)cntr++;
        }
        if(cntr==0)whoLeft.push_back(i);
    }
    return whoLeft;
}

struct card{
    int color;
    int shape;
};
typedef struct card card;

std::string describeState(int &turnNum, int &playerCount, int &currentPlayer, room &gameRoom, std::vector<client> &players, std::vector<std::vector<card>> &hands, std::vector<std::vector<card>> &table){
    std::string state = "Turn " + std::to_string(turnNum) + "\n";
    state += "Current player: " + std::string(players.at(currentPlayer).nick.c_str()) + "\n";
    for (int i = 0; i < playerCount; i++) {
        state += "Player " + std::string(players.at(i).nick.c_str()) +
            " has " + std::to_string(hands.at(i).size()) +
            " cards in hand and " + std::to_string(table.at(i).size()) +
            " cards on the table.\n";

            if (table.at(i).size() > 0) {
                card topc = table.at(i).at(table.at(i).size() - 1);
                state += "Currently on top- color " + std::to_string(topc.color) +
                ", shape " + std::to_string(topc.shape) + "\n";
            }
    }
    state += std::to_string(gameRoom.spectatorCount) + " spectators watching.\n";

    return state;
}

void gameRunner(long roomId){
    managed_shared_memory segment(open_only, shm);
    roomVector* rooms = segment.find<roomVector>("rooms").first;
    named_mutex room_mutex(open_only, rmMut);
    std::string qName="TotemRoom"+std::to_string(roomId);
    message_queue mq(create_only, qName.c_str(), 100, sizeof(message));
    alloc allocInst(segment.get_segment_manager());

    room gameRoom(allocInst);
    std::vector<client> players;
    int playerCount=0;
    updateRoomVars(roomId, room_mutex, rooms, gameRoom, playerCount, players);
    
    std::vector<card> cards;
    for(int i=0;i<18;i++){
        for(int j=0; j<4; j++){
            card c={.color=j, .shape=i};
            cards.push_back(c);
        }
    }

    std::random_device rd;
    std::default_random_engine rng { rd() };
    std::shuffle(std::begin(cards), std::end(cards), rng);
    int currentPlayer=rng()%playerCount;
    std::vector<std::vector<card>> hands;
    std::vector<std::vector<card>> table;
    for(int i=0; i<playerCount; i++){
        hands.push_back(std::vector<card>());
        table.push_back(std::vector<card>());
    }
    for(unsigned int i=0; i<cards.size(); i++){
        hands.at(i%playerCount).push_back(cards.at(i));
    }
    std::vector<card> pub;

    bool end=false;
    int timeout=0;
    message cmd;
    unsigned int prio;
    message_queue::size_type recSize;
    time_t timer=time(NULL);
    int turnNum=0;

        // --- AUTOMATIC REFRESH AFTER GAME INITIALIZATION ---
    std::string state = describeState(turnNum, playerCount, currentPlayer, gameRoom, players, hands, table);
    for (int i = 0; i < playerCount; i++) {
        write(players[i].fd, state.c_str(), state.length());
    }

    while(!end){
        if(mq.try_receive(&cmd, sizeof(cmd), recSize, prio)){
            if((strncmp(cmd.cmd, "leave", 5)==0)||(strncmp(cmd.cmd, "spectate", 8)==0)){
                std::vector<int> whoLeft=updateRoomVars(roomId, room_mutex, rooms, gameRoom, playerCount, players);
                if(!whoLeft.empty()){
                    if(whoLeft.at(0)==-1){
                        printf("Room doesn't exist, stopping the match...\n");
                        return;
                    }
                    for(int j=whoLeft.size()-1; j>=0; j--){
                        if(currentPlayer>=playerCount)currentPlayer=0;
                        if(currentPlayer>whoLeft.at(j))currentPlayer--;
                        for(card c : hands.at(whoLeft.at(j))){
                            pub.push_back(c);
                        }
                        for(card c : table.at(whoLeft.at(j))){
                            pub.push_back(c);
                        }
                        hands.erase(hands.begin()+whoLeft.at(j));
                        table.erase(table.begin()+whoLeft.at(j));
                    }
                }
            }
            else{
                if((strncmp(cmd.cmd, "refresh", 7)==0)){
                    std::string state=describeState(turnNum, playerCount, currentPlayer, gameRoom, players, hands, table);
                    write(cmd.sender, state.c_str(), state.length());
                }
                if((strncmp(cmd.cmd, "draw ", 5)==0)){
                    bool found=false;
                    int i;
                    for(i=0; i<playerCount; i++){
                        if(cmd.sender==players[i].fd){
                            found=true;
                            break;
                        }
                    }
                    if(!found){
                        std::string err="Spectators can't play.\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }
                    int reqTurn=getArgument(cmd.cmd, 5);
                    if(reqTurn==-1){
                        std::string err="Invalid argument.\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }
                    if(reqTurn==turnNum){
                        if(i==currentPlayer){
                            if(hands.at(currentPlayer).size()>0){
                                table.at(currentPlayer).push_back(hands.at(currentPlayer).at(0));
                                hands.at(currentPlayer).erase(hands.at(currentPlayer).begin()+0);
                            }
                            turnNum++;
                            currentPlayer=(currentPlayer+1)%playerCount;
                            timeout=0;
                            timer=time(NULL);

                            // --- BROADCAST NEW GAME STATE AFTER DRAW ---
                            std::string state = describeState(turnNum, playerCount, currentPlayer, gameRoom, players, hands, table);
                            for (int p = 0; p < playerCount; p++) {
                                write(players[p].fd, state.c_str(), state.length());
                            }

                        }
                        else{
                            std::string err="Not your turn.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                    }
                    else{
                        std::string err="Current turn is."+std::to_string(turnNum)+"\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }
                }
                if((strncmp(cmd.cmd, "grab ", 5)==0)){
                    bool found=false;
                    int i;
                    for(i=0; i<playerCount; i++){
                        if(cmd.sender==players[i].fd){
                            found=true;
                            break;
                        }
                    }
                    if(!found){
                        std::string err="Spectators can't play.\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }
                    int reqTurn=getArgument(cmd.cmd, 5);
                    if(reqTurn==-1){
                        std::string err="Invalid argument.\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }
                    if(reqTurn==turnNum){
                        std::vector<int> opps;
                        if(table.at(i).size()>0){
                            card c=table.at(i).at(table.at(i).size()-1);
                            for(int j=0; j<playerCount; j++){
                                if(j==i)continue;
                                if(table.at(j).size()==0)continue;
                                int topInd=table.at(j).size()-1;
                                if(c.shape==table.at(j).at(topInd).shape){
                                    opps.push_back(j);
                                }
                            }
                        }
                        if(opps.empty()){
                            for(int j=0; j<playerCount; j++){
                                for(card opC : table.at(j)){
                                    hands.at(i).push_back(opC);
                                }
                                table.at(j).clear();
                            }
                            for(card opC : pub){
                                hands.at(i).push_back(opC);
                            }
                            pub.clear();
                            std::string mesg="You made a mistake. Take all the cards :)\n";
                            write(cmd.sender, mesg.c_str(), mesg.length());
                        }
                        else{
                            for(unsigned int j=0; j<table.at(i).size(); j++){
                                hands.at(opps.at(j%opps.size())).push_back(table.at(i).at(j));
                            }
                            for(int j : opps){
                                for(card c : table.at(j)){
                                    hands.at(j).push_back(c);
                                }
                                table.at(j).clear();
                            }
                            table.at(i).clear();
                            std::string mesg="You win the fight.\n";
                            write(cmd.sender, mesg.c_str(), mesg.length());
                            mesg="You lost a fight- take cards from the winner.\n";
                            for(int j : opps){
                                write(players[j].fd, mesg.c_str(), mesg.length());
                            }

                            if((hands.at(i).empty())&&(table.at(i).empty())){
                                //wygrana
                                end=true;
                                mesg="You won the game!\n";
                                write(cmd.sender, mesg.c_str(), mesg.length());
                                mesg="You lost the game.\n";
                                for(int j=0; j<playerCount; j++){
                                    if(j==i)continue;
                                    write(players[j].fd, mesg.c_str(), mesg.length());
                                }
                            }
                        }
                    }
                    else{
                        std::string err="Current turn is."+std::to_string(turnNum)+"\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }

                    // --- BROADCAST NEW GAME STATE AFTER GRAB (ONLY IF GAME NOT ENDED) ---
                    if (!end) {
                        std::string state = describeState(turnNum, playerCount, currentPlayer, gameRoom, players, hands, table);
                        
                        for (int p = 0; p < playerCount; p++) {
                            write(players[p].fd, state.c_str(), state.length());
                        }
                    }

                }
            }
        }
        else{
            time_t last=timer;
            timer=time(NULL);
            timeout+=timer-last;
            if(timeout>=30){
                //wystaw mu karte
                if(hands.at(currentPlayer).size()>0){
                    table.at(currentPlayer).push_back(hands.at(currentPlayer).at(0));
                    hands.at(currentPlayer).erase(hands.at(currentPlayer).begin()+0);
                }
                turnNum++;
                currentPlayer=(currentPlayer+1)%playerCount;
                timeout=0;
                timer=time(NULL);
                std::string state = describeState(turnNum, playerCount, currentPlayer, gameRoom, players, hands, table);
                        
                for (int p = 0; p < playerCount; p++) {
                    write(players[p].fd, state.c_str(), state.length());
                }
            }
            usleep(50000);
        }
    }

    return;
}


bool running=true;
void terminator(int signum) {
   printf("Terminating due to signal %d...\n", signum);
   running=false;
   return;
}

int main(int argc, char** argv){
    if(argc<2){
        printf("Enter port number as an argument.\n");
        return 10;
    }

    signal(SIGINT, terminator);
    addrinfo hints{};
    hints.ai_flags=AI_PASSIVE;
    hints.ai_family=AF_INET;
    hints.ai_protocol = IPPROTO_TCP;
    addrinfo * resolved;
    int res;
    if((res= getaddrinfo("0.0.0.0", argv[1], &hints, &resolved))) {fprintf(stderr, "Getaddrinfo failed: %s\n", gai_strerror(res)); return 1;}

    int sock;
    if((sock=socket(resolved->ai_family, resolved->ai_socktype, resolved->ai_protocol))==-1){
        perror("Unable to create a socket.\n");
        return errno;
    };
    const int one = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    bind(sock, resolved->ai_addr, resolved->ai_addrlen);
    listen(sock, 5);
    fcntl(sock, F_SETFL, (fcntl(sock, F_GETFL)|O_NONBLOCK));

    struct shm_remove{
        shm_remove() { shared_memory_object::remove(shm); }
        ~shm_remove(){ shared_memory_object::remove(shm); }
    } shmRemover;
    struct cl_mutex_remove{
        cl_mutex_remove() { named_mutex::remove(clMut); }
        ~cl_mutex_remove(){ named_mutex::remove(clMut); }
    } clientMutexRemover;
    struct rm_mutex_remove{
        rm_mutex_remove() { named_mutex::remove(rmMut); }
        ~rm_mutex_remove(){ named_mutex::remove(rmMut); }
    } roomMutexRemover;
    message_queue::remove(MQ_NAME);
    message_queue mq(create_only, MQ_NAME, 100, sizeof(message));
    named_mutex client_mutex(create_only, clMut);
    named_mutex room_mutex(create_only, rmMut);
    managed_shared_memory segment(create_only, shm, 65536);
    alloc allocInst(segment.get_segment_manager());
    clientVector* clients=segment.construct<clientVector>("clients")(clientAlloc(segment.get_segment_manager()));
    roomVector* rooms=segment.construct<roomVector>("rooms")(roomAlloc(segment.get_segment_manager()));
    

    while(running){
        sockaddr_in clientAddr;
        socklen_t clientAddrLen=sizeof(clientAddr);
        int clientSock=accept(sock, (sockaddr*)&clientAddr, &clientAddrLen);
        if (clientSock != -1) {
            client_mutex.lock();
            clients->emplace_back(clientSock, -1, "", allocInst);
            client_mutex.unlock();

            std::thread(handleComms, clientSock, clientAddr).detach();
        }
        else{
            message cmd;
            unsigned int prio;
            message_queue::size_type recSize;
            if(mq.try_receive(&cmd, sizeof(cmd), recSize, prio)){
                if(prio==0){
                    if(strncmp(cmd.cmd, "leave", 5)==0){
                        int roomId=-1;
                        client_mutex.lock();
                        for(unsigned int i=0; i<clients->size(); i++){
                            if(clients->at(i).fd==cmd.sender){
                                if(clients->at(i).roomId!=-1){
                                    roomId=clients->at(i).roomId;
                                    clients->at(i).roomId=-1;
                                }
                                write(cmd.sender, "Currently not in a room.", 25);
                                break;
                            }
                        }
                        client_mutex.unlock();
                        if(roomId!=-1){
                            room_mutex.lock();
                            bool found=false;
                            unsigned int players=0;
                            unsigned int i;
                            std::string qName="TotemRoom"+std::to_string(roomId);
                            for(i=0; i<rooms->size(); i++){
                                if(rooms->at(i).id==roomId){
                                    for(int j=0; j<8; j++){
                                        if(rooms->at(i).players[j].fd==cmd.sender){
                                            rooms->at(i).players[j].fd=-1;
                                            found=true;
                                        }
                                        if(rooms->at(i).players[j].fd!=-1)players++;
                                    }
                                    if(!found)rooms->at(i).spectatorCount--;
                                    if(rooms->at(i).state==INPROGRESS){
                                        message_queue roomQ(open_only, qName.c_str());
                                        roomQ.send(&cmd, sizeof(cmd), 1);
                                    }
                                    break;
                                }
                            }
                            if((players==0)&&(rooms->at(i).spectatorCount==0)){
                                rooms->erase(rooms->begin()+i);
                                message_queue::remove(qName.c_str());
                            }
                            room_mutex.unlock();
                        }
                    }
                    if(strncmp(cmd.cmd, "create ", 6)==0){
                        client_mutex.lock();
                        bool found=false;
                        unsigned int clientIndex;
                        for(unsigned int i=0; i<clients->size(); i++){
                            if(clients->at(i).fd==cmd.sender){
                                clientIndex=i;
                                if(clients->at(i).roomId!=-1)found=true;
                                break;
                            }
                        }
                        if(found){
                            client_mutex.unlock();
                            write(cmd.sender, "Already in a room.\n", 20);
                            continue;
                        };
                        int roomId=getArgument(cmd.cmd, 7);
                        if(roomId==-1){
                            client_mutex.unlock();
                            write(cmd.sender, "Invalid argument.", 18);
                            continue;
                        }
                        room_mutex.lock();
                        found=false;
                        for(unsigned int i=0; i<rooms->size(); i++){
                            if(rooms->at(i).id==roomId){
                                found=true;
                                break;
                            }
                        }
                        if(found){
                            client_mutex.unlock();
                            room_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" already exists\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        room temp(allocInst);
                        temp.id=roomId;
                        temp.joinTimes[0]=time(NULL);
                        clients->at(clientIndex).roomId=roomId;
                        temp.players[0]=clients->at(clientIndex);
                        rooms->push_back(temp);
                        room_mutex.unlock();
                        client_mutex.unlock();
                    }
                    if(strncmp(cmd.cmd, "join ", 5)==0){
                        client_mutex.lock();
                        bool found=false;
                        bool started=false;
                        unsigned int clientIndex;
                        for(unsigned int i=0; i<clients->size(); i++){
                            if(clients->at(i).fd==cmd.sender){
                                clientIndex=i;
                                if(clients->at(i).roomId!=-1)found=true;
                                break;
                            }
                        }
                        if(found){
                            client_mutex.unlock();
                            write(cmd.sender, "Already in a room.\n", 20);
                            continue;
                        }
                        int roomId=getArgument(cmd.cmd, 5);
                        if(roomId==-1){
                            client_mutex.unlock();
                            write(cmd.sender, "Invalid argument.", 18);
                            continue;
                        }
                        room_mutex.lock();
                        found=false;
                        unsigned int roomIndex;
                        int free=0;
                        for(unsigned int i=0; i<rooms->size(); i++){
                            if(rooms->at(i).id==roomId){
                                roomIndex=i;
                                found=true;
                                if(rooms->at(i).state==INPROGRESS)started=true;
                                for(int j=0; j<8; j++){
                                    if(rooms->at(i).players[j].fd==-1)free++;
                                }
                                break;
                            }
                        }
                        if(!found){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        if(free==0){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" is full.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        if(started){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" has already started playing. "+
                                "Consider spectating instead.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        clients->at(clientIndex).roomId=roomId;
                        for(int j=0; j<8; j++){
                            if(rooms->at(roomIndex).players[j].fd==-1){
                                rooms->at(roomIndex).players[j]=clients->at(clientIndex);
                                rooms->at(roomIndex).joinTimes[j]=time(NULL);
                                break;
                            }
                        }
                        room_mutex.unlock();
                        client_mutex.unlock();
                        
                    }
                    if(strncmp(cmd.cmd, "spectate ", 9)==0){
                        client_mutex.lock();
                        bool found=false;
                        unsigned int clientIndex;
                        for(unsigned int i=0; i<clients->size(); i++){
                            if(clients->at(i).fd==cmd.sender){
                                clientIndex=i;
                                if(clients->at(i).roomId!=-1)found=true;
                                break;
                            }
                        }
                        if(found){
                            client_mutex.unlock();
                            write(cmd.sender, "Already in a room.\n", 20);
                            continue;
                        }
                        int roomId=getArgument(cmd.cmd, 9);
                        if(roomId==-1){
                            client_mutex.unlock();
                            write(cmd.sender, "Invalid argument.", 18);
                            continue;
                        }
                        room_mutex.lock();
                        found=false;
                        unsigned int roomIndex;
                        for(unsigned int i=0; i<rooms->size(); i++){
                            if(rooms->at(i).id==roomId){
                                roomIndex=i;
                                found=true;
                                break;
                            }
                        }
                        if(!found){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        clients->at(clientIndex).roomId=roomId;
                        rooms->at(roomIndex).spectatorCount++;
                        if(rooms->at(roomIndex).state==INPROGRESS){
                            std::string qName="TotemRoom"+std::to_string(roomId);
                            message_queue roomQ(open_only, qName.c_str());
                            roomQ.send(&cmd, sizeof(cmd), 1);
                        }
                        room_mutex.unlock();
                        client_mutex.unlock();
                    }
                    if(strncmp(cmd.cmd, "start", 5)==0){
                        client_mutex.lock();
                        bool found=false;
                        int roomId;
                        for(unsigned int i=0; i<clients->size(); i++){
                            if(clients->at(i).fd==cmd.sender){
                                roomId=clients->at(i).roomId;
                                if(roomId!=-1)found=true;
                                break;
                            }
                        }
                        if(!found){
                            client_mutex.unlock();
                            write(cmd.sender, "Not in a room.\n", 16);
                            continue;
                        }
                        room_mutex.lock();
                        found=false;
                        unsigned int roomIndex;
                        bool allowed=false;
                        unsigned int pCount=0;
                        bool idle=false;
                        for(unsigned int i=0; i<rooms->size(); i++){
                            if(rooms->at(i).id==roomId){
                                roomIndex=i;
                                found=true;
                                if(rooms->at(i).state==IDLE)idle=true;
                                int j=0;
                                for(int k=0; k<8; k++){
                                    if(rooms->at(i).players[k].fd!=-1)pCount++;
                                    if(rooms->at(i).players[k].fd==cmd.sender)j=k;
                                }
                                time_t min=time(NULL);
                                int minInd = -1;
                                for(int k=0; k<8; k++){
                                    if (rooms->at(i).players[k].fd == -1)
                                        continue;

                                    if (rooms->at(i).joinTimes[k] < min) {
                                        min = rooms->at(i).joinTimes[k];
                                        minInd = k;
                                    }
                                }
                                if((j==minInd)&&(pCount>=2))allowed=true;
                                break;
                            }
                        }
                        if(!found){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        if(!allowed){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="You don't have permission to start a game in room "+
                                std::to_string(roomId)+" or there are less than 2 players.\n";
                            write(cmd.sender, err.c_str(), err.length());
                            continue;
                        }
                        if(!idle){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            continue;
                        }
                        rooms->at(roomIndex).state=INPROGRESS;
                        std::string qName="TotemRoom"+std::to_string(roomId);
                        message_queue::remove(qName.c_str());
                        std::thread(gameRunner, roomId).detach();

                        // retry-loop: czekamy aż gameRunner utworzy kolejkę
                        message_queue* roomQptr = nullptr;
                        for (int tries = 0; tries < 20; tries++) {
                            try {
                                roomQptr = new message_queue(open_only, qName.c_str());
                                break;
                            } catch (...) {
                                usleep(50000); // 50 ms
                            }
                        }
                        if (!roomQptr) {
                            printf("[ERROR] Could not open room queue after start!\n");
                        } else {
                            message r;
                            r.sender = -1;
                            strcpy(r.cmd, "refresh");
                            roomQptr->send(&r, sizeof(r), 1);
                            delete roomQptr;
                        }

                        room_mutex.unlock();
                        client_mutex.unlock();
                    }
                }
                else{
                    //przekaż do kolejki pokoju, w którym znajduje się nadawca
                    client_mutex.lock();
                    bool found=false;
                    int roomId;
                    for(unsigned int i=0; i<clients->size(); i++){
                        if(clients->at(i).fd==cmd.sender){
                            roomId=clients->at(i).roomId;
                            if(roomId!=-1)found=true;
                            break;
                        }
                    }
                    if(!found){
                        client_mutex.unlock();
                        write(cmd.sender, "Not in a room.\n", 16);
                        continue;
                    }
                    room_mutex.lock();
                    found=false;
                    unsigned int roomIndex;
                    roomState state;
                    for(unsigned int i=0; i<rooms->size(); i++){
                        if(rooms->at(i).id==roomId){
                            roomIndex=i;
                            found=true;
                            state=rooms->at(i).state;
                            break;
                        }
                    }
                    if(!found){
                        room_mutex.unlock();
                        client_mutex.unlock();
                        std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
                        write(cmd.sender, err.c_str(), err.length());
                        continue;
                    }
                    std::string qName="TotemRoom"+std::to_string(roomId);
                    message_queue roomQ(open_only, qName.c_str());
                    if((strncmp(cmd.cmd, "refresh", 7)==0)&&(state==IDLE)){
                        room temp=rooms->at(roomIndex);
                        std::string roomDesc=describeRoom(&temp);
                        write(cmd.sender, roomDesc.c_str(), roomDesc.length());
                    }
                    else roomQ.send(&cmd, sizeof(cmd), 1);
                    room_mutex.unlock();
                    client_mutex.unlock();
                }
            }
            else{
                client_mutex.lock();
                printf("Current clients:\n");
                for(unsigned int i=0; i<clients->size(); i++){
                    const char* nick=(clients->at(i).nick!="")? clients->at(i).nick.c_str() : "unnamed";
                    printf("%d - %s;\n", clients->at(i).fd, nick);
                }
                client_mutex.unlock();
                usleep(50000);
            }
        }
    }

    sleep(2);
    for(unsigned int i=0; i<clients->size(); i++){
        shutdown(clients->at(i).fd, SHUT_RDWR);
        close(clients->at(i).fd);
    }
    segment.destroy<clientVector>("clients");
    segment.destroy<roomVector>("rooms");
    message_queue::remove(MQ_NAME);
    freeaddrinfo(resolved);
    shutdown(sock, SHUT_RDWR);
    close(sock);

    return 0;

}