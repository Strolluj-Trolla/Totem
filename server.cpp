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
#include <boost/container/vector.hpp>
#include <boost/container/string.hpp>
#include <signal.h>

#define buff_size 50
#define shm "TotemMem"
#define mut "TotemMut"

using namespace boost::interprocess;

typedef managed_shared_memory::segment_manager segman;
typedef allocator<void, segman> alloc;
typedef allocator<char, segman> charAlloc;
typedef boost::container::basic_string<char, std::char_traits<char>,  charAlloc> string;
struct client{
    using allocator_type=alloc;
    int fd;
    string nick;
    client(int fd_, const char* nick_, const allocator_type& allocate): fd(fd_), nick(nick_, charAlloc(allocate)) {}
};
struct room{
    long id;
    client* players[8];
    time_t joinTimes[8];
    int spectatorCount;
};
typedef struct client client;
typedef struct room room;
typedef allocator<client, segman> clientAlloc;
typedef allocator<room, segman> roomAlloc;
typedef boost::container::vector<client, clientAlloc> clientVector;
typedef boost::container::vector<room, roomAlloc> roomVector;

void handleComms(int clientSocket, sockaddr_in clientAddress){
    managed_shared_memory segment(open_only, shm);
    clientVector* clients=segment.find<clientVector>("clients").first;
    named_mutex client_mutex(open_only, mut);

    char buff[buff_size]="Siemka\n";
    printf("Connection from %s\n", inet_ntoa(clientAddress.sin_addr));
    write(clientSocket, buff, buff_size);
    int readBytes=read(clientSocket, buff, buff_size-1);
    buff[readBytes-1]='\000';
    unsigned int i=0;
    client_mutex.lock();
    while(i<clients->size()){
        if(clients->at(i).fd==clientSocket){
            clients->at(i).nick.assign(buff, readBytes+1);
            break;
        };
        i++;
    }
    client_mutex.unlock();
    sleep(10);
    client_mutex.lock();
    i=0;
    while(i<clients->size()){
        if(clients->at(i).fd==clientSocket)break;
        i++;
    }
    clients->erase(clients->begin()+i);
    client_mutex.unlock();
    shutdown(clientSocket, SHUT_RDWR);
    close(clientSocket);
    return;
}

 bool running=true;
void terminator(int signum) {
   printf("Terminating...\n");
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
    struct mutex_remove{
        mutex_remove() { named_mutex::remove(mut); }
        ~mutex_remove(){ named_mutex::remove(mut); }
    } mutexRemover;
    named_mutex client_mutex(create_only, mut);
    managed_shared_memory segment(create_only, shm, 65536);
    alloc allocInst(segment.get_segment_manager());
    clientVector* clients=segment.construct<clientVector>("clients")(clientAlloc(segment.get_segment_manager()));
    roomVector* rooms=segment.construct<roomVector>("rooms")(roomAlloc(segment.get_segment_manager()));

    while(running){
        sockaddr_in clientAddr;
        socklen_t clientAddrLen=sizeof(clientAddr);
        int clientSock=accept(sock, (sockaddr*)&clientAddr, &clientAddrLen);
        if(clientSock!=-1){
            clients->emplace_back(clientSock, "", allocInst);
            std::thread(handleComms, clientSock, clientAddr).detach();
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
        };
    }

    segment.destroy<clientVector>("clients");
    segment.destroy<roomVector>("rooms");
    freeaddrinfo(resolved);
    shutdown(sock, SHUT_RDWR);
    close(sock);

    return 0;

}