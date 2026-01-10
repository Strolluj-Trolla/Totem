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
#include <iostream>
#include <boost/interprocess/managed_shared_memory.hpp>
#include <boost/interprocess/allocators/allocator.hpp>
#include <boost/interprocess/sync/named_mutex.hpp>
#include <boost/interprocess/ipc/message_queue.hpp>
#include <boost/container/vector.hpp>
#include <boost/container/string.hpp>
#include <signal.h>
#include <ctime>

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

void handleComms(int clientSocket, sockaddr_in clientAddress){
    managed_shared_memory segment(open_only, shm);
    clientVector* clients = segment.find<clientVector>("clients").first;
    roomVector* rooms     = segment.find<roomVector>("rooms").first;
    named_mutex client_mutex(open_only, clMut);
    named_mutex room_mutex(open_only, rmMut);
    message_queue mq(open_only, MQ_NAME);

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
                if (!alreadySet) {
                    std::string nick = line;
                    printf("[DEBUG] Nick attempt from %s: '%s' (len=%zu)\n",
                        inet_ntoa(clientAddress.sin_addr),
                        nick.c_str(),
                        nick.length());
                }

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
                        std::string roomDesc =
                            "Room " + std::to_string(temp.id) + "- players:\n";

                        for (int j = 0; j < 8; j++) {
                            if (temp.players[j].fd == -1) break;
                            roomDesc += std::string(temp.players[j].nick.c_str()) + "\n";
                        }

                        roomDesc += std::to_string(temp.spectatorCount) + " spectators\n";
                        if(temp.state==IDLE)roomDesc+="Waiting to start the match.\n";
                        else roomDesc+="Match in progress.\n";
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

void gameRunner(long roomId){
    return;
}

bool running=true;
void terminator(int signum) {
   printf("Terminating due to signal %d...\n", signum);
   running=false;
   return;
}

int getArgument(const char* cmd, int startIndex){
    std::string arg="";
    for(int i=startIndex; cmd[i]!='\000'; i++){
        arg+=cmd[i];
    }
    try{
        int num=std::stoi(arg);
        return num;
    }
    catch(...){
        return -1;
    }
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
    if((res= getaddrinfo("localhost", argv[1], &hints, &resolved))) {fprintf(stderr, "Getaddrinfo failed: %s\n", gai_strerror(res)); return 1;}

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
                            for(unsigned int i=0; i<rooms->size(); i++){
                                if(rooms->at(i).id==roomId){
                                    for(int j=0; j<8; j++){
                                        if(rooms->at(i).players[j].fd==cmd.sender){
                                            rooms->at(i).players[j].fd=-1;
                                            found=true;
                                            break;
                                        }
                                    }
                                    if(!found)rooms->at(i).spectatorCount--;
                                    break;
                                }
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
                            write(cmd.sender, err.c_str(), err.length()+1);
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
                                for(int j=8; j<8; j++){
                                    if(rooms->at(i).players[j].fd==-1)free++;
                                }
                                break;
                            }
                        }
                        if(!found){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
                            write(cmd.sender, err.c_str(), err.length()+1);
                            continue;
                        }
                        if(free==0){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" is full.\n";
                            write(cmd.sender, err.c_str(), err.length()+1);
                            continue;
                        }
                        if(started){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" has already started playing. "+
                                "Consider spectating instead.\n";
                            write(cmd.sender, err.c_str(), err.length()+1);
                            continue;
                        }
                        clients->at(clientIndex).roomId=roomId;
                        for(int j=8; j<8; j++){
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
                            write(cmd.sender, err.c_str(), err.length()+1);
                            continue;
                        }
                        clients->at(clientIndex).roomId=roomId;
                        rooms->at(roomIndex).spectatorCount++;
                        room_mutex.unlock();
                        client_mutex.unlock();
                    }
                    if(strncmp(cmd.cmd, "start", 5)==0){
                        client_mutex.lock();
                        bool found=false;
                        unsigned int clientIndex;
                        int roomId;
                        for(unsigned int i=0; i<clients->size(); i++){
                            if(clients->at(i).fd==cmd.sender){
                                clientIndex=i;
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
                        for(unsigned int i=0; i<rooms->size(); i++){
                            if(rooms->at(i).id==roomId){
                                roomIndex=i;
                                found=true;
                                int j;
                                for(j=0; j<8; j++){
                                    if(rooms->at(i).players[j].fd==cmd.sender)break;
                                }
                                time_t min=time(NULL);
                                int minInd=0;
                                for(int k=0;k<8;k++){
                                    if(rooms->at(i).joinTimes[k]<min){
                                        min=rooms->at(i).joinTimes[k];
                                        minInd=k;
                                    }
                                }
                                if(j==minInd)allowed=true;
                                break;
                            }
                        }
                        if(!found){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="Room "+std::to_string(roomId)+" doesn't exist.\n";
                            write(cmd.sender, err.c_str(), err.length()+1);
                            continue;
                        }
                        if(!allowed){
                            room_mutex.unlock();
                            client_mutex.unlock();
                            std::string err="You don't have perission to start a game in room "+
                                std::to_string(roomId)+"\n";
                            write(cmd.sender, err.c_str(), err.length()+1);
                            continue;
                        }
                        rooms->at(roomIndex).state=INPROGRESS;
                        std::string qName="TotemRoom"+std::to_string(roomId);
                        message_queue::remove(qName.c_str());
                        message_queue mq(create_only, qName.c_str(), 100, sizeof(message));
                        std::thread(gameRunner, roomId).detach();
                        room_mutex.unlock();
                        client_mutex.unlock();
                    }
                }
                else{
                    //przekaż do kolejki pokoju, w którym znajduje się nadawca
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
                sleep(1);
            }
        }
    }

    sleep(2);
    segment.destroy<clientVector>("clients");
    segment.destroy<roomVector>("rooms");
    message_queue::remove(MQ_NAME);
    freeaddrinfo(resolved);
    shutdown(sock, SHUT_RDWR);
    close(sock);

    return 0;

}